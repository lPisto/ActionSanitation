import asyncio
from app.api.endpoints.products import get_products, Request
import json
from fastapi import Request

async def main():
    try:
        # We need a mock request object for normalize_product_data
        scope = {
            "type": "http",
            "method": "GET",
            "headers": [(b"host", b"localhost:8000")],
            "path": "/api/products",
            "query_string": b"limit=4&sort=-created"
        }
        mock_request = Request(scope)
        res = await get_products(request=mock_request, limit=4, sort="-created")
        print("Success:", len(res.get("records", [])))
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
