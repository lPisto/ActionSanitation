import httpx
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import uuid4
from app.core.config import settings
from app.db.mongodb import get_database

router = APIRouter()

def normalize_address(address: Optional[str]) -> str:
    return " ".join((address or "").split())

def require_database():
    db = get_database()
    if db is None:
        raise HTTPException(status_code=503, detail="Database connection is not available.")
    return db

class PaymentIntentRequest(BaseModel):
    amount: float  # Converge prefiere decimales
    currency: str = "USD"
    order_id: Optional[str] = None
    customer_email: Optional[str] = None
    items: Optional[List[dict]] = []
    shipping_address: Optional[str] = None
    shipping_method: Optional[str] = None
    billing_address: Optional[str] = None
    po_number: Optional[str] = None
    order_notes: Optional[str] = None

async def update_local_order_status(intent_id: str, new_status: str, error_msg: str = None):
    try:
        db = get_database()
        update_data = {"status": new_status}
        if error_msg:
            update_data["error_message"] = error_msg
            
        await db["orders"].update_one(
            {"$or": [
                {"id": intent_id},
                {"local_order_id": intent_id},
                {"payment_session_id": intent_id},
                {"converge_txn_id": intent_id},
                {"elavon_order_id": intent_id}
            ]},
            {"$set": update_data}
        )
    except Exception as e:
        print(f"Error updating local order in MongoDB: {e}")

async def update_local_order_status_by_candidates(candidates: list, new_status: str, extra_data: dict = None):
    clean_candidates = [str(candidate) for candidate in candidates if candidate]
    if not clean_candidates:
        return

    db = get_database()
    update_data = {"status": new_status}
    if extra_data:
        update_data.update(extra_data)

    await db["orders"].update_one(
        {"$or": [
            {"id": {"$in": clean_candidates}},
            {"local_order_id": {"$in": clean_candidates}},
            {"payment_session_id": {"$in": clean_candidates}},
            {"converge_txn_id": {"$in": clean_candidates}},
            {"elavon_order_id": {"$in": clean_candidates}}
        ]},
        {"$set": update_data}
    )

