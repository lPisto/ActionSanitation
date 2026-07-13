"""Audit which Spire items marked "Upload to Web" are NOT shown on the website, and why.

Run from the Backend/ folder:  python scripts/audit_hidden_products.py

It pulls the full catalog from Spire, keeps only items with upload=True (the ones the
client expects on the site), then reports each one the website hides and the reason,
so we can reconcile counts like "Spire 1634 vs website 1578".
"""
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.services.spire_client import spire_client
from app.services.product_rules import (
    clean_dangerous_good_marker,
    product_is_active,
    product_upload_is_enabled,
)


def exclusion_reason(p: dict) -> str:
    """Return why the website hides this product, or '' if it is shown."""
    if not product_is_active(p):
        return "inactive in Spire (warehouse status != A)"

    description = p.get("inventory", {}).get("description") or p.get("description", "")
    low = str(description).lower()
    compact = low.replace(" ", "")
    if "discontinued" in low:
        return "description contains 'discontinued'"
    if "do not use" in low:
        return "description contains 'do not use'"
    if "use part" in low:
        return "superseded ('use part …')"
    if re.search(r"\bdisc'?d\b", low):
        return "description contains 'disc'd' (discontinued)"
    if "sample" in low:
        return "description contains 'sample'"
    if "500ml" in compact:
        return "description contains '500ml'"

    price = p.get("price")
    if price is None:
        sell = p.get("pricing", {}).get("sellPrice")
        if isinstance(sell, list) and sell:
            price = sell[0]
        else:
            price = sell
    try:
        if price is None or float(price) <= 0:
            return f"price is 0/empty ({price})"
    except (ValueError, TypeError):
        return f"price not numeric ({price})"

    return ""


async def main():
    res = await spire_client.get_products(limit=0)
    records = res.get("records", []) if isinstance(res, dict) else (res or [])

    uploadable = [p for p in records if isinstance(p, dict) and product_upload_is_enabled(p)]
    hidden = []
    for p in uploadable:
        reason = exclusion_reason(p)
        if reason:
            hidden.append((p.get("partNo") or p.get("id"), (p.get("description") or "").strip(), reason))

    print(f"Total Spire records:        {len(records)}")
    print(f"Marked Upload to Web:       {len(uploadable)}")
    print(f"Shown on website:           {len(uploadable) - len(hidden)}")
    print(f"Hidden by website filters:  {len(hidden)}")
    print("-" * 80)

    from collections import Counter
    reason_counts = Counter(r for _, _, r in hidden)
    for reason, count in reason_counts.most_common():
        print(f"  {count:4d}  {reason}")
    print("-" * 80)

    for part_no, desc, reason in sorted(hidden, key=lambda x: x[2]):
        print(f"{str(part_no):12s} | {desc[:45]:45s} | {reason}")


if __name__ == "__main__":
    asyncio.run(main())
