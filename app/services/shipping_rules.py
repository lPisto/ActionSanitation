import os
from typing import Any

from app.services.product_rules import get_field, product_is_dangerous_good


BASE_SHIPPING_COST = float(os.getenv("BASE_SHIPPING_COST", "20"))
FREE_SHIPPING_SUBTOTAL = float(os.getenv("FREE_SHIPPING_SUBTOTAL", "250"))
DANGEROUS_GOODS_SHIPPING_SURCHARGE = float(os.getenv("DANGEROUS_GOODS_SHIPPING_SURCHARGE", "20"))
BESTWAY_SMALL_ORDER_FEE = float(os.getenv("BESTWAY_SMALL_ORDER_FEE", "15"))
BESTWAY_FREE_SHIP_CODE = "BESTWAYC"
BESTWAY_STANDARD_SHIP_CODE = "BESTWAY"


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


def calculate_shipping_breakdown(
    subtotal: Any,
    items: list[Any],
    shipping_method: str | None = None,
    free_delivery: bool = False,
    ship_code: str | None = None,
) -> dict:
    dangerous_goods = has_dangerous_goods(items)
    normalized_ship_code = normalize_ship_code(ship_code)

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
        }

    subtotal_value = parse_float(subtotal)
    is_free_action_delivery = free_delivery or normalized_ship_code == BESTWAY_FREE_SHIP_CODE
    is_bestway = normalized_ship_code == BESTWAY_STANDARD_SHIP_CODE
    is_known_non_bestway = bool(normalized_ship_code) and normalized_ship_code not in {
        BESTWAY_FREE_SHIP_CODE,
        BESTWAY_STANDARD_SHIP_CODE,
    }

    checkout_notice = ""
    shipping_pending_confirmation = False

    if is_free_action_delivery:
        base_shipping_cost = 0.0
        dangerous_goods_surcharge = 0.0
        checkout_notice = "Complimentary delivery."
    elif is_bestway:
        base_shipping_cost = 0.0 if subtotal_value >= FREE_SHIPPING_SUBTOTAL else BESTWAY_SMALL_ORDER_FEE
        dangerous_goods_surcharge = 0.0
        checkout_notice = "Delivery by Action drivers. No D.G. handling fee applies."
    elif is_known_non_bestway:
        base_shipping_cost = 0.0
        dangerous_goods_surcharge = DANGEROUS_GOODS_SHIPPING_SURCHARGE if dangerous_goods else 0.0
        shipping_pending_confirmation = True
        checkout_notice = (
            "Shipping extra, plus D.G. handling fee may apply. "
            "Confirmation email will follow including shipping cost and D.G. handling fee if applicable."
        )
    else:
        base_shipping_cost = 0.0 if free_delivery or subtotal_value >= FREE_SHIPPING_SUBTOTAL else BASE_SHIPPING_COST
        dangerous_goods_surcharge = DANGEROUS_GOODS_SHIPPING_SURCHARGE if dangerous_goods else 0.0

    return {
        "shipping_cost": round_money(base_shipping_cost + dangerous_goods_surcharge),
        "base_shipping_cost": round_money(base_shipping_cost),
        "dangerous_goods_surcharge": round_money(dangerous_goods_surcharge),
        "has_dangerous_goods": dangerous_goods,
        "ship_code": normalized_ship_code,
        "shipping_pending_confirmation": shipping_pending_confirmation,
        "checkout_notice": checkout_notice,
        "dangerous_goods_label": (
            f"{DANGEROUS_GOODS_SHIPPING_SURCHARGE:.2f} D.G. Handling Fee"
            if dangerous_goods_surcharge > 0
            else ""
        ),
    }
