import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import Any, Dict, List, Optional
from pydantic import BaseModel
from app.services.spire_client import spire_client
from app.api.deps import get_current_user, get_optional_current_user
from app.models.user import UserInDB
from app.models.order import OrderCreate, OrderResponse
from app.core.config import settings
from app.db.mongodb import get_database
from app.services.email_service import send_order_confirmation_email, send_pending_payment_order_notification_email
from app.services.freightcom_client import quote_freightcom_shipping
from app.services.shipping_rules import calculate_shipping_breakdown, requires_freightcom_quote
from app.services.elavon_converge import query_converge_transaction
import json
import httpx
import xml.etree.ElementTree as ET

router = APIRouter()
COD_PAYMENT_METHODS = {"on_account", "cod", "cash_on_delivery"}
DIRECT_ORDER_PAYMENT_METHODS = COD_PAYMENT_METHODS | {"e_transfer"}

COUNTRY_CODES = {
    "canada": "CA",
    "ca": "CA",
    "united states": "US",
    "united states of america": "US",
    "usa": "US",
    "us": "US",
}

def normalize_address(address: Optional[str]) -> str:
    return " ".join((address or "").split())

def parse_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default

def round_money(value) -> float:
    return round(parse_float(value), 2)

def items_total(items: list) -> float:
    total = 0.0
    for item in items or []:
        if isinstance(item, dict):
            quantity = item.get("quantity", 0)
            price = item.get("price", item.get("unit_price", 0))
        else:
            quantity = getattr(item, "quantity", 0)
            price = getattr(item, "price", getattr(item, "unit_price", 0))
        total += parse_float(quantity) * parse_float(price)
    return round(total, 2)

def document_total(order_doc: Optional[dict]) -> float:
    if not order_doc:
        return 0.0

    total = parse_float(order_doc.get("total_amount"))
    if total > 0:
        return total

    return items_total(order_doc.get("items") or [])

def payment_amount(txn_data: dict, existing_order: Optional[dict]) -> float:
    raw_amount = (txn_data or {}).get("amount", 0.0)
    if isinstance(raw_amount, dict):
        raw_amount = raw_amount.get("amount", 0.0)

    amount = parse_float(raw_amount)
    if amount > 0:
        return amount

    return document_total(existing_order)

def resolve_order_money(order: OrderCreate, existing_order: Optional[dict], source_items: list) -> dict:
    shipping_cost = round_money(order.shipping_cost)
    if shipping_cost <= 0 and existing_order:
        shipping_cost = round_money(existing_order.get("shipping_cost"))

    tax_amount = round_money(order.tax_amount)
    if tax_amount <= 0 and existing_order:
        tax_amount = round_money(existing_order.get("tax_amount"))

    subtotal = items_total(source_items)
    requested_total = round_money(order.total_amount)
    if requested_total <= 0 and existing_order:
        requested_total = document_total(existing_order)
    if requested_total <= 0:
        requested_total = round_money(subtotal + shipping_cost + tax_amount)

    return {
        "subtotal": subtotal,
        "shipping_cost": shipping_cost,
        "tax_amount": tax_amount,
        "total_amount": requested_total,
    }

def resolve_billing_address(
    billing_address: Optional[str],
    shipping_address: Optional[str],
    existing_order: Optional[dict] = None,
) -> Optional[str]:
    distinct_billing = get_distinct_billing_address(billing_address, shipping_address)
    if distinct_billing:
        return distinct_billing
    if existing_order and existing_order.get("billing_address"):
        return existing_order.get("billing_address")
    return normalize_address(shipping_address) or None

def require_database():
    db = get_database()
    if db is None:
        raise HTTPException(status_code=503, detail="Database connection is not available.")
    return db

def get_distinct_billing_address(billing_address: Optional[str], shipping_address: Optional[str]) -> Optional[str]:
    normalized_billing = normalize_address(billing_address)
    if not normalized_billing:
        return None

    normalized_shipping = normalize_address(shipping_address)
    if normalized_billing.lower() == normalized_shipping.lower():
        return None

    return normalized_billing

def country_code(value: Optional[str]) -> str:
    cleaned = normalize_address(value).lower()
    return COUNTRY_CODES.get(cleaned, normalize_address(value)[:3].upper())

def address_details_to_dict(details: Any) -> dict:
    if not details:
        return {}
    if isinstance(details, dict):
        return details
    if hasattr(details, "model_dump"):
        return details.model_dump()
    return {}

def first_non_empty(*values) -> str:
    for value in values:
        normalized = normalize_address(value)
        if normalized:
            return normalized
    return ""

def build_spire_address(
    details: Any,
    fallback_line: Optional[str],
    current_user: UserInDB,
    use_billing_defaults: bool = False,
) -> dict:
    data = address_details_to_dict(details)
    first_name = normalize_address(current_user.first_name)
    last_name = normalize_address(current_user.last_name)
    full_name = normalize_address(data.get("name")) or normalize_address(f"{first_name} {last_name}")

    default_line = current_user.billing_street_address if use_billing_defaults else current_user.street_address
    default_city = current_user.billing_city if use_billing_defaults else current_user.city
    default_state = current_user.billing_state_province if use_billing_defaults else current_user.state_province
    default_zip = current_user.billing_zip if use_billing_defaults else current_user.zip
    default_country = current_user.billing_country if use_billing_defaults else current_user.country

    line1 = first_non_empty(data.get("line1"), data.get("street"), default_line, fallback_line)
    city = first_non_empty(data.get("city"), default_city)
    prov_state = first_non_empty(data.get("prov_state"), data.get("provState"), data.get("state"), default_state)
    postal_code = first_non_empty(data.get("postal_code"), data.get("postalCode"), data.get("zip"), default_zip)
    country = country_code(first_non_empty(data.get("country"), default_country))
    email = first_non_empty(data.get("email"), current_user.email)
    phone = first_non_empty(data.get("phone"), current_user.phone_number)

    address = {
        "name": full_name[:40],
        "line1": line1[:50],
        "city": city[:30],
        "provState": prov_state[:20],
        "postalCode": postal_code[:10],
        "country": country,
        "email": email[:50],
        "salesperson": {
            "code": "WEB"
        },
        "territory": {
            "code": "WEB"
        }
    }

    if phone:
        address["phone"] = {"number": phone[:20]}

    return {key: value for key, value in address.items() if value}

def payment_status_for(method: str) -> str:
    if method in COD_PAYMENT_METHODS:
        return "Payment Due on Delivery"
    if method == "e_transfer":
        return "E-Transfer Pending"
    return "Paid"

def provider_for(method: str) -> str:
    if method in COD_PAYMENT_METHODS:
        return "Cash on Delivery"
    if method == "e_transfer":
        return "E-Transfer"
    return "Elavon"

