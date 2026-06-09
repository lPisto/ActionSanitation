import httpx
from fastapi import APIRouter, HTTPException, Request, Header
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from app.core.config import settings
from app.db.mongodb import get_database

router = APIRouter()

class PaymentIntentRequest(BaseModel):
    amount: float  # Converge prefiere decimales
    currency: str = "USD"
    order_id: Optional[str] = None
    customer_email: Optional[str] = None
    items: Optional[List[dict]] = []

async def update_local_order_status(intent_id: str, new_status: str, error_msg: str = None):
    try:
        db = get_database()
        update_data = {"status": new_status}
        if error_msg:
            update_data["error_message"] = error_msg
            
        await db["orders"].update_one(
            {"id": intent_id},
            {"$set": update_data}
        )
    except Exception as e:
        print(f"Error updating local order in MongoDB: {e}")

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

        order_payload = {
            "total": {
                "amount": f"{request.amount:.2f}",
                "currencyCode": request.currency.upper()
            },
            "description": f"Order {request.order_id or ''}".strip(),
            "items": []
        }

        if request.customer_email:
            order_payload["shopperEmailAddress"] = request.customer_email

        if request.order_id:
            order_payload["orderReference"] = request.order_id

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
            "returnUrl": f"{frontend_url}/checkout/success",
            "cancelUrl": f"{frontend_url}/checkout/cancel",
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

        try:
            db = get_database()

            local_order = {
                "id": session_id,
                "elavon_order_id": order_id,
                "elavon_order_href": order_href,
                "elavon_payment_session_href": session_href,
                "spire_order_no": "Pending",
                "customer_email": request.customer_email,
                "total_amount": request.amount,
                "items": request.items,
                "status": "Pending",
                "provider": "Elavon",
                "created_at": datetime.utcnow().isoformat()
            }

            await db["orders"].insert_one(local_order)

        except Exception as e:
            print(f"Error saving pending order to MongoDB: {e}")

        return {
    "paymentSessionId": session_id,
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
        db = get_database()
        token = form_data.get("ssl_token")
        if token:
            await db["orders"].update_one(
                {"id": token},
                {"$set": {"id": txn_id, "converge_txn_id": txn_id}}
            )
            intent_id = txn_id
        else:
            intent_id = txn_id

        # Sincronizamos la base de datos local (marcar como Pagada)
        await update_local_order_status(intent_id, "Paid")
        print(f"💰 ¡Webhook: Pago exitoso procesado para Intent ID: {intent_id}")
    else:
        error_message = form_data.get("ssl_result_message")
        await update_local_order_status(txn_id, "Payment Failed", error_message)

    return {"status": "success"}