@router.post("/create-payment-intent")
async def create_payment_intent(request: PaymentIntentRequest):
    try:
        base_url = settings.CONVERGE_URL.rstrip("/")

        auth = (
            settings.ELAVON_MERCHANT_ALIAS,
            settings.ELAVON_SECRET_KEY
        )

        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        local_order_id = request.order_id or f"web_{uuid4().hex[:12]}"

        order_payload = {
            "total": {
                "amount": f"{request.amount:.2f}",
                "currencyCode": request.currency.upper()
            },
            "description": f"Order {local_order_id}",
            "items": [],
            "orderReference": local_order_id
        }

        if request.customer_email:
            order_payload["shopperEmailAddress"] = request.customer_email

        async with httpx.AsyncClient(timeout=30) as client:
            order_resp = await client.post(
                f"{base_url}/orders",
                json=order_payload,
                auth=auth,
                headers=headers
            )

        print("ELAVON ORDER STATUS:", order_resp.status_code)
        print("ELAVON ORDER RESPONSE:", order_resp.text)

        if order_resp.status_code not in [200, 201]:
            raise HTTPException(
                status_code=order_resp.status_code,
                detail=f"Elavon Order Error: {order_resp.text}"
            )

        order_data = order_resp.json()

        order_id = order_data.get("id")
        order_href = order_data.get("href")

        if not order_id or not order_href:
            raise HTTPException(
                status_code=400,
                detail=f"Could not create Elavon order: {order_data}"
            )

        frontend_url = settings.FRONTEND_URLS.split(",")[0].strip().rstrip("/")

        payment_session_payload = {
            "order": order_href,
            "returnUrl": f"{frontend_url}/checkout/success?local_order_id={local_order_id}",
            "cancelUrl": f"{frontend_url}/checkout/cancel?local_order_id={local_order_id}",
            "doCreateTransaction": True,
            "doCapture": True
        }

        async with httpx.AsyncClient(timeout=30) as client:
            session_resp = await client.post(
                f"{base_url}/payment-sessions",
                json=payment_session_payload,
                auth=auth,
                headers=headers
            )

        print("ELAVON SESSION STATUS:", session_resp.status_code)
        print("ELAVON SESSION RESPONSE:", session_resp.text)

        if session_resp.status_code not in [200, 201]:
            raise HTTPException(
                status_code=session_resp.status_code,
                detail=f"Elavon Payment Session Error: {session_resp.text}"
            )

        session_data = session_resp.json()

        session_id = session_data.get("id")
        session_href = session_data.get("href")
        session_url = session_data.get("url")

        if not session_id:
            raise HTTPException(
                status_code=400,
                detail=f"Could not generate payment session: {session_data}"
            )

        db = require_database()
        now = datetime.utcnow().isoformat()

        local_order = {
            "id": local_order_id,
            "local_order_id": local_order_id,
            "payment_session_id": session_id,
            "elavon_order_id": order_id,
            "elavon_order_href": order_href,
            "elavon_payment_session_href": session_href,
            "spire_order_no": "Pending",
            "customer_email": request.customer_email,
            "total_amount": request.amount,
            "items": request.items,
            "shipping_address": normalize_address(request.shipping_address) or None,
            "shipping_method": request.shipping_method,
            "billing_address": normalize_address(request.billing_address) or None,
            "po_number": (request.po_number or "").strip() or None,
            "order_notes": (request.order_notes or "").strip() or None,
            "status": "Payment Pending",
            "provider": "Elavon",
            "updated_at": now
        }

        result = await db["orders"].update_one(
            {"id": local_order_id},
            {
                "$set": local_order,
                "$setOnInsert": {"created_at": now}
            },
            upsert=True
        )
        print(
            "MONGO ORDER UPSERT:",
            {
                "local_order_id": local_order_id,
                "matched_count": result.matched_count,
                "modified_count": result.modified_count,
                "upserted_id": str(result.upserted_id) if result.upserted_id else None,
            }
        )

        return {
            "paymentSessionId": session_id,
            "localOrderId": local_order_id,
            "paymentSessionUrl": session_url,
            "elavonOrderId": order_id,
            "elavonPaymentSessionHref": session_href
        }

    except HTTPException:
        raise

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"An unexpected error occurred: {str(e)}"
        )

@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Webhook adaptado para Converge. 
    Converge envía una notificación HTTP POST si se configura en el Merchant Panel.
    """
    # Converge suele enviar los datos como Form Data en lugar de JSON
    form_data = await request.form()
    txn_id = form_data.get("ssl_txn_id")
    result = form_data.get("ssl_result")
    
    if not txn_id:
        return {"status": "ignored"}

    # ssl_result "0" significa aprobado en Converge
    if result == "0":
        # En Converge el ID que guardamos inicialmente era el token, 
        # pero ahora recibimos el txn_id definitivo. Actualizamos la referencia.
        token = form_data.get("ssl_token")
        candidates = [
            token,
            txn_id,
            form_data.get("ssl_invoice_number"),
            form_data.get("ssl_order_id"),
            form_data.get("ssl_customer_code"),
            form_data.get("orderReference"),
            form_data.get("local_order_id"),
        ]

        # El pago fue confirmado; la orden queda Paid cuando también se crea/actualiza en Spire.
        await update_local_order_status_by_candidates(candidates, "Payment Confirmed", {"converge_txn_id": txn_id})
        intent_id = txn_id
        print(f"💰 ¡Webhook: Pago exitoso procesado para Intent ID: {intent_id}")
    else:
        error_message = form_data.get("ssl_result_message")
        await update_local_order_status_by_candidates(
            [
                txn_id,
                form_data.get("ssl_token"),
                form_data.get("ssl_invoice_number"),
                form_data.get("ssl_order_id"),
                form_data.get("local_order_id"),
            ],
            "Payment Failed",
            {"error_message": error_message}
        )

    return {"status": "success"}