def payment_note_for(method: str, total_amount: Optional[float] = None, txn_id: Optional[str] = None) -> str:
    if method in COD_PAYMENT_METHODS:
        return "*** PAYMENT DUE ON DELIVERY — not yet paid. ***"
    if method == "e_transfer":
        return "*** E-TRANSFER PENDING — awaiting customer payment. ***"
    amount_txt = f" ${total_amount:.2f} CAD" if total_amount else ""
    ref_txt = f" Ref: {txn_id}." if txn_id else ""
    return f"*** PAID ONLINE — order already paid{amount_txt} by credit card through the website.{ref_txt} ***"

def format_address_note(addr: dict) -> str:
    """Human-readable single-line address for a Spire order note."""
    if not addr:
        return ""
    city_line = " ".join(
        part for part in [addr.get("city"), addr.get("provState"), addr.get("postalCode")] if part
    )
    parts = [addr.get("name"), addr.get("line1"), city_line, addr.get("country")]
    return ", ".join(part for part in parts if part)

def spire_order_notes(
    customer_notes: str,
    payment_method: str,
    total_amount: Optional[float] = None,
    txn_id: Optional[str] = None,
    shipping_method: Optional[str] = None,
    ship_address: Optional[dict] = None,
    bill_address: Optional[dict] = None,
    promo_note: str = "",
) -> str:
    """Build the Spire order note body.

    Spire has no dedicated web/paid flag, so we surface everything the warehouse
    and accounting need directly in the order note: payment status, the exact
    ship-to/bill-to used at checkout, promo items and customer notes.
    """
    pieces = []
    payment_note = payment_note_for(payment_method, total_amount, txn_id)
    if payment_note:
        pieces.append(payment_note)

    is_pickup = str(shipping_method or "").strip().lower() == "pickup"
    if is_pickup:
        pieces.append("Fulfillment: PICKUP at store.")
    else:
        ship_line = format_address_note(ship_address or {})
        if ship_line:
            pieces.append(f"Ship to: {ship_line}")

    bill_line = format_address_note(bill_address or {})
    ship_line = format_address_note(ship_address or {})
    if bill_line and bill_line.lower() != ship_line.lower():
        pieces.append(f"Bill to: {bill_line}")

    if promo_note:
        pieces.append(promo_note)
    if customer_notes:
        pieces.append(f"Customer notes: {customer_notes}")
    return " | ".join(pieces)[:3500]

FREE_TSHIRT_DEFAULT_SIZES = ["S", "M", "L", "XL"]
# Sizes explicitly not offered for the free T-shirt (per Dennis: no 2XL).
FREE_TSHIRT_EXCLUDED_SIZES = {"2XL", "XXL"}
PROMO_ORDER_STATUSES_NOT_COUNTED = {"Payment Pending", "Payment Failed", "Cancelled", "Canceled"}

def free_tshirt_promo_enabled() -> bool:
    value = os.getenv("FREE_TSHIRT_PROMO_ENABLED", "true").strip().lower()
    return value not in {"0", "false", "no", "off"}

def free_tshirt_size_options() -> List[str]:
    raw = os.getenv("FREE_TSHIRT_SIZE_OPTIONS", "").strip()
    if not raw:
        return FREE_TSHIRT_DEFAULT_SIZES
    sizes = [part.strip().upper() for part in raw.split(",") if part.strip()]
    return sizes or FREE_TSHIRT_DEFAULT_SIZES

def free_tshirt_sku_map() -> Dict[str, str]:
    raw = os.getenv("FREE_TSHIRT_SKUS", "").strip()
    if not raw:
        return {}

    parsed: Dict[str, str] = {}
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            parsed = {str(size).strip().upper(): str(sku).strip() for size, sku in data.items() if str(sku).strip()}
    except json.JSONDecodeError:
        for pair in raw.split(","):
            if ":" not in pair:
                continue
            size, sku = pair.split(":", 1)
            size = size.strip().upper()
            sku = sku.strip()
            if size and sku:
                parsed[size] = sku

    return parsed

def free_tshirt_available_sizes() -> List[str]:
    configured_sizes = list(free_tshirt_sku_map().keys())
    sizes = configured_sizes or free_tshirt_size_options()
    # Never offer excluded sizes (e.g. 2XL), regardless of env/SKU configuration.
    return [size for size in sizes if size.upper() not in FREE_TSHIRT_EXCLUDED_SIZES]

def normalize_free_tshirt_size(size: Optional[str]) -> Optional[str]:
    normalized = (size or "").strip().upper()
    if not normalized:
        return None
    if normalized in free_tshirt_available_sizes():
        return normalized
    return None

def free_tshirt_part_no_for_size(size: str) -> Optional[str]:
    return free_tshirt_sku_map().get(size)

def is_free_tshirt_part_no(part_no: Optional[str]) -> bool:
    normalized = (part_no or "").strip()
    if not normalized:
        return False
    return normalized in set(free_tshirt_sku_map().values())

def build_free_tshirt_order_item(size: str, sku: str) -> dict:
    return {
        "product_id": sku,
        "quantity": 1,
        "price": 0.0,
        "name": f"Free Action T-Shirt - {size}",
        "sku": sku,
        "image": None,
        "is_dangerous_good": False,
        "is_free_tshirt": True,
        "free_tshirt_size": size,
    }

async def customer_has_prior_web_order(
    db,
    current_user: UserInDB,
    exclude_values: Optional[List[str]] = None,
) -> bool:
    exclude_values = [str(value) for value in (exclude_values or []) if value]
    query: Dict[str, Any] = {
        "customer_email": current_user.email,
        "status": {"$nin": list(PROMO_ORDER_STATUSES_NOT_COUNTED)},
    }

    if exclude_values:
        query["$nor"] = [
            {"id": {"$in": exclude_values}},
            {"local_order_id": {"$in": exclude_values}},
            {"payment_session_id": {"$in": exclude_values}},
            {"converge_txn_id": {"$in": exclude_values}},
            {"elavon_order_id": {"$in": exclude_values}},
        ]

    existing = await db["orders"].find_one(query, {"_id": 1})
    return existing is not None

async def customer_can_claim_free_tshirt(
    db,
    current_user: UserInDB,
    exclude_values: Optional[List[str]] = None,
) -> bool:
    if not free_tshirt_promo_enabled():
        return False
    return not await customer_has_prior_web_order(db, current_user, exclude_values)

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

def valid_order_identifier(*values) -> Optional[str]:
    invalid = {"", "PENDING", "UNKNOWN", "NONE", "NULL"}
    for value in values:
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized.upper() not in invalid:
            return normalized
    return None

