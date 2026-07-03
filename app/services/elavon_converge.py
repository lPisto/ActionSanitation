from html import escape
from typing import Optional
from xml.etree import ElementTree

import httpx

from app.core.config import settings


def _clean(value: Optional[str]) -> str:
    return str(value or "").strip()


def converge_hpp_configured() -> bool:
    return bool(
        _clean(settings.ELAVON_CONVERGE_ACCOUNT_ID)
        and _clean(settings.ELAVON_CONVERGE_USER_ID)
        and _clean(settings.ELAVON_CONVERGE_PIN)
    )


def converge_hpp_base_url() -> str:
    return (_clean(settings.CONVERGE_HPP_URL) or "https://api.demo.convergepay.com").rstrip("/")


def converge_hpp_payment_url() -> str:
    return f"{converge_hpp_base_url()}/hosted-payments/"


def converge_hpp_token_url() -> str:
    return f"{converge_hpp_base_url()}/hosted-payments/transaction_token"


def converge_xml_url() -> str:
    configured = _clean(settings.CONVERGE_XML_URL)
    if configured:
        return configured

    base_url = converge_hpp_base_url()
    if "api.convergepay.com" in base_url and "demo" not in base_url:
        return "https://api.convergepay.com/VirtualMerchant/processxml.do"

    return "https://api.demo.convergepay.com/VirtualMerchantDemo/processxml.do"


def converge_credentials() -> dict:
    credentials = {
        "ssl_account_id": _clean(settings.ELAVON_CONVERGE_ACCOUNT_ID),
        "ssl_user_id": _clean(settings.ELAVON_CONVERGE_USER_ID),
        "ssl_pin": _clean(settings.ELAVON_CONVERGE_PIN),
    }
    vendor_id = _clean(settings.ELAVON_CONVERGE_VENDOR_ID)
    if vendor_id:
        credentials["ssl_vendor_id"] = vendor_id
    return credentials


def _xml_from_fields(fields: dict) -> str:
    pieces = ["<?xml version=\"1.0\" encoding=\"UTF-8\"?>", "<txn>"]
    for key, value in fields.items():
        pieces.append(f"<{key}>{escape(str(value or ''))}</{key}>")
    pieces.append("</txn>")
    return "".join(pieces)


def _parse_xml_response(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}

    try:
        root = ElementTree.fromstring(raw)
    except ElementTree.ParseError:
        return {"raw_response": raw, "ssl_result_message": raw}

    txn = root if root.tag == "txn" else root.find(".//txn")
    if txn is None:
        txn = root

    result = {}
    for child in list(txn):
        result[child.tag] = (child.text or "").strip()
    return result


async def create_converge_hpp_token(
    *,
    amount: float,
    local_order_id: str,
    customer_email: Optional[str],
    billing_address_details: Optional[dict],
    frontend_success_url: str,
    frontend_cancel_url: str,
) -> str:
    if not converge_hpp_configured():
        raise RuntimeError("Converge HPP credentials are not configured.")

    billing = billing_address_details or {}
    name = _clean(billing.get("name"))
    first_name = ""
    last_name = ""
    if name:
        parts = name.split()
        first_name = parts[0]
        last_name = " ".join(parts[1:])

    payload = {
        **converge_credentials(),
        "ssl_transaction_type": "ccsale",
        "ssl_amount": f"{float(amount):.2f}",
        "ssl_invoice_number": local_order_id[:25],
        "ssl_merchant_txn_id": local_order_id[:50],
        "ssl_email": _clean(customer_email or billing.get("email")),
        "ssl_first_name": first_name,
        "ssl_last_name": last_name,
        "ssl_avs_address": _clean(billing.get("line1"))[:30],
        "ssl_city": _clean(billing.get("city")),
        "ssl_state": _clean(billing.get("prov_state") or billing.get("state")),
        "ssl_avs_zip": _clean(billing.get("postal_code") or billing.get("zip")),
        "ssl_country": _clean(billing.get("country")),
        "ssl_result_format": "HTML",
        "ssl_receipt_link_method": "REDG",
        "ssl_receipt_link_url": frontend_success_url[:255],
        "ssl_error_url": frontend_cancel_url[:255],
    }
    payload = {key: value for key, value in payload.items() if _clean(value)}

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            converge_hpp_token_url(),
            data=payload,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/plain",
            },
        )

    token = (response.text or "").strip()
    if response.status_code == 403:
        raise RuntimeError(
            "Converge rejected the token request with 403 Forbidden. "
            "Check that the Hosted API user is enabled, the server IP is whitelisted in Converge, "
            "and the credentials match the selected demo/production endpoint."
        )
    if response.status_code != 200 or not token:
        raise RuntimeError(f"Converge token error: {response.status_code} {token}")
    if token.lower().startswith(("error", "invalid", "unauthorized")):
        raise RuntimeError(f"Converge token error: {token}")

    return token


async def query_converge_transaction(txn_id: Optional[str]) -> Optional[dict]:
    txn_id = _clean(txn_id)
    if not txn_id or not converge_hpp_configured():
        return None

    fields = {
        **converge_credentials(),
        "ssl_transaction_type": "txnquery",
        "ssl_txn_id": txn_id,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            converge_xml_url(),
            data={"xmldata": _xml_from_fields(fields)},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "text/xml",
            },
        )

    if response.status_code != 200:
        return None

    data = _parse_xml_response(response.text)
    if not data:
        return None

    data["id"] = data.get("ssl_txn_id") or txn_id
    data["amount"] = data.get("ssl_amount")
    data["status"] = "APPROVED" if data.get("ssl_result") == "0" else "DECLINED"
    data["state"] = data["status"]
    return data
