import os
from typing import Any

from app.services.product_rules import get_field, product_is_dangerous_good


BASE_SHIPPING_COST = float(os.getenv("BASE_SHIPPING_COST", "20"))
FREE_SHIPPING_SUBTOTAL = float(os.getenv("FREE_SHIPPING_SUBTOTAL", "200"))
DANGEROUS_GOODS_SHIPPING_SURCHARGE = float(os.getenv("DANGEROUS_GOODS_SHIPPING_SURCHARGE", "20"))
BESTWAY_SMALL_ORDER_FEE = float(os.getenv("BESTWAY_SMALL_ORDER_FEE", "15"))
LOCAL_DELIVERY_FREE_SUBTOTAL = float(os.getenv("LOCAL_DELIVERY_FREE_SUBTOTAL", "200"))
LOCAL_DELIVERY_SMALL_ORDER_FEE = float(os.getenv("LOCAL_DELIVERY_SMALL_ORDER_FEE", "15"))
OUTSIDE_DELIVERY_DG_FLAT_FEE = float(os.getenv("OUTSIDE_DELIVERY_DG_FLAT_FEE", str(DANGEROUS_GOODS_SHIPPING_SURCHARGE)))
BESTWAY_FREE_SHIP_CODE = "BESTWAYC"
BESTWAY_STANDARD_SHIP_CODE = "BESTWAY"
DEFAULT_LOCAL_DELIVERY_FSAS = """
L8E L8G L8H L8J L8K L8L L8M L8N L8P L8R L8S L8T L8V L8W L9A L9B L9C L9G L9H L8B
L7L L7M L7N L7P L7R L7S L7T L7V L6H L6J L6K L6L L6M L6N L6P L6R L9T L7G
L4T L4V L4W L4X L4Y L4Z L5A L5B L5C L5E L5G L5H L5J L5K L5L L5M L5N L5P L5R L5S L5T L5V L5W
N3R N3S N3T N3V N3L
N1P N1R N1S N1T N2A N2B N2C N2E N2G N2H N2J N2K N2L N2M N2N N2P N3C N3E N3H
N1C N1E N1G N1H N1K N0B
L0S L2A L2E L2G L2H L2J L2M L2N L2P L2R L2S L2T L2V L2W L3B L3C L3K L3M L3P L3R L3S L3T L0R
N0A N3W N1A
"""


def parse_code_set(value: str) -> set[str]:
    return {part.strip().upper() for part in value.replace(",", " ").split() if part.strip()}


LOCAL_DELIVERY_FSA_CODES = parse_code_set(os.getenv("LOCAL_DELIVERY_FSA_CODES", DEFAULT_LOCAL_DELIVERY_FSAS))
LOCAL_DELIVERY_FSA_CODES |= parse_code_set(os.getenv("LOCAL_DELIVERY_FSA_CODES_ADD", ""))
LOCAL_DELIVERY_FSA_CODES -= parse_code_set(os.getenv("LOCAL_DELIVERY_FSA_CODES_EXCLUDE", ""))


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def round_money(value: Any) -> float:
    return round(parse_float(value), 2)


def item_quantity(item: Any) -> float:
    return parse_float(get_field(item, "quantity", 0))


def item_price(item: Any) -> float:
    return parse_float(get_field(item, "price", get_field(item, "unit_price", 0)))


def items_total(items: list[Any]) -> float:
    return round_money(sum(item_quantity(item) * item_price(item) for item in (items or [])))


def has_dangerous_goods(items: list[Any]) -> bool:
    return any(product_is_dangerous_good(item) for item in (items or []))


def normalize_ship_code(ship_code: Any) -> str:
    return str(ship_code or "").strip().upper()


def normalize_postal_code(postal_code: Any) -> str:
    return "".join(ch for ch in str(postal_code or "").upper() if ch.isalnum())


def postal_code_fsa(postal_code: Any) -> str:
    return normalize_postal_code(postal_code)[:3]


def is_regular_delivery_postal_code(postal_code: Any) -> bool:
    return postal_code_fsa(postal_code) in LOCAL_DELIVERY_FSA_CODES


def is_action_delivery(postal_code: Any, ship_code: Any = None) -> bool:
    normalized_ship_code = normalize_ship_code(ship_code)
    return normalized_ship_code == BESTWAY_FREE_SHIP_CODE or is_regular_delivery_postal_code(postal_code)


