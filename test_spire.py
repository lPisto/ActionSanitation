import asyncio
import json
import httpx
from app.services.spire_client import spire_client

async def fetch_images(client, item_id):
    try:
        url = f"{spire_client.base_url}inventory/items/{item_id}/images/"
        resp = await client.get(url, headers=spire_client.auth_header)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("count", 0) > 0:
                return item_id, data
    except Exception:
        pass
    return item_id, None

async def main():
    try:
        # Get up to 1000 items
        res = await spire_client._request("GET", "inventory/items/", params={"limit": 500})
        items = res.get("records", [])
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = [fetch_images(client, item.get("id")) for item in items]
            results = await asyncio.gather(*tasks)
            
            for item_id, images_data in results:
                if images_data:
                    print(f"Item {item_id} has images:")
                    print(json.dumps(images_data, indent=2))
                    return
        print("No images found in 500 items.")
    except Exception as e:
        print("Error:", e)

asyncio.run(main())
