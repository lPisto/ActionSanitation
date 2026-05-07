import asyncio
from app.services.spire_client import spire_client

async def main():
    try:
        res = await spire_client._request("GET", "inventory/items/1", params={"embed": "images"})
        print(res)
    except Exception as e:
        print("Error:", e)

asyncio.run(main())