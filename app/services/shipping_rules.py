import os
from typing import Any

from app.services.product_rules import get_field, product_is_dangerous_good


BASE_SHIPPING_COST = float(os.getenv("BASE_SHIPPING_COST", "20"))
FREE_SHIPPING_SUBTOTAL = float(os.getenv("FREE_SHIPPING_SUBTOTAL", "250"))
DANGEROUS_GOODS_SHIPPING_SURCHARGE = float(os.getenv("DANGEROUS_GOODS_SHIPPING_SURCHARGE", "20"))


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


def calculate_shipping_breakdown(
    subtotal: Any,
    items: list[Any],
    shipping_method: str | None = None,
) -> dict:
    if str(shipping_method or "").lower() == "pickup":
        return {
            "shipping_cost": 0.0,
            "base_shipping_cost": 0.0,
            "dangerous_goods_surcharge": 0.0,
            "has_dangerous_goods": has_dangerous_goods(items),
        }

    subtotal_value = parse_float(subtotal)
    base_shipping_cost = 0.0 if subtotal_value >= FREE_SHIPPING_SUBTOTAL else BASE_SHIPPING_COST
    dangerous_goods = has_dangerous_goods(items)
    dangerous_goods_surcharge = DANGEROUS_GOODS_SHIPPING_SURCHARGE if dangerous_goods else 0.0

    return {
        "shipping_cost": round_money(base_shipping_cost + dangerous_goods_surcharge),
        "base_shipping_cost": round_money(base_shipping_cost),
        "dangerous_goods_surcharge": round_money(dangerous_goods_surcharge),
        "has_dangerous_goods": dangerous_goods,
    }
