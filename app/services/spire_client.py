import httpx
from fastapi import HTTPException
from app.core.config import settings
import base64
import json

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

    async def get_products(self, limit: int = 100, start: int = 0, group_no: str = None, department_code: str = None, q: str = None):
        params = {"limit": limit, "start": start}
        if q:
            params["q"] = q
            
        filters = {}
        if group_no:
            filters["groupNo"] = group_no
        if department_code:
            filters["salesDepartment.code"] = department_code
            
        if filters:
            params["filter"] = json.dumps(filters)

        return await self._request("GET", "inventory/items/", params=params)

    async def get_inventory_groups(self):
        # Often used as the main "Category" in Spire
        return await self._request("GET", "inventory/groups/", params={"limit": 0})
        
    async def get_sales_departments(self):
        # Often used as a higher-level category or department
        return await self._request("GET", "inventory/sales_departments/", params={"limit": 0})

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
        filter_query = json.dumps({"customerNo": customer_no, "partNo": product_id})
        return await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query})

    async def get_deals(self):
        # En Spire, las ofertas globales se guardan en la Price Matrix con el customerNo en null
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Filtramos ofertas donde el cliente sea nulo (aplica a todos) y que la fecha no esté vencida
        # Spire API no soporta facilmente filtros OR de fecha nula o mayor en la URL, 
        # así que traemos las globales y filtramos en Python por simplicidad o usamos un filtro básico.
        filter_query = json.dumps({"customerNo": None})
        res = await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query, "limit": 100})
        
        valid_deals = []
        for record in res.get("records", []):
            end_date = record.get("endDate")
            # Si no tiene fecha de fin, o la fecha de fin es igual o posterior a hoy, es una oferta válida
            if not end_date or end_date >= current_date:
                valid_deals.append(record)
                
        return valid_deals
        
    async def create_sales_order(self, order_data: dict):
        return await self._request("POST", "sales/orders/", json=order_data)
        
    async def get_customer_orders(self, customer_no: str):
        filter_query = json.dumps({"customer.customerNo": customer_no})
        return await self._request("GET", "sales/orders/", params={"filter": filter_query})

    async def get_sales_order(self, order_id: str):
        return await self._request("GET", f"sales/orders/{order_id}")

    async def get_sales_order_invoice(self, order_id: str):
        return await self._request("GET", f"sales/orders/{order_id}/invoice")

spire_client = SpireClient()
