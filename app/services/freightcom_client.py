import os
from typing import Any, Optional

import httpx

from app.services.product_rules import get_field
from app.services.shipping_rules import parse_float, round_money


def _first_configured(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _item_payload(item: Any) -> dict:
    quantity = parse_float(get_field(item, "quantity", 1), 1)
    weight = parse_float(get_field(item, "weight_kg", get_field(item, "weight", 0)), 0)
    return {
        "sku": get_field(item, "sku", get_field(item, "product_id", "")),
        "description": get_field(item, "name", ""),
        "quantity": max(quantity, 1),
        "weight_kg": weight if weight > 0 else parse_float(os.getenv("FREIGHTCOM_DEFAULT_ITEM_WEIGHT_KG", "1"), 1),
    }


def _dot_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            index = int(part)
            current = current[index] if index < len(current) else None
        else:
            return None
    return current


def _extract_rate_amount(data: Any) -> float:
    configured_path = os.getenv("FREIGHTCOM_RATE_FIELD", "").strip()
    if configured_path:
        amount = round_money(_dot_path(data, configured_path))
        if amount > 0:
            return amount

    candidate_keys = {
        "shipping_cost",
        "total",
        "totalCharge",
        "total_charge",
        "charge",
        "rate",
        "price",
        "amount",
        "netCharge",
        "net_charge",
    }

    def walk(value: Any) -> Optional[float]:
        if isinstance(value, dict):
            for key, nested in value.items():
                if key in candidate_keys:
                    amount = round_money(nested)
                    if amount > 0:
                        return amount
                found = walk(nested)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = walk(nested)
                if found:
                    return found
        return None

    return walk(data) or 0.0


async def quote_freightcom_shipping(
    postal_code: str,
    items: list[Any],
    subtotal: Any,
) -> Optional[float]:
    rate_url = _first_configured("FREIGHTCOM_RATE_URL", "FREIGHTCOM_QUOTE_URL")
    if not rate_url:
        return None

    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    api_key = _first_configured("FREIGHTCOM_API_KEY", "FREIGHTCOM_TOKEN")
    if api_key:
        auth_scheme = os.getenv("FREIGHTCOM_AUTH_SCHEME", "Bearer").strip()
        headers["Authorization"] = f"{auth_scheme} {api_key}".strip()

    origin_postal_code = os.getenv("FREIGHTCOM_ORIGIN_POSTAL_CODE", "").strip()
    payload = {
        "origin": {"postal_code": origin_postal_code},
        "destination": {"postal_code": postal_code},
        "subtotal": round_money(subtotal),
        "currency": os.getenv("FREIGHTCOM_CURRENCY", "CAD"),
        "items": [_item_payload(item) for item in (items or [])],
    }

    username = os.getenv("FREIGHTCOM_USERNAME", "").strip()
    password = os.getenv("FREIGHTCOM_PASSWORD", "").strip()
    auth = (username, password) if username and password else None

    try:
        async with httpx.AsyncClient(timeout=float(os.getenv("FREIGHTCOM_TIMEOUT_SECONDS", "15"))) as client:
            response = await client.post(rate_url, json=payload, headers=headers, auth=auth)
            response.raise_for_status()
            amount = _extract_rate_amount(response.json())
            return amount if amount > 0 else None
    except Exception as exc:
        print(f"Freightcom quote failed: {exc}")
        return None