def spire_fallback_payload(
    order_data: dict,
    include_freight: bool = True,
    include_note_aliases: bool = True,
) -> dict:
    allowed_keys = [
        "customer",
        "status",
        "salesperson",
        "customerPO",
        "items",
        "shippingAddress",
        "billingAddress",
        "referenceNo",
        "shippingCarrier",
    ]
    fallback = {key: order_data[key] for key in allowed_keys if key in order_data}
    if include_freight and "freightAmount" in order_data:
        fallback["freightAmount"] = order_data["freightAmount"]
    return fallback

async def create_spire_order_with_fallback(order_data: dict) -> dict:
    try:
        return await spire_client.create_sales_order(order_data)
    except HTTPException as first_error:
        print(f"Spire full order payload failed, retrying without optional fields: {first_error.detail}")

    fallback = spire_fallback_payload(order_data, include_freight=True, include_note_aliases=True)
    try:
        return await spire_client.create_sales_order(fallback)
    except HTTPException as second_error:
        print(f"Spire fallback order payload failed, retrying with minimal order payload: {second_error.detail}")

    minimal_fallback = spire_fallback_payload(order_data, include_freight=True, include_note_aliases=False)
    try:
        return await spire_client.create_sales_order(minimal_fallback)
    except HTTPException as third_error:
        print(f"Spire minimal order payload failed, retrying without freightAmount: {third_error.detail}")

    fallback_without_freight = spire_fallback_payload(order_data, include_freight=False, include_note_aliases=False)
    return await spire_client.create_sales_order(fallback_without_freight)

def spire_note_subject(payment_method: str) -> str:
    if payment_method in COD_PAYMENT_METHODS:
        return "Payment Due on Delivery"
    if payment_method == "e_transfer":
        return "E-Transfer Pending"
    return "Paid Online"

async def create_spire_order_note_safe(order_id: Optional[str], note_body: str, payment_method: str):
    order_id = valid_order_identifier(order_id)
    note_body = (note_body or "").strip()
    if not order_id or not note_body:
        return

    subject = spire_note_subject(payment_method)
    # Alert on every web order so staff get a clear popup in Spire — including
    # "Paid Online" orders, which previously had no visible paid indicator.
    alert = True
    payload = {
        "subject": subject[:60],
        "body": note_body[:4000],
        "print": True,
        "alert": alert,
    }
    try:
        await spire_client.create_sales_order_note(order_id, payload)
        return
    except HTTPException as first_error:
        print(f"Spire order note payload failed, retrying minimal note: {first_error.detail}")

    try:
        await spire_client.create_sales_order_note(order_id, {
            "subject": subject[:60],
            "body": note_body[:4000],
            "print": True,
            "alert": alert,
        })
    except Exception as e:
        print(f"Could not create Spire order note for order {order_id}: {e}")

async def notify_staff_pending_payment_order(
    current_user: UserInDB,
    order: OrderCreate,
    order_id: str,
    local_order_id: str,
    items: list,
    total_amount: float,
    shipping_address: str,
    billing_address: str = "",
    po_number: str = "",
    order_notes: str = "",
):
    if order.payment_method not in DIRECT_ORDER_PAYMENT_METHODS:
        return

    try:
        await send_pending_payment_order_notification_email(
            customer_name=f"{current_user.first_name} {current_user.last_name}".strip(),
            customer_email=current_user.email,
            customer_company=current_user.company or "",
            payment_method=order.payment_method,
            order_id=order_id,
            local_order_id=local_order_id,
            items=items,
            total_amount=total_amount,
            shipping_address=shipping_address or "N/A",
            billing_address=billing_address or "",
            po_number=po_number or "",
            order_notes=order_notes or "",
        )
    except Exception as e:
        print(f"Error sending pending payment order notification: {e}")

async def verify_converge_transaction(txn_id: str):
    """Consulta el estado de una transacción en Elavon EPG REST API"""
    url = f"{settings.CONVERGE_URL.rstrip('/')}/transactions/{txn_id}"

    async with httpx.AsyncClient() as client:
        resp = await client.get(url, auth=(settings.ELAVON_MERCHANT_ALIAS, settings.ELAVON_SECRET_KEY))
        if resp.status_code != 200:
            return None
        return resp.json()

def get_transaction_href(transaction_data, base_url: str) -> Optional[str]:
    if not transaction_data:
        return None
    if isinstance(transaction_data, str):
        return transaction_data
    if isinstance(transaction_data, dict):
        if transaction_data.get("href"):
            return transaction_data.get("href")
        if transaction_data.get("id"):
            return f"{base_url}/transactions/{transaction_data['id']}"
    return None

async def verify_elavon_payment(payment_txn_id: Optional[str], existing_order: Optional[dict]):
    if (existing_order or {}).get("provider") == "Elavon Converge HPP":
        return await query_converge_transaction(payment_txn_id)

    base_url = settings.CONVERGE_URL.rstrip("/")
    auth = (settings.ELAVON_MERCHANT_ALIAS, settings.ELAVON_SECRET_KEY)

    if payment_txn_id:
        return await verify_converge_transaction(payment_txn_id)

    if not existing_order:
        return None

    session_href = existing_order.get("elavon_payment_session_href")
    session_id = existing_order.get("payment_session_id")
    if not session_href and session_id:
        session_href = f"{base_url}/payment-sessions/{session_id}"

    if not session_href:
        return None

    async with httpx.AsyncClient(timeout=30) as client:
        session_resp = await client.get(session_href, auth=auth, headers={"Accept": "application/json"})
        if session_resp.status_code != 200:
            return None

        session_data = session_resp.json()
        tx_href = get_transaction_href(session_data.get("transaction"), base_url)
        if not tx_href:
            return None

        tx_resp = await client.get(tx_href, auth=auth, headers={"Accept": "application/json"})
        if tx_resp.status_code != 200:
            return None

        tx_data = tx_resp.json()
        tx_data["_transaction_href"] = tx_href
        tx_data["_payment_session"] = session_data
        return tx_data

class ShippingItem(BaseModel):
    product_id: str
    quantity: int
    weight_kg: float = 0.0
    sku: Optional[str] = None
    name: Optional[str] = None
    is_dangerous_good: bool = False

class ShippingRequest(BaseModel):
    postal_code: str
    items: List[ShippingItem]
    subtotal: float

def calculate_shipping_cost_response(
    req: ShippingRequest,
    free_delivery: bool = False,
    ship_code: str = "",
    freightcom_shipping_cost: Optional[float] = None,
) -> dict:
    return calculate_shipping_breakdown(
        req.subtotal,
        req.items,
        free_delivery=free_delivery,
        ship_code=ship_code,
        postal_code=req.postal_code,
        freightcom_shipping_cost=freightcom_shipping_cost,
    )

