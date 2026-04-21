import httpx
from fastapi import HTTPException
from app.core.config import settings
import base64

class SpireClient:
    def __init__(self):
        self.base_url = settings.SPIRE_BASE_URL
        self.username = settings.SPIRE_USERNAME
        self.password = settings.SPIRE_PASSWORD
        self.auth_header = self._get_auth_header()

    def _get_auth_header(self):
        credentials = f"{self.username}:{self.password}"
        encoded_credentials = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded_credentials}"}

    async def _request(self, method: str, endpoint: str, **kwargs):
        url = f"{self.base_url}{endpoint}"
        headers = kwargs.get("headers", {})
        headers.update(self.auth_header)
        kwargs["headers"] = headers

        async with httpx.AsyncClient() as client:
            try:
                response = await client.request(method, url, **kwargs)
                response.raise_for_status()
                return response.json() if response.text else {}
            except httpx.HTTPStatusError as e:
                # Log error or handle specific Spire API errors here
                raise HTTPException(status_code=e.response.status_code, detail=f"Spire API error: {e.response.text}")
            except httpx.RequestError as e:
                raise HTTPException(status_code=500, detail=f"Failed to connect to Spire API: {str(e)}")

    async def get_products(self, limit: int = 100, start: int = 0):
        # Update endpoint path based on Spire API documentation
        return await self._request("GET", "inventory/items/", params={"limit": limit, "start": start})

    async def get_product(self, product_id: str):
        return await self._request("GET", f"inventory/items/{product_id}")

    async def get_customer(self, customer_no: str):
        return await self._request("GET", f"customers/{customer_no}")

    async def create_customer(self, customer_data: dict):
        return await self._request("POST", "customers/", json=customer_data)
        
    async def update_customer(self, customer_no: str, customer_data: dict):
        return await self._request("PUT", f"customers/{customer_no}", json=customer_data)

    async def get_customer_pricing(self, customer_no: str, product_id: str):
        # Spire stores customer special pricing in the Inventory Price Matrix
        # You can filter by customer_no and part_no to get the specific negotiated price
        return await self._request("GET", "inventory/price_matrix/", params={"customer_no": customer_no, "part_no": product_id})
        
    async def create_sales_order(self, order_data: dict):
        return await self._request("POST", "sales/orders/", json=order_data)
        
    async def get_customer_orders(self, customer_no: str):
        return await self._request("GET", "sales/orders/").filter(lambda o: o.get("customer", {}).get("customerNo") == customer_no)

spire_client = SpireClient()
