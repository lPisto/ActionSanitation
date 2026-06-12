import re
from typing import Any, Optional


DG_NAME_RE = re.compile(r"(?<![a-z0-9])DG", re.IGNORECASE)
DG_MARKER_RE = re.compile(r"(?:\*+\s*)?\bDG\b(?:\s*\*+)?", re.IGNORECASE)


def get_field(data: Any, key: str, default: Any = None) -> Any:
    if isinstance(data, dict):
        return data.get(key, default)
    return getattr(data, key, default)


def nested_inventory(data: Any) -> dict:
    inv = get_field(data, "inventory", {})
    return inv if isinstance(inv, dict) else {}


def clean_dangerous_good_marker(value: Any) -> str:
    original = str(value or "").strip()
    if not original:
        return ""

    cleaned = DG_MARKER_RE.sub(" ", original)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([,.;:/)\]])", r"\1", cleaned)
    cleaned = re.sub(r"([(/[])\s+", r"\1", cleaned)
    cleaned = re.sub(r"^[\s\-:|,/]+|[\s\-:|,/]+$", "", cleaned).strip()
    cleaned = re.sub(r"\s*-\s*(?=-|$)", "", cleaned).strip()
    return cleaned or original


def product_name_values(product_data: Any) -> list[str]:
    inv = nested_inventory(product_data)
    values = [
        get_field(product_data, "title"),
        get_field(product_data, "name"),
        get_field(product_data, "description"),
        get_field(product_data, "short_description"),
        get_field(inv, "description"),
    ]
    return [str(value) for value in values if value not in (None, "")]


def product_code_values(product_data: Any) -> list[str]:
    inv = nested_inventory(product_data)
    values = [
        get_field(product_data, "partNo"),
        get_field(product_data, "sku"),
        get_field(product_data, "product_id"),
        get_field(product_data, "code"),
        get_field(product_data, "id"),
        get_field(inv, "partNo"),
        get_field(inv, "sku"),
        get_field(inv, "code"),
        get_field(inv, "id"),
    ]
    return [str(value) for value in values if value not in (None, "")]


def product_is_dangerous_good(product_data: Any, metadata: Optional[dict] = None) -> bool:
    if metadata and bool(metadata.get("is_dangerous_good")):
        return True
    if bool(get_field(product_data, "is_dangerous_good", False)):
        return True

    if any(DG_NAME_RE.search(name) for name in product_name_values(product_data)):
        return True

    return any("DG" in code.upper() for code in product_code_values(product_data))


def parse_optional_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)

    normalized = str(value).strip().lower()
    if normalized in {"true", "1", "yes", "y", "t"}:
        return True
    if normalized in {"false", "0", "no", "n", "f"}:
        return False
    return None


def product_upload_is_enabled(product_data: Any) -> bool:
    inv = nested_inventory(product_data)
    for value in (get_field(product_data, "upload"), get_field(inv, "upload")):
        parsed = parse_optional_bool(value)
        if parsed is not None:
            return parsed
    return True