def get_product_weight(product_id: str) -> float:
    try:
        with open("products.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data.get("records", []):
                if item.get("partNo") == product_id:
                    weight_str = item.get("weight", "0")
                    return float(weight_str)
    except Exception as e:
        print(f"Error reading weight for {product_id}: {e}")
    return 0.0

@router.post("/calculate-shipping")
async def calculate_shipping(
    req: ShippingRequest,
    current_user: Optional[UserInDB] = Depends(get_optional_current_user),
):
    # Regla de negocio estricta: < 250 -> $20 fijo, >= 250 -> $0 (Gratis)
    # Esto tiene prioridad sobre cualquier cotización de transportista externa.
    shipping_settings = await get_customer_shipping_settings(current_user)
    freightcom_shipping_cost = None
    if requires_freightcom_quote(
        req.postal_code,
        free_delivery=shipping_settings["free_delivery"],
        ship_code=shipping_settings["ship_code"],
    ):
        freightcom_shipping_cost = await quote_freightcom_shipping(req.postal_code, req.items, req.subtotal)
    return calculate_shipping_cost_response(
        req,
        free_delivery=shipping_settings["free_delivery"],
        ship_code=shipping_settings["ship_code"],
        freightcom_shipping_cost=freightcom_shipping_cost,
    )

    total_weight = 0.0
    for item in req.items:
        w = get_product_weight(item.product_id)
        # If weight is 0, we assume at least 0.5 kg for shipping purposes
        weight_to_add = w if w > 0 else 0.5
        total_weight += (weight_to_add * item.quantity)
    
    if total_weight == 0:
        total_weight = 1.0

    # Ensure max length for postal code and formatting (Canada Post expects without spaces)
    dest_zip = req.postal_code.replace(" ", "").upper()

    xml_request = f"""<?xml version="1.0" encoding="UTF-8"?>
<mailing-scenario xmlns="http://www.canadapost.ca/ws/ship/rate-v4">
  <customer-number>1234567</customer-number>
  <parcel-characteristics>
    <weight>{total_weight:.2f}</weight>
  </parcel-characteristics>
  <origin-postal-code>K2B8J6</origin-postal-code>
  <destination>
    <domestic>
      <postal-code>{dest_zip}</postal-code>
    </domestic>
  </destination>
</mailing-scenario>"""

    # We will try to call the Canada Post API.
    # Note: Without a valid API key, this will likely fail. We provide a fallback.
    url = "https://ct.soa-gw.canadapost.ca/rs/ship/price"
    
    # We use a dummy API key for the development environment. In production, these should be env vars.
    api_user = os.getenv("CANADA_POST_USER", "dummy")
    api_pass = os.getenv("CANADA_POST_PASS", "dummy")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=xml_request,
                headers={"Content-Type": "application/vnd.cpc.ship.rate-v4+xml", "Accept": "application/vnd.cpc.ship.rate-v4+xml"},
                auth=(api_user, api_pass),
                timeout=5.0
            )
            
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            # Find all prices and return the minimum or standard (DOM.RP)
            namespaces = {'ns': 'http://www.canadapost.ca/ws/ship/rate-v4'}
            quotes = root.findall('.//ns:price-quote', namespaces)
            prices = []
            for quote in quotes:
                due = quote.find('.//ns:due', namespaces)
                if due is not None:
                    prices.append(float(due.text))
            
            if prices:
                return {"shipping_cost": min(prices)}
    except Exception as e:
        print(f"Canada Post API Error: {e}")
    
    # Aplicar regla de negocio: < 250 -> 20, >= 250 -> Gratis
    shipping_settings = await get_customer_shipping_settings(current_user)
    return calculate_shipping_cost_response(
        req,
        free_delivery=shipping_settings["free_delivery"],
        ship_code=shipping_settings["ship_code"],
    )

@router.get("/promotions/free-tshirt")
async def get_free_tshirt_promotion(current_user: Optional[UserInDB] = Depends(get_optional_current_user)):
    sizes = free_tshirt_available_sizes()
    eligible = False
    if current_user:
        db = require_database()
        eligible = await customer_can_claim_free_tshirt(db, current_user)

    return {
        "enabled": free_tshirt_promo_enabled(),
        "eligible": eligible,
        "configured": bool(free_tshirt_sku_map()),
        "sizes": sizes,
        "title": "Free T-shirt with your first web order",
        "message": "Choose your size at checkout. One free T-shirt per customer on their first website order.",
    }

