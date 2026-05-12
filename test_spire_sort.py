import asyncio
from app.services.spire_client import spire_client

async def main():
    try:
        res = await spire_client.get_products(limit=4, sort="-created")
        print(res)
    except Exception as e:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
