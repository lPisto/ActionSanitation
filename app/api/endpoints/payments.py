import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Header
from fastapi.responses import RedirectResponse
from ipaddress import ip_address
from urllib.parse import urlencode
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from uuid import uuid4
from app.core.config import settings
from app.db.mongodb import get_database
from app.api.deps import get_current_user, get_optional_current_user
from app.models.user import UserInDB
from app.models.order import OrderCreate
from app.services.spire_client import spire_client
from app.services.freightcom_client import quote_freightcom_shipping
from app.services.shipping_rules import calculate_shipping_breakdown, items_total, requires_freightcom_quote, round_money
from app.services.elavon_converge import (
    converge_hpp_configured,
    converge_hpp_payment_url,
    converge_checkout_js_url,
    converge_country_code,
    converge_invoice_number,
    converge_state_code,
    create_converge_hpp_token,
    query_converge_transaction,
)

router = APIRouter()

def normalize_address(address: Optional[str]) -> str:
    return " ".join((address or "").split())

def require_database():
    db = get_database()
    if db is None:
        raise HTTPException(status_code=503, detail="Database connection is not available.")
    return db


async def finalize_confirmed_order_in_spire(order_doc: dict, txn_id: str) -> dict:
    """Create the Spire order from the persisted checkout snapshot.

    This is intentionally idempotent: the orders endpoint returns the existing Spire
    order when the same local/transaction ID was already finalized by the browser.
    """
    if order_doc.get("spire_order_no") not in (None, "", "Pending", "UNKNOWN"):
        return {"status": "already_finalized", "order_id": str(order_doc.get("spire_order_no"))}

    db = require_database()
    customer_email = normalize_address(order_doc.get("customer_email")).lower()
    if not customer_email:
        return {"status": "missing_customer"}

    user_record = await db["users"].find_one({
        "$or": [{"email": customer_email}, {"user.email": customer_email}]
    })
    if not user_record:
        return {"status": "missing_customer"}

    user_data = user_record.get("user") if isinstance(user_record.get("user"), dict) else user_record
    try:
        current_user = UserInDB(**user_data)
        order_request = OrderCreate(
            items=order_doc.get("items") or [],
            shipping_address=order_doc.get("shipping_address") or "",
            shipping_address_details=order_doc.get("shipping_address_details"),
            billing_address=order_doc.get("billing_address"),
            billing_address_details=order_doc.get("billing_address_details"),
            shipping_method=order_doc.get("shipping_method"),
            payment_method="credit_card",
            stripe_payment_intent_id=txn_id,
            local_order_id=order_doc.get("local_order_id") or order_doc.get("id"),
            payment_session_id=order_doc.get("payment_session_id"),
            po_number=order_doc.get("po_number"),
            order_notes=order_doc.get("order_notes"),
            free_tshirt_size=order_doc.get("free_tshirt_size"),
            ship_to_code=order_doc.get("ship_to_code"),
            ship_to_name=order_doc.get("ship_to_name"),
            shipping_cost=order_doc.get("shipping_cost") or 0,
            tax_amount=order_doc.get("tax_amount") or 0,
            total_amount=order_doc.get("total_amount"),
        )
    except Exception as exc:
        await db["orders"].update_one(
            {"_id": order_doc["_id"]},
            {"$set": {
                "status": "Payment Confirmed - ERP Pending",
                "erp_error_message": f"Could not rebuild order: {str(exc)}"[:500],
                "updated_at": datetime.utcnow().isoformat(),
            }},
        )
        return {"status": "invalid_order_snapshot"}

    try:
        # Runtime import avoids a module-import cycle: orders also imports the
        # Converge verification service used above.
        from app.api.endpoints.orders import create_order

        result = await create_order(order_request, current_user)
        return {"status": "finalized", "order": result}
    except Exception as exc:
        detail = getattr(exc, "detail", str(exc))
        await db["orders"].update_one(
            {"_id": order_doc["_id"]},
            {"$set": {
                "status": "Payment Confirmed - ERP Pending",
                "erp_error_message": str(detail)[:500],
                "updated_at": datetime.utcnow().isoformat(),
            }},
        )
        print(f"Confirmed Converge payment could not be finalized in Spire: {detail}")
        return {"status": "erp_pending"}


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
    ship_to_code: Optional[str] = None
    ship_to_name: Optional[str] = None
    billing_address: Optional[str] = None
    billing_address_details: Optional[dict] = None
    po_number: Optional[str] = None
    order_notes: Optional[str] = None
    free_tshirt_size: Optional[str] = None
    shipping_cost: Optional[float] = 0.0
    tax_amount: Optional[float] = 0.0


