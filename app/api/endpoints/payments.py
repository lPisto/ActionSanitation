import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from urllib.parse import urlencode
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import uuid4
from app.core.config import settings
from app.db.mongodb import get_database
from app.api.deps import get_optional_current_user
from app.models.user import UserInDB
from app.services.spire_client import spire_client
from app.services.freightcom_client import quote_freightcom_shipping
from app.services.shipping_rules import calculate_shipping_breakdown, items_total, requires_freightcom_quote, round_money
from app.services.elavon_converge import (
    converge_hpp_configured,
    converge_hpp_payment_url,
    create_converge_hpp_token,
)

router = APIRouter()

def normalize_address(address: Optional[str]) -> str:
    return " ".join((address or "").split())

def require_database():
    db = get_database()
    if db is None:
        raise HTTPException(status_code=503, detail="Database connection is not available.")
    return db


def first_non_empty(*values) -> str:
    for value in values:
        normalized = normalize_address(value)
        if normalized:
            return normalized
    return ""

class PaymentIntentRequest(BaseModel):
    amount: float  # Converge prefiere decimales
    currency: str = "USD"
    order_id: Optional[str] = None
    customer_email: Optional[str] = None
    items: Optional[List[dict]] = []
    shipping_address: Optional[str] = None
    shipping_address_details: Optional[dict] = None
    shipping_method: Optional[str] = None
    billing_address: Optional[str] = None
    billing_address_details: Optional[dict] = None
    po_number: Optional[str] = None
    order_notes: Optional[str] = None
    free_tshirt_size: Optional[str] = None
    shipping_cost: Optional[float] = 0.0
    tax_amount: Optional[float] = 0.0

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

async def user_has_free_delivery(current_user: Optional[UserInDB]) -> bool:
    shipping_settings = await get_customer_shipping_settings(current_user)
    return shipping_settings["free_delivery"]

async def get_customer_shipping_settings(current_user: Optional[UserInDB]) -> dict:
    if not current_user:
        return {"free_delivery": False, "ship_code": ""}

    free_delivery = bool(getattr(current_user, "free_delivery", False))
    ship_code = ""
    try:
        customer = await spire_client.get_customer(current_user.spire_customer_no)
        free_delivery = free_delivery or spire_client.customer_has_free_delivery(customer)
        ship_code = spire_client.customer_ship_code(customer)
    except Exception as e:
        print(f"Could not check shipping settings for {current_user.spire_customer_no}: {e}")
    return {"free_delivery": free_delivery, "ship_code": ship_code}

