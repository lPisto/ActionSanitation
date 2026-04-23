import asyncio
import httpx
from app.core.config import settings
import base64

async def main():
    base_url = settings.SPIRE_BASE_URL
    username = settings.SPIRE_USERNAME
    password = settings.SPIRE_PASSWORD
    credentials = f"{username}:{password}"
    encoded_credentials = base64.b64encode(credentials.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_credentials}"}

    async with httpx.AsyncClient() as client:
        test_id = "WA0E6AA800"
        res = await client.get(f"{base_url}customers/", headers=headers, params={"filter": f'{{"customerNo": "{test_id}"}}'})
        print("GET Status:", res.status_code)
        print("GET Response:", res.text)

if __name__ == "__main__":
    asyncio.run(main())