def requires_freightcom_quote(
    postal_code: Any,
    shipping_method: str | None = None,
    free_delivery: bool = False,
    ship_code: Any = None,
) -> bool:
    if str(shipping_method or "").lower() == "pickup":
        return False
    normalized_ship_code = normalize_ship_code(ship_code)
    if free_delivery or normalized_ship_code == BESTWAY_FREE_SHIP_CODE:
        return False
    return not is_regular_delivery_postal_code(postal_code)


def calculate_shipping_breakdown(
    subtotal: Any,
    items: list[Any],
    shipping_method: str | None = None,
    free_delivery: bool = False,
    ship_code: str | None = None,
    postal_code: Any = None,
    freightcom_shipping_cost: Any = None,
) -> dict:
    dangerous_goods = has_dangerous_goods(items)
    normalized_ship_code = normalize_ship_code(ship_code)
    is_bestway = normalized_ship_code == BESTWAY_STANDARD_SHIP_CODE
    regular_delivery_area = is_action_delivery(postal_code, normalized_ship_code)
    freight_quote_required = requires_freightcom_quote(postal_code, shipping_method, free_delivery, normalized_ship_code)

    if str(shipping_method or "").lower() == "pickup":
        return {
            "shipping_cost": 0.0,
            "base_shipping_cost": 0.0,
            "dangerous_goods_surcharge": 0.0,
            "has_dangerous_goods": dangerous_goods,
            "ship_code": normalized_ship_code,
            "shipping_pending_confirmation": False,
            "checkout_notice": "",
            "dangerous_goods_label": "",
            "regular_delivery_area": regular_delivery_area,
            "freightcom_quote_required": False,
            "freightcom_shipping_cost": 0.0,
        }

    subtotal_value = parse_float(subtotal)
    is_free_action_delivery = free_delivery or normalized_ship_code == BESTWAY_FREE_SHIP_CODE

    checkout_notice = ""
    shipping_pending_confirmation = False
    freightcom_cost = round_money(freightcom_shipping_cost)

    if is_free_action_delivery:
        base_shipping_cost = 0.0
        dangerous_goods_surcharge = 0.0
        checkout_notice = "Complimentary delivery."
    elif regular_delivery_area:
        local_small_order_fee = BESTWAY_SMALL_ORDER_FEE if is_bestway else LOCAL_DELIVERY_SMALL_ORDER_FEE
        base_shipping_cost = 0.0 if subtotal_value >= LOCAL_DELIVERY_FREE_SUBTOTAL else local_small_order_fee
        dangerous_goods_surcharge = 0.0
        if is_bestway:
            checkout_notice = (
                "Delivery by Action drivers. No D.G. handling fee applies. "
                f"Free delivery applies over ${LOCAL_DELIVERY_FREE_SUBTOTAL:.2f}; "
                f"${local_small_order_fee:.2f} delivery fee under."
            )
        else:
            checkout_notice = (
                "Local Action delivery. Free delivery applies over "
                f"${LOCAL_DELIVERY_FREE_SUBTOTAL:.2f}; ${local_small_order_fee:.2f} delivery fee under."
            )
    elif freight_quote_required and freightcom_cost > 0:
        base_shipping_cost = freightcom_cost
        dangerous_goods_surcharge = OUTSIDE_DELIVERY_DG_FLAT_FEE if dangerous_goods else 0.0
        checkout_notice = "Freightcom shipping quote included."
    else:
        base_shipping_cost = 0.0
        dangerous_goods_surcharge = OUTSIDE_DELIVERY_DG_FLAT_FEE if dangerous_goods else 0.0
        shipping_pending_confirmation = freight_quote_required
        checkout_notice = (
            "Outside our regular delivery area. Freightcom shipping quote required; "
            "please select On Account. Confirmation email will follow including shipping cost"
            " and D.G. handling fee if applicable."
        )

    return {
        "shipping_cost": round_money(base_shipping_cost + dangerous_goods_surcharge),
        "base_shipping_cost": round_money(base_shipping_cost),
        "dangerous_goods_surcharge": round_money(dangerous_goods_surcharge),
        "has_dangerous_goods": dangerous_goods,
        "ship_code": normalized_ship_code,
        "shipping_pending_confirmation": shipping_pending_confirmation,
        "checkout_notice": checkout_notice,
        "regular_delivery_area": regular_delivery_area,
        "freightcom_quote_required": freight_quote_required,
        "freightcom_shipping_cost": freightcom_cost if freight_quote_required else 0.0,
        "dangerous_goods_label": (
            f"{OUTSIDE_DELIVERY_DG_FLAT_FEE:.2f} D.G. Handling Fee"
            if dangerous_goods_surcharge > 0
            else ""
        ),
    }