@router.post("/create-payment-intent")
async def create_payment_intent(
    request: PaymentIntentRequest,
    current_user: Optional[UserInDB] = Depends(get_optional_current_user),
):
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
        subtotal = items_total(request.items or [])
        shipping_settings = await get_customer_shipping_settings(current_user)
        free_delivery = shipping_settings["free_delivery"]
        shipping_details = request.shipping_address_details or {}
        shipping_postal_code = first_non_empty(
            shipping_details.get("postal_code"),
            shipping_details.get("postalCode"),
            shipping_details.get("zip"),
            getattr(current_user, "zip", ""),
        )
        freightcom_shipping_cost = None
        if requires_freightcom_quote(
            shipping_postal_code,
            request.shipping_method,
            free_delivery=free_delivery,
            ship_code=shipping_settings["ship_code"],
        ):
            freightcom_shipping_cost = await quote_freightcom_shipping(
                shipping_postal_code,
                request.items or [],
                subtotal,
            )
        shipping_breakdown = calculate_shipping_breakdown(
            subtotal,
            request.items or [],
            request.shipping_method,
            free_delivery=free_delivery,
            ship_code=shipping_settings["ship_code"],
            postal_code=shipping_postal_code,
            freightcom_shipping_cost=freightcom_shipping_cost,
        )
        shipping_cost = shipping_breakdown["shipping_cost"]
        tax_amount = round_money(request.tax_amount)
        amount = round_money(request.amount)
        if subtotal > 0:
            expected_amount = round_money(subtotal + shipping_cost + tax_amount)
            if free_delivery or str(request.shipping_method or "").lower() == "pickup":
                amount = expected_amount
            elif amount < expected_amount:
                amount = expected_amount

        frontend_url = settings.FRONTEND_URLS.split(",")[0].strip().rstrip("/")

        if converge_hpp_configured():
            # Point Converge's receipt/error URLs at a BACKEND endpoint that accepts
            # both GET and POST. Converge's hosted page may return the result via POST
            # (and Elavon recommends POST); a static frontend route can't handle a POST,
            # which made Converge report "declined" even after the bank authorized. The
            # backend endpoint normalizes the result and redirects to the checkout page.
            backend_url = str(request.base_url).rstrip("/")
            return_url = f"{backend_url}/api/payments/converge-return?local_order_id={local_order_id}"
            token = await create_converge_hpp_token(
                amount=amount,
                local_order_id=local_order_id,
                customer_email=request.customer_email,
                billing_address_details=request.billing_address_details,
                frontend_success_url=return_url,
                frontend_cancel_url=return_url,
            )

            db = require_database()
            now = datetime.utcnow().isoformat()
            local_order = {
                "id": local_order_id,
                "local_order_id": local_order_id,
                "payment_session_id": local_order_id,
                "elavon_order_id": None,
                "elavon_order_href": None,
                "elavon_payment_session_href": None,
                "customer_email": request.customer_email,
                "total_amount": amount,
                "items": request.items,
                "shipping_address": normalize_address(request.shipping_address) or None,
                "shipping_address_details": request.shipping_address_details,
                "shipping_method": request.shipping_method,
                "billing_address": normalize_address(request.billing_address) or None,
                "billing_address_details": request.billing_address_details,
                "po_number": (request.po_number or "").strip() or None,
                "order_notes": (request.order_notes or "").strip() or None,
                "free_tshirt_size": (request.free_tshirt_size or "").strip() or None,
                "shipping_cost": shipping_cost,
                "tax_amount": tax_amount,
                "status": "Payment Pending",
                "provider": "Elavon Converge HPP",
                "updated_at": now,
            }

            await db["orders"].update_one(
                {"id": local_order_id},
                {
                    "$set": local_order,
                    "$setOnInsert": {"created_at": now, "spire_order_no": None},
                },
                upsert=True,
            )

            return {
                "paymentSessionId": local_order_id,
                "localOrderId": local_order_id,
                "paymentSessionUrl": None,
                "elavonHppUrl": converge_hpp_payment_url(),
                "elavonHppFields": {"ssl_txn_auth_token": token},
            }

        order_payload = {
            "total": {
                "amount": f"{amount:.2f}",
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
            "customer_email": request.customer_email,
            "total_amount": amount,
            "items": request.items,
            "shipping_address": normalize_address(request.shipping_address) or None,
            "shipping_address_details": request.shipping_address_details,
            "shipping_method": request.shipping_method,
            "billing_address": normalize_address(request.billing_address) or None,
            "billing_address_details": request.billing_address_details,
            "po_number": (request.po_number or "").strip() or None,
            "order_notes": (request.order_notes or "").strip() or None,
            "free_tshirt_size": (request.free_tshirt_size or "").strip() or None,
            "shipping_cost": shipping_cost,
            "tax_amount": tax_amount,
            "status": "Payment Pending",
            "provider": "Elavon",
            "updated_at": now
        }

        result = await db["orders"].update_one(
            {"id": local_order_id},
            {
                "$set": local_order,
                "$setOnInsert": {"created_at": now, "spire_order_no": None}
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

    except RuntimeError as e:
        raise HTTPException(
            status_code=502,
            detail=str(e)
        )

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


@router.api_route("/converge-return", methods=["GET", "POST"])
async def converge_return(request: Request):
    """Return endpoint for the Converge hosted page. Accepts BOTH GET and POST so the
    result is captured no matter how Converge sends it (Elavon recommends POST). A static
    frontend route can't process a POST, which made Converge report "declined" even after
    the bank authorized. This reads the result and redirects the browser to the checkout
    page, where the order is finalized (or the decline message is shown)."""
    params = dict(request.query_params)
    if request.method == "POST":
        try:
            form_data = await request.form()
            for key, value in form_data.items():
                params[key] = value
        except Exception:
            pass

    ssl_result = str(params.get("ssl_result") or "")
    txn_id = params.get("ssl_txn_id") or ""
    message = params.get("ssl_result_message") or ""
    local_order_id = params.get("local_order_id") or params.get("ssl_invoice_number") or ""

    frontend_url = settings.FRONTEND_URLS.split(",")[0].strip().rstrip("/")
    query = urlencode({
        "ssl_txn_id": txn_id,
        "ssl_result": ssl_result,
        "ssl_result_message": message,
        "local_order_id": local_order_id,
    })
    # CheckoutSuccess handles both approved (ssl_result == "0") and declined/cancelled.
    return RedirectResponse(url=f"{frontend_url}/checkout/success?{query}", status_code=303)