@router.post("/", response_model=OrderResponse)
async def create_order(order: OrderCreate, current_user: UserInDB = Depends(get_current_user)):
    total_amount = 0.0
    db = require_database()
    lookup_values = [
        order.local_order_id,
        order.payment_session_id,
        order.stripe_payment_intent_id
    ]
    lookup_values = [value for value in lookup_values if value]
    existing_order = None
    if lookup_values:
        existing_order = await db["orders"].find_one({
            "$or": [
                {"id": {"$in": lookup_values}},
                {"local_order_id": {"$in": lookup_values}},
                {"payment_session_id": {"$in": lookup_values}},
                {"converge_txn_id": {"$in": lookup_values}},
                {"elavon_order_id": {"$in": lookup_values}}
            ]
        })

    if existing_order and existing_order.get("spire_order_no") not in (None, "", "Pending", "UNKNOWN"):
        status = existing_order.get("status", "Paid")
        return {
            "order_id": str(existing_order.get("spire_order_no")),
            "status": status,
            "total_amount": document_total(existing_order)
        }

    source_items = order.items or (existing_order or {}).get("items") or []
    if not source_items:
        raise HTTPException(status_code=400, detail="No items found for this order.")

    preliminary_shipping_address_details = (
        address_details_to_dict(order.shipping_address_details)
        or (existing_order or {}).get("shipping_address_details")
        or {}
    )
    shipping_postal_code = first_non_empty(
        preliminary_shipping_address_details.get("postal_code"),
        preliminary_shipping_address_details.get("postalCode"),
        preliminary_shipping_address_details.get("zip"),
        current_user.zip,
    )
    shipping_settings = await get_customer_shipping_settings(current_user)
    free_delivery = shipping_settings["free_delivery"]
    money = resolve_order_money(order, existing_order, source_items)
    shipping_cost = money["shipping_cost"]
    tax_amount = money["tax_amount"]
    freightcom_shipping_cost = None
    if requires_freightcom_quote(
        shipping_postal_code,
        order.shipping_method,
        free_delivery=free_delivery,
        ship_code=shipping_settings["ship_code"],
    ):
        freightcom_shipping_cost = await quote_freightcom_shipping(
            shipping_postal_code,
            source_items,
            money["subtotal"],
        )
    shipping_breakdown = calculate_shipping_breakdown(
        money["subtotal"],
        source_items,
        order.shipping_method,
        free_delivery=free_delivery,
        ship_code=shipping_settings["ship_code"],
        postal_code=shipping_postal_code,
        freightcom_shipping_cost=freightcom_shipping_cost,
    )
    required_shipping_cost = round_money(shipping_breakdown["shipping_cost"])
    if free_delivery or str(order.shipping_method or "").lower() == "pickup":
        shipping_cost = required_shipping_cost
        money["shipping_cost"] = shipping_cost
    elif shipping_cost < required_shipping_cost:
        shipping_cost = required_shipping_cost
        money["shipping_cost"] = shipping_cost

    expected_total = round_money(money["subtotal"] + shipping_cost + tax_amount)
    if money["total_amount"] < expected_total:
        money["total_amount"] = expected_total

    # 1. Validate Converge Transaction (unless On Account)
    if order.payment_method in DIRECT_ORDER_PAYMENT_METHODS:
        # Skip Converge validation for On Account and E-Transfer orders.
        total_amount = money["total_amount"]
    else:
        payment_txn_id = order.stripe_payment_intent_id or (existing_order or {}).get("converge_txn_id")
        txn_data = await verify_elavon_payment(payment_txn_id, existing_order)
        if not txn_data:
            raise HTTPException(status_code=409, detail="Payment is still pending confirmation. Please wait a moment.")
        
        # En EPG REST, el estado exitoso suele ser "COMPLETED"
        txn_state = str(txn_data.get("state") or txn_data.get("status") or "").upper()
        if txn_state not in ("COMPLETED", "CAPTURED", "APPROVED", "AUTHORIZED"):
            error_msg = (
                txn_data.get("errorMessage")
                or txn_data.get("message")
                or txn_data.get("ssl_result_message")
                or "Transaction not completed"
            )
            if existing_order:
                await db["orders"].update_one(
                    {"_id": existing_order["_id"]},
                    {"$set": {
                        "status": "Payment Failed",
                        "error_message": error_msg,
                        "updated_at": datetime.utcnow().isoformat()
                    }}
                )
            raise HTTPException(status_code=400, detail=f"Payment validation failed: {error_msg}")
            
        total_amount = payment_amount(txn_data, existing_order)
        if total_amount + 0.01 < expected_total:
            raise HTTPException(
                status_code=400,
                detail="Order total does not include the D.G. shipping surcharge. Please restart checkout."
            )
        transaction_id = txn_data.get("id") or payment_txn_id
        order.stripe_payment_intent_id = transaction_id
        if existing_order and transaction_id:
            await db["orders"].update_one(
                {"_id": existing_order["_id"]},
                {"$set": {
                    "converge_txn_id": transaction_id,
                    "status": "Payment Confirmed",
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )

    local_order_id = (
        order.local_order_id
        or (existing_order or {}).get("local_order_id")
        or (existing_order or {}).get("id")
        or order.stripe_payment_intent_id
    )
    if not local_order_id and order.payment_method in COD_PAYMENT_METHODS:
        local_order_id = f"cod_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    if not local_order_id and order.payment_method == "e_transfer":
        local_order_id = f"e_transfer_{datetime.utcnow().strftime('%Y%m%d%H%M%S%f')}"
    po_number = (order.po_number or "").strip() or (existing_order or {}).get("po_number")
    order_notes = (order.order_notes or "").strip() or (existing_order or {}).get("order_notes")
    shipping_address = order.shipping_address or (existing_order or {}).get("shipping_address") or ""
    shipping_address_details = preliminary_shipping_address_details
    billing_address_details = address_details_to_dict(order.billing_address_details) or (existing_order or {}).get("billing_address_details")
    reference_no = (local_order_id or "OnAccount")[:20]
    if total_amount <= 0:
        total_amount = money["total_amount"]

    def item_field(item, field: str, default=None):
        if isinstance(item, dict):
            return item.get(field, default)
        return getattr(item, field, default)

    promo_exclude_values = [
        local_order_id,
        order.local_order_id,
        order.payment_session_id,
        order.stripe_payment_intent_id,
        (existing_order or {}).get("id"),
        (existing_order or {}).get("local_order_id"),
        (existing_order or {}).get("payment_session_id"),
        (existing_order or {}).get("converge_txn_id"),
        (existing_order or {}).get("elavon_order_id"),
    ]
    requested_free_tshirt_size = (
        order.free_tshirt_size
        or (existing_order or {}).get("free_tshirt_size")
        or ""
    )
    free_tshirt_size = normalize_free_tshirt_size(requested_free_tshirt_size)
    existing_free_tshirt_line = next(
        (
            item for item in source_items
            if item_field(item, "is_free_tshirt", False)
            or is_free_tshirt_part_no(item_field(item, "sku") or item_field(item, "product_id"))
        ),
        None,
    )
    free_tshirt_item = None
    free_tshirt_sku = (existing_order or {}).get("free_tshirt_sku")
    free_tshirt_claimed = bool((existing_order or {}).get("free_tshirt_claimed") or existing_free_tshirt_line)
    promo_note = ""

    if existing_free_tshirt_line:
        free_tshirt_size = free_tshirt_size or item_field(existing_free_tshirt_line, "free_tshirt_size")
        free_tshirt_sku = free_tshirt_sku or item_field(existing_free_tshirt_line, "sku") or item_field(existing_free_tshirt_line, "product_id")
        if free_tshirt_size:
            promo_note = f"Free T-shirt included. Size: {free_tshirt_size}."
    elif free_tshirt_size and not free_tshirt_claimed:
        if await customer_can_claim_free_tshirt(db, current_user, promo_exclude_values):
            free_tshirt_claimed = True
            free_tshirt_sku = free_tshirt_part_no_for_size(free_tshirt_size)
            promo_note = f"Free T-shirt requested for first web order. Size: {free_tshirt_size}."
            if free_tshirt_sku:
                free_tshirt_item = build_free_tshirt_order_item(free_tshirt_size, free_tshirt_sku)
                promo_note = f"Free T-shirt included. Size: {free_tshirt_size}."
            else:
                print(
                    "Free T-shirt requested but FREE_TSHIRT_SKUS has no SKU "
                    f"for size {free_tshirt_size}."
                )

    source_items_for_order = list(source_items)
    if free_tshirt_item:
        source_items_for_order.append(free_tshirt_item)

    if not existing_order:
        now = datetime.utcnow().isoformat()
        initial_status = "ERP Pending"
        if order.payment_method in COD_PAYMENT_METHODS:
            initial_status = "Payment Due on Delivery"
        elif order.payment_method == "e_transfer":
            initial_status = "E-Transfer Pending"

        initial_items = [
            {
                "product_id": item_field(item, "product_id"),
                "quantity": item_field(item, "quantity", 1),
                "price": item_field(item, "price", 0),
                "name": item_field(item, "name", ""),
                "sku": item_field(item, "sku") or item_field(item, "product_id"),
                "image": item_field(item, "image"),
                "is_dangerous_good": item_field(item, "is_dangerous_good", False),
                "is_free_tshirt": item_field(item, "is_free_tshirt", False),
                "free_tshirt_size": item_field(item, "free_tshirt_size"),
            }
            for item in source_items_for_order
        ]
        await db["orders"].update_one(
            {"id": local_order_id},
            {
                "$set": {
                    "id": local_order_id,
                    "local_order_id": local_order_id,
                    "spire_order_no": "Pending",
                    "customer_email": current_user.email,
                    "total_amount": total_amount,
                    "shipping_address": shipping_address,
                    "shipping_address_details": shipping_address_details,
                    "billing_address": resolve_billing_address(order.billing_address, shipping_address, existing_order),
                    "billing_address_details": billing_address_details,
                    "shipping_cost": shipping_cost,
                    "tax_amount": tax_amount,
                    "po_number": po_number,
                    "order_notes": order_notes,
                    "items": initial_items,
                    "free_tshirt_size": free_tshirt_size,
                    "free_tshirt_sku": free_tshirt_sku,
                    "free_tshirt_claimed": free_tshirt_claimed,
                    "converge_txn_id": order.stripe_payment_intent_id,
                    "payment_session_id": order.payment_session_id,
                    "status": initial_status,
                    "provider": provider_for(order.payment_method),
                    "updated_at": now
                },
                "$setOnInsert": {"created_at": now}
            },
            upsert=True
        )

    shipping_spire_address = build_spire_address(
        shipping_address_details,
        shipping_address,
        current_user,
        use_billing_defaults=False,
    )
    billing_spire_address = build_spire_address(
        billing_address_details,
        resolve_billing_address(order.billing_address, shipping_address, existing_order),
        current_user,
        use_billing_defaults=bool(current_user.billing_street_address),
    )

    spire_items = [
        {
            "partNo": item_field(item, "sku") or item_field(item, "product_id"),
            "description": str(item_field(item, "name", ""))[:40],
            "orderQty": item_field(item, "quantity", 1),
            "unitPrice": str(item_field(item, "price", 0))
        } for item in source_items_for_order
    ]

    freight_part_no = os.getenv("SPIRE_FREIGHT_PART_NO", "").strip()
    if shipping_cost > 0 and freight_part_no:
        spire_items.append({
            "partNo": freight_part_no,
            "description": "Delivery Fee",
            "orderQty": 1,
            "unitPrice": f"{shipping_cost:.2f}",
        })

    spire_reference_no = f"WEB-{reference_no}"[:20] if not str(reference_no).upper().startswith("WEB") else reference_no
    spire_notes = spire_order_notes(
        order_notes or "",
        order.payment_method,
        total_amount=total_amount,
        txn_id=order.stripe_payment_intent_id,
        shipping_method=order.shipping_method,
        ship_address=shipping_spire_address,
        bill_address=billing_spire_address,
        promo_note=promo_note,
    )
    staff_order_notes = " | ".join([note for note in (order_notes, promo_note) if note])[:500]

    # 2. Map to Spire Order format according to the API schema
    spire_order = {
        "customer": {
            "customerNo": current_user.spire_customer_no
        },
        "status": "O",
        "customerPO": po_number[:30] if po_number else "",
        "items": spire_items,
        "shippingAddress": shipping_spire_address,
        "billingAddress": billing_spire_address,
        "referenceNo": spire_reference_no,
        "shippingCarrier": order.shipping_method[:15] if order.shipping_method else "",
        "freightAmount": shipping_cost,
    }

    if existing_order and existing_order.get("items") and not free_tshirt_item:
        saved_items = existing_order.get("items")
    else:
        saved_items = [
            {
                "product_id": item_field(item, "product_id"),
                "quantity": item_field(item, "quantity", 1),
                "price": item_field(item, "price", 0),
                "name": item_field(item, "name", ""),
                "sku": item_field(item, "sku") or item_field(item, "product_id"),
                "image": item_field(item, "image"),
                "is_dangerous_good": item_field(item, "is_dangerous_good", False),
                "is_free_tshirt": item_field(item, "is_free_tshirt", False),
                "free_tshirt_size": item_field(item, "free_tshirt_size"),
            }
            for item in source_items_for_order
        ]
    
    # 3. Send to Spire with explicit error handling for the frontend
    try:
        spire_response = await create_spire_order_with_fallback(spire_order)
        spire_order_id = valid_order_identifier(spire_response.get("id"), spire_response.get("orderId"))
        spire_order_no = spire_response.get("orderNo") or spire_response.get("id") or "UNKNOWN"
        if spire_order_no == "UNKNOWN":
            print(f"Warning: Spire response missing orderNo. Response: {spire_response}")
            
            # Fallback: Recuperar la orden recién creada consultando por el referenceNo (Stripe ID)
            customer_orders = await spire_client.get_customer_orders(current_user.spire_customer_no)
            for rec in customer_orders.get("records", []):
                if rec.get("referenceNo") in (spire_reference_no, reference_no):
                    spire_order_id = valid_order_identifier(rec.get("id"), spire_order_id)
                    spire_order_no = str(rec.get("orderNo") or rec.get("id") or "UNKNOWN")
                    break
        await create_spire_order_note_safe(spire_order_id or spire_order_no, spire_notes, order.payment_method)
    except HTTPException as e:
        # Registra el error detallado en la consola del backend
        print(f"Spire ERP Error details: {e.detail}")
        if order.payment_method == "e_transfer":
            fallback_billing_address = resolve_billing_address(order.billing_address, shipping_address, existing_order)
            await db["orders"].update_one(
                {"id": local_order_id},
                {"$set": {
                    "id": local_order_id,
                    "local_order_id": local_order_id,
                    "spire_order_no": "Pending",
                    "customer_email": current_user.email,
                    "total_amount": total_amount,
                    "shipping_address": shipping_address,
                    "shipping_address_details": shipping_address_details,
                    "billing_address": fallback_billing_address,
                    "billing_address_details": billing_address_details,
                    "shipping_cost": shipping_cost,
                    "tax_amount": tax_amount,
                    "po_number": po_number,
                    "order_notes": order_notes,
                    "items": saved_items,
                    "free_tshirt_size": free_tshirt_size,
                    "free_tshirt_sku": free_tshirt_sku,
                    "free_tshirt_claimed": free_tshirt_claimed,
                    "status": "E-Transfer Pending",
                    "provider": "E-Transfer",
                    "erp_error_message": str(e.detail),
                    "updated_at": datetime.utcnow().isoformat()
                }},
                upsert=True
            )
            await notify_staff_pending_payment_order(
                current_user=current_user,
                order=order,
                order_id=local_order_id,
                local_order_id=local_order_id,
                items=saved_items,
                total_amount=total_amount,
                shipping_address=shipping_address,
                billing_address=fallback_billing_address or "",
                po_number=po_number or "",
                order_notes=staff_order_notes or "",
            )
            return {
                "order_id": local_order_id,
                "status": "E-Transfer Pending",
                "total_amount": total_amount
            }
        raise HTTPException(status_code=400, detail="There was an issue processing your order with our side. Please contact support.")
    except Exception as e:
        print(f"Unexpected error communicating with Spire: {str(e)}")
        raise HTTPException(status_code=500, detail="Unexpected error communicating with the server. Please try again later.")

    # 4. Actualizar la orden local de Payment Pending/Confirmed a Paid una vez creada en Spire
    if not local_order_id:
        local_order_id = f"on_account_{spire_order_no}"

    billing_address = resolve_billing_address(order.billing_address, shipping_address, existing_order)

    update_filter = {"_id": existing_order["_id"]} if existing_order else {"id": local_order_id}
    await db["orders"].update_one(
        update_filter,
        {"$set": {
            "id": local_order_id,
            "local_order_id": local_order_id,
            "spire_order_no": spire_order_no,
            "customer_email": current_user.email,
            "total_amount": total_amount,
            "shipping_address": shipping_address,
            "shipping_address_details": shipping_address_details,
            "billing_address": billing_address,
            "billing_address_details": billing_address_details,
            "shipping_cost": shipping_cost,
            "tax_amount": tax_amount,
            "po_number": po_number,
            "order_notes": order_notes,
            "items": saved_items,
            "free_tshirt_size": free_tshirt_size,
            "free_tshirt_sku": free_tshirt_sku,
            "free_tshirt_claimed": free_tshirt_claimed,
            "converge_txn_id": order.stripe_payment_intent_id,
            "payment_session_id": order.payment_session_id or (existing_order or {}).get("payment_session_id"),
            "status": payment_status_for(order.payment_method),
            "provider": provider_for(order.payment_method),
            "updated_at": datetime.utcnow().isoformat()
        }},
        upsert=True
    )

    # 5. Notify staff when payment will be handled outside the online card flow.
    await notify_staff_pending_payment_order(
        current_user=current_user,
        order=order,
        order_id=str(spire_order_no),
        local_order_id=str(local_order_id),
        items=saved_items,
        total_amount=total_amount,
        shipping_address=shipping_address,
        billing_address=billing_address or "",
        po_number=po_number or "",
        order_notes=staff_order_notes or "",
    )

    # 6. Enviar email de confirmación
    try:
        await send_order_confirmation_email(
            to_email=current_user.email,
            name=f"{current_user.first_name} {current_user.last_name}".strip(),
            order_id=spire_order_no,
            items=saved_items,
            total_amount=total_amount,
            shipping_address=shipping_address or "N/A",
            shipping_method=order.shipping_method or "delivery",
            promo_note=promo_note,
        )
    except Exception as e:
        print(f"Error sending order confirmation email: {e}")

    return {
        "order_id": spire_order_no,
        "status": payment_status_for(order.payment_method),
        "total_amount": total_amount
    }

@router.get("/me")
@router.get("/history")
async def get_order_history(request: Request, current_user: UserInDB = Depends(get_current_user)):
    # Fetch orders from Spire for this customer
    response = await spire_client.get_customer_orders(current_user.spire_customer_no)
    records = response.get("records", [])
    
    # Extraemos el estado local de pagos desde MongoDB
    db = get_database()
    local_orders = await db["orders"].find({"customer_email": current_user.email}).to_list(length=None)
    local_data = {str(o.get("spire_order_no")): o for o in local_orders if o.get("spire_order_no")}
    
    status_map = {"O": "Processing", "C": "Completed", "H": "On Hold", "Q": "Quote", "I": "Invoiced"}

    async def build_item_snapshot(part_no: str, quantity=1, price=0.0, description: Optional[str] = None, local_match: Optional[dict] = None):
        local_match = local_match or {}
        name = description or local_match.get("name") or part_no
        image = local_match.get("image")
        is_dangerous_good = local_match.get("is_dangerous_good", False)

        if part_no and (not image or not name or name == part_no):
            try:
                from app.api.endpoints.products import normalize_product_data
                product = await spire_client.get_product(part_no)
                product = normalize_product_data(product, request)
                name = name if name and name != part_no else product.get("description") or part_no
                image = image or product.get("image")
                is_dangerous_good = product.get("is_dangerous_good", is_dangerous_good)
            except Exception:
                pass

        return {
            "product_id": part_no,
            "name": name,
            "sku": local_match.get("sku") or part_no,
            "quantity": quantity,
            "price": parse_float(price),
            "image": image,
            "is_dangerous_good": is_dangerous_good
        }
    
    formatted_orders = []
    seen_local_ids = set()
    for rec in records:
        rec_copy = rec.copy()
        
        order_no = str(rec.get("orderNo") or rec.get("id"))
        local_info = local_data.get(order_no, {})
        if local_info.get("_id"):
            seen_local_ids.add(str(local_info.get("_id")))
        payment_status = local_info.get("status", "Paid") # Por defecto 'Paid' si ya logró entrar a Spire
        spire_status = status_map.get(rec.get("status", "O"), rec.get("status", "O"))
        
        # Combinamos ambos estados para mayor claridad al cliente
        rec_copy["status"] = f"{payment_status}"
        
        # Extraemos el total real cobrado en Stripe desde la BD local. Si no existe, usamos el de Spire.
        local_total = document_total(local_info)
        raw_total = local_total if local_total > 0 else (rec.get("grandTotal") or rec.get("total") or rec.get("subtotal") or 0)
        try:
            rec_copy["total_amount"] = float(raw_total)
        except (ValueError, TypeError):
            rec_copy["total_amount"] = items_total(local_info.get("items") or [])
        if rec_copy["total_amount"] <= 0:
            rec_copy["total_amount"] = items_total(local_info.get("items") or [])
            
        # Extraemos la dirección de envío y la fecha
        shipping_addr = rec.get("shippingAddress", {})
        rec_copy["ship_to"] = shipping_addr.get("line1") or local_info.get("shipping_address") or ""
        rec_copy["shipping_city"] = shipping_addr.get("city") or ""
        rec_copy["created_at"] = local_info.get("created_at") or rec.get("orderDate") or ""
            
        # Extraemos los items combinando Spire (cantidades reales) y local (nombres que Spire no devuelve)
        spire_items = rec.get("items") or []
        local_items = local_info.get("items") or []
        
        mapped_items = []
        if spire_items:
            for it in spire_items:
                part_no = it.get("partNo") or it.get("inventory", {}).get("partNo")
                
                # Buscamos si tenemos guardado el nombre original en nuestra BD local
                local_match = next((li for li in local_items if li.get("product_id") == part_no or li.get("sku") == part_no), {})
                mapped_items.append(await build_item_snapshot(
                    part_no,
                    quantity=it.get("orderQty", 1),
                    price=it.get("unitPrice", 0),
                    description=it.get("description"),
                    local_match=local_match,
                ))
        else:
            mapped_items = [
                await build_item_snapshot(
                    item.get("product_id") or item.get("sku"),
                    quantity=item.get("quantity", 1),
                    price=item.get("price", item.get("unit_price", 0)),
                    description=item.get("name"),
                    local_match=item,
                )
                for item in local_items
                if item.get("product_id") or item.get("sku")
            ]
            
        rec_copy["items"] = mapped_items

        formatted_orders.append(rec_copy)

    for local in local_orders:
        if str(local.get("_id")) in seen_local_ids:
            continue

        local_order_id = valid_order_identifier(
            local.get("local_order_id"),
            local.get("id"),
            local.get("spire_order_no"),
            local.get("_id"),
        ) or str(local.get("_id"))
        local_items = local.get("items") or []
        details = local.get("shipping_address_details") or {}
        formatted_orders.append({
            "id": local_order_id,
            "order_id": local_order_id,
            "_id": str(local.get("_id")),
            "status": local.get("status", "Pending"),
            "total_amount": document_total(local),
            "ship_to": local.get("shipping_address") or details.get("line1") or "",
            "shipping_city": details.get("city") or "",
            "created_at": local.get("created_at") or local.get("updated_at") or "",
            "items": [
                await build_item_snapshot(
                    item.get("product_id") or item.get("sku"),
                    quantity=item.get("quantity", 1),
                    price=item.get("price", item.get("unit_price", 0)),
                    description=item.get("name"),
                    local_match=item,
                )
                for item in local_items
                if item.get("product_id") or item.get("sku")
            ],
        })
        
    formatted_orders.sort(key=lambda o: o.get("created_at") or o.get("orderDate") or "", reverse=True)
    return formatted_orders

@router.get("/products")
async def get_order_product_history(request: Request, current_user: UserInDB = Depends(get_current_user)):
    orders = await get_order_history(request, current_user)
    freight_part_no = os.getenv("SPIRE_FREIGHT_PART_NO", "").strip()
    products_by_id: dict[str, dict] = {}

    for order in orders:
        ordered_at = order.get("created_at") or order.get("orderDate") or ""
        for item in order.get("items") or []:
            part_no = valid_order_identifier(item.get("product_id"), item.get("sku"))
            if not part_no or (freight_part_no and part_no == freight_part_no):
                continue
            if item.get("is_free_tshirt") or is_free_tshirt_part_no(part_no):
                continue

            existing = products_by_id.get(part_no)
            product_snapshot = {
                "product_id": part_no,
                "sku": item.get("sku") or part_no,
                "name": item.get("name") or part_no,
                "price": parse_float(item.get("price", item.get("unit_price", 0))),
                "image": item.get("image"),
                "is_dangerous_good": item.get("is_dangerous_good", False),
                "last_ordered_at": ordered_at,
            }

            if not existing:
                products_by_id[part_no] = product_snapshot
                continue

            if ordered_at and ordered_at > (existing.get("last_ordered_at") or ""):
                existing["last_ordered_at"] = ordered_at
            for key in ("name", "price", "image", "is_dangerous_good"):
                if not existing.get(key) and product_snapshot.get(key):
                    existing[key] = product_snapshot[key]

    return {
        "items": sorted(
            products_by_id.values(),
            key=lambda product: str(product.get("name") or product.get("sku") or "").lower(),
        )
    }

@router.get("/{order_id}/invoice")
async def view_invoice(order_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Fetch specific invoice details from Spire
    return await spire_client.get_sales_order_invoice(order_id)

@router.post("/{order_id}/repeat")
async def repeat_purchase(order_id: str, request: Request, current_user: UserInDB = Depends(get_current_user)):
    try:
        db = get_database()
        local_order = await db["orders"].find_one({
            "customer_email": current_user.email,
            "$or": [
                {"id": order_id},
                {"local_order_id": order_id},
                {"spire_order_no": order_id},
            ]
        })
        if local_order and local_order.get("items"):
            cart_items = []
            for item in local_order.get("items", []):
                part_no = item.get("product_id") or item.get("sku")
                if not part_no:
                    continue
                if item.get("is_free_tshirt") or is_free_tshirt_part_no(part_no):
                    continue
                name = item.get("name") or part_no
                image = item.get("image")
                is_dangerous_good = item.get("is_dangerous_good", False)
                if not image:
                    try:
                        from app.api.endpoints.products import normalize_product_data
                        product = await spire_client.get_product(part_no)
                        product = normalize_product_data(product, request)
                        name = product.get("description") or name
                        image = product.get("image")
                        is_dangerous_good = product.get("is_dangerous_good", is_dangerous_good)
                    except Exception:
                        pass
                cart_items.append({
                    "product_id": part_no,
                    "name": name,
                    "quantity": item.get("quantity", 1),
                    "price": parse_float(item.get("price", item.get("unit_price", 0))),
                    "image": image,
                    "is_dangerous_good": is_dangerous_good
                })
            if cart_items:
                return {
                    "message": f"Order {order_id} fetched successfully for repeat purchase.",
                    "cart_items": cart_items
                }

        # Fetch the old order from Spire
        old_order = await spire_client.get_sales_order(order_id)
        
        # Security check: Ensure this order actually belongs to the current user
        if old_order.get("customer", {}).get("customerNo") != current_user.spire_customer_no:
            raise HTTPException(status_code=403, detail="You do not have permission to access this order.")

        # Extract the items to send back to the frontend cart
        items_to_cart = []
        for item in old_order.get("items", []):
            # Buscamos el partNo en la raíz (o dentro de inventory por seguridad)
            part_no = item.get("partNo") or item.get("inventory", {}).get("partNo")
            if part_no:
                if is_free_tshirt_part_no(part_no):
                    continue
                name = item.get("description") or part_no
                image = None
                is_dangerous_good = False
                
                # Intentamos recuperar el producto desde el inventario para obtener la imagen y nombre real
                try:
                    from app.api.endpoints.products import normalize_product_data
                    product = await spire_client.get_product(part_no)
                    product = normalize_product_data(product, request)
                    name = product.get("description") or name
                    image = product.get("image")
                    is_dangerous_good = product.get("is_dangerous_good", False)
                except Exception:
                    pass

                items_to_cart.append({
                    "product_id": part_no,
                    "name": name,  # React espera la propiedad 'name'
                    "quantity": item.get("orderQty", 1),
                    "price": float(item.get("unitPrice", 0)),   # Aseguramos que el precio sea número
                    "image": image,
                    "is_dangerous_good": is_dangerous_good
                })

        return {
            "message": f"Order {order_id} fetched successfully for repeat purchase.",
            "cart_items": items_to_cart
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
