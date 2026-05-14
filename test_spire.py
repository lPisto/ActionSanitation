import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from app.services.spire_client import spire_client
from app.api.endpoints.products import is_product_active

async def test():
    res = await spire_client.get_products(limit=0, q="Drain")
    records = res.get('records', [])
    print(f"Total items returned for 'Drain': {len(records)}")
    
    for r in records:
        part_no = r.get('partNo') or r.get('inventory', {}).get('partNo') or r.get('id')
        print(f"Spire ID/PartNo: {part_no} | Name: {r.get('description')} | Active: {is_product_active(r)}")

asyncio.run(test())