class ConvergeResultRequest(BaseModel):
    local_order_id: str
    ssl_txn_id: Optional[str] = None
    ssl_result: Optional[str] = None
    ssl_result_message: Optional[str] = None
    ssl_avs_response: Optional[str] = None
    ssl_cvv2_response: Optional[str] = None
    ssl_issuer_response: Optional[str] = None
    error_code: Optional[str] = None


def request_client_ip(request: Request) -> str:
    candidates = []
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        candidates.append(forwarded.split(",", 1)[0].strip())
    candidates.append(request.headers.get("x-real-ip", "").strip())
    if request.client:
        candidates.append(str(request.client.host or "").strip())

    for candidate in candidates:
        if not candidate:
            continue
        try:
            return str(ip_address(candidate))
        except ValueError:
            continue
    return ""

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
                {"converge_invoice_number": intent_id},
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
            {"converge_invoice_number": {"$in": clean_candidates}},
            {"elavon_order_id": {"$in": clean_candidates}}
        ]},
        {"$set": update_data}
    )


async def find_local_order_by_candidates(candidates: list) -> Optional[dict]:
    clean_candidates = [str(candidate) for candidate in candidates if candidate]
    if not clean_candidates:
        return None
    db = require_database()
    return await db["orders"].find_one({
        "$or": [
            {"id": {"$in": clean_candidates}},
            {"local_order_id": {"$in": clean_candidates}},
            {"payment_session_id": {"$in": clean_candidates}},
            {"converge_txn_id": {"$in": clean_candidates}},
            {"converge_invoice_number": {"$in": clean_candidates}},
            {"elavon_order_id": {"$in": clean_candidates}},
        ]
    })

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
    http_request: Request,
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
            # (and Elavon recommends POST); a static frontend route can't handle a POST.
            # The backend verifies the result, finalizes approved orders server-side,
            # and then redirects the browser to the checkout page.
            backend_url = str(http_request.base_url).rstrip("/")
            return_url = f"{backend_url}/api/payments/converge-return?local_order_id={local_order_id}"
            cardholder_ip = request_client_ip(http_request)
            token = await create_converge_hpp_token(
                amount=amount,
                local_order_id=local_order_id,
                customer_email=request.customer_email,
                billing_address_details=request.billing_address_details,
                shipping_address_details=request.shipping_address_details,
                frontend_success_url=return_url,
                frontend_cancel_url=return_url,
                cardholder_ip=cardholder_ip,
            )
            billing = request.billing_address_details or {}
            shipping = request.shipping_address_details or {}
            print(
                f"[CONVERGE-TOKEN] order={local_order_id} amount={amount:.2f} "
                f"token_obtained={bool(token)} "
                f"billing_country={converge_country_code(billing.get('country')) or '-'} "
                f"billing_state={converge_state_code(billing.get('prov_state') or billing.get('state'), billing.get('country')) or '-'} "
                f"shipping_country={converge_country_code(shipping.get('country')) or '-'} "
                f"shipping_state={converge_state_code(shipping.get('prov_state') or shipping.get('state'), shipping.get('country')) or '-'} "
                f"cardholder_ip_present={bool(cardholder_ip)}"
            )

            db = require_database()
            now = datetime.utcnow().isoformat()
            local_order = {
                "id": local_order_id,
                "local_order_id": local_order_id,
                "payment_session_id": local_order_id,
                "converge_invoice_number": converge_invoice_number(local_order_id),
                "elavon_order_id": None,
                "elavon_order_href": None,
                "elavon_payment_session_href": None,
                "customer_email": request.customer_email,
                "total_amount": amount,
                "items": request.items,
                "shipping_address": normalize_address(request.shipping_address) or None,
                "shipping_address_details": request.shipping_address_details,
                "shipping_method": request.shipping_method,
                "ship_to_code": (request.ship_to_code or "").strip() or None,
                "ship_to_name": (request.ship_to_name or "").strip() or None,
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
                "elavonCheckoutJsUrl": converge_checkout_js_url(),
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

@router.post("/converge-result")
async def reconcile_converge_result(
    report: ConvergeResultRequest,
    current_user: UserInDB = Depends(get_current_user),
):
    """Record the Lightbox result and verify it server-to-server when possible.

    Browser callbacks are useful diagnostics but are never trusted as proof of
    approval. Only txnquery can promote an order to Payment Confirmed.
    """
    db = require_database()
    local_order_id = normalize_address(report.local_order_id)
    order = await db["orders"].find_one({
        "$or": [
            {"id": local_order_id},
            {"local_order_id": local_order_id},
            {"payment_session_id": local_order_id},
            {"converge_invoice_number": local_order_id},
        ]
    })
    if not order or order.get("provider") != "Elavon Converge HPP":
        raise HTTPException(status_code=404, detail="Payment attempt not found.")

    order_email = normalize_address(order.get("customer_email")).lower()
    user_email = normalize_address(current_user.email).lower()
    if order_email and order_email != user_email:
        raise HTTPException(status_code=404, detail="Payment attempt not found.")

    txn_id = normalize_address(report.ssl_txn_id)
    # Full trace of what the browser reported vs what the server re-query returns.
    print("=" * 70)
    print(f"[CONVERGE-RESULT] order={local_order_id} txn={txn_id or '-'} BEGIN")
    print(f"[CONVERGE-RESULT] browser_report={report.model_dump()}")
    verified = await query_converge_transaction(txn_id) if txn_id else None
    verified_state = str((verified or {}).get("status") or "").upper()
    if verified is None:
        print(f"[CONVERGE-RESULT] server txnquery returned NOTHING "
              f"(no txn_id or Converge did not answer) txn={txn_id or '-'}")
    else:
        for key in sorted(verified.keys()):
            print(f"[CONVERGE-RESULT]   {key} = {verified[key]!r}")

    diagnostics = {
        "ssl_result": normalize_address((verified or {}).get("ssl_result") or report.ssl_result)[:20],
        "ssl_result_message": normalize_address(
            (verified or {}).get("ssl_result_message") or report.ssl_result_message
        )[:255],
        "ssl_avs_response": normalize_address(
            (verified or {}).get("ssl_avs_response") or report.ssl_avs_response
        )[:20],
        "ssl_cvv2_response": normalize_address(
            (verified or {}).get("ssl_cvv2_response") or report.ssl_cvv2_response
        )[:20],
        "ssl_issuer_response": normalize_address(
            (verified or {}).get("ssl_issuer_response") or report.ssl_issuer_response
        )[:50],
        "error_code": normalize_address(report.error_code)[:50],
        "reported_at": datetime.utcnow().isoformat(),
        "server_verified": bool(verified),
    }
    diagnostics = {key: value for key, value in diagnostics.items() if value not in (None, "")}
    # Surfaced in the live logs so a test/real decline is visible without querying Mongo.
    print(
        f"[CONVERGE-RESULT] order={local_order_id} txn={txn_id or '-'} "
        f"verified_state={verified_state or 'NONE'} server_verified={bool(verified)} "
        f"diagnostics={diagnostics}"
    )
    print(f"[CONVERGE-RESULT] order={local_order_id} txn={txn_id or '-'} END")
    update = {
        "payment_result": diagnostics,
        "updated_at": datetime.utcnow().isoformat(),
    }

    if verified_state == "APPROVED":
        verified_txn_id = normalize_address((verified or {}).get("ssl_txn_id") or txn_id)
        update.update({"status": "Payment Confirmed", "converge_txn_id": verified_txn_id})
        await db["orders"].update_one({"_id": order["_id"]}, {"$set": update})
        erp_result = await finalize_confirmed_order_in_spire(order, verified_txn_id)
        return {
            "status": "APPROVED",
            "ssl_txn_id": verified_txn_id,
            "ssl_result_message": diagnostics.get("ssl_result_message", "APPROVAL"),
            "erp_status": erp_result.get("status"),
        }

    if verified_state == "DECLINED":
        message = diagnostics.get("ssl_result_message") or "Transaction declined"
        update.update({"status": "Payment Failed", "error_message": message})
        await db["orders"].update_one({"_id": order["_id"]}, {"$set": update})
        return {"status": "DECLINED", "ssl_result_message": message}

    # A decline callback is still useful for support, but without txnquery it is
    # deliberately not treated as a settled/approved charge.
    callback_result = normalize_address(report.ssl_result)
    if callback_result and callback_result != "0":
        update["status"] = "Payment Declined (Unverified)"
    await db["orders"].update_one({"_id": order["_id"]}, {"$set": update})
    return {
        # Do not tell the shopper to retry until the server has confirmed the decline;
        # retrying an ambiguous result is how duplicate authorizations happen.
        "status": "PENDING",
        "ssl_result_message": diagnostics.get("ssl_result_message", ""),
    }


@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Webhook adaptado para Converge. 
    Converge envía una notificación HTTP POST si se configura en el Merchant Panel.
    """
    # Converge suele enviar los datos como Form Data en lugar de JSON
    form_data = await request.form()
    txn_id = form_data.get("ssl_txn_id")
    if not txn_id:
        return {"status": "ignored"}

    # Export-script payloads are not proof of payment on their own. Re-query Converge
    # through the whitelisted server before changing the local payment state.
    verified = await query_converge_transaction(str(txn_id))
    if not verified:
        return {"status": "pending_verification"}

    result = str(verified.get("ssl_result") or "")
    verified_txn_id = verified.get("ssl_txn_id") or txn_id
    result_details = {
        "payment_result": {
            "ssl_result": result,
            "ssl_result_message": verified.get("ssl_result_message") or form_data.get("ssl_result_message"),
            "ssl_avs_response": verified.get("ssl_avs_response") or form_data.get("ssl_avs_response"),
            "ssl_cvv2_response": verified.get("ssl_cvv2_response") or form_data.get("ssl_cvv2_response"),
            "ssl_issuer_response": verified.get("ssl_issuer_response") or form_data.get("ssl_issuer_response"),
            "reported_at": datetime.utcnow().isoformat(),
            "server_verified": True,
        }
    }

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
        await update_local_order_status_by_candidates(
            candidates,
            "Payment Confirmed",
            {"converge_txn_id": verified_txn_id, **result_details},
        )
        confirmed_order = await find_local_order_by_candidates([*candidates, verified_txn_id])
        if confirmed_order:
            await finalize_confirmed_order_in_spire(confirmed_order, str(verified_txn_id))
        intent_id = verified_txn_id
        print(f"💰 ¡Webhook: Pago exitoso procesado para Intent ID: {intent_id}")
    else:
        error_message = verified.get("ssl_result_message") or form_data.get("ssl_result_message")
        await update_local_order_status_by_candidates(
            [
                txn_id,
                form_data.get("ssl_token"),
                form_data.get("ssl_invoice_number"),
                form_data.get("ssl_order_id"),
                form_data.get("local_order_id"),
            ],
            "Payment Failed",
            {"error_message": error_message, "converge_txn_id": verified_txn_id, **result_details}
        )

    return {"status": "success"}


@router.api_route("/converge-return", methods=["GET", "POST"])
async def converge_return(request: Request):
    """Return endpoint for the Converge hosted page. Accepts BOTH GET and POST so the
    result is captured no matter how Converge sends it. The return does not determine
    whether Converge approves a card; it only reconciles the completed transaction and
    makes sure an approval reaches Spire before redirecting the browser."""
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

    # The browser-facing result is useful, but txnquery is authoritative. This also
    # covers the rare case where the hosted page reports a stale/ambiguous result after
    # the processor has already recorded an approval.
    if txn_id:
        verified = await query_converge_transaction(txn_id)
        verified_state = str((verified or {}).get("status") or "").upper()
        if verified_state == "APPROVED":
            ssl_result = "0"
            message = (verified or {}).get("ssl_result_message") or "APPROVAL"
            verified_txn_id = (verified or {}).get("ssl_txn_id") or txn_id
            candidates = [local_order_id, params.get("ssl_invoice_number"), verified_txn_id]
            await update_local_order_status_by_candidates(
                candidates,
                "Payment Confirmed",
                {"converge_txn_id": verified_txn_id},
            )
            confirmed_order = await find_local_order_by_candidates(candidates)
            if confirmed_order:
                await finalize_confirmed_order_in_spire(confirmed_order, str(verified_txn_id))
        elif verified_state == "DECLINED":
            ssl_result = str((verified or {}).get("ssl_result") or ssl_result or "1")
            message = (verified or {}).get("ssl_result_message") or message or "Transaction declined"
            await update_local_order_status_by_candidates(
                [local_order_id, params.get("ssl_invoice_number"), txn_id],
                "Payment Failed",
                {"converge_txn_id": txn_id, "error_message": message},
            )

    frontend_url = settings.FRONTEND_URLS.split(",")[0].strip().rstrip("/")
    query = urlencode({
        "ssl_txn_id": txn_id,
        "ssl_result": ssl_result,
        "ssl_result_message": message,
        "local_order_id": local_order_id,
    })
    # CheckoutSuccess handles both approved (ssl_result == "0") and declined/cancelled.
    return RedirectResponse(url=f"{frontend_url}/checkout/success?{query}", status_code=303)
