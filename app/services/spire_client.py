import httpx
from fastapi import HTTPException
from app.core.config import settings
import base64
import json
import re

class SpireClient:
    def __init__(self):
        self.base_url = settings.SPIRE_BASE_URL
        self.username = settings.SPIRE_USERNAME
        self.password = settings.SPIRE_PASSWORD
        self.auth_header = self._get_auth_header()
        self._image_cache = {}
        self._image_cache_keys = []
        self._MAX_IMAGE_CACHE = 200 # Límite de imágenes cacheadas en memoria RAM

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

    async def get_products(self, limit: int = 0, start: int = 0, group_no: str = None, department_code: str = None, q: str = None, sort: str = None):
        params = {"limit": limit, "start": start, "embed": ["images", "inventory.images"]}
        if q:
            params["q"] = q
        if sort:
            params["sort"] = sort
            
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
        return await self._request("GET", f"inventory/items/{product_id}", params={"embed": ["images", "inventory.images"]})

    async def get_image_data_from_url(self, url: str):
        if url in self._image_cache:
            return self._image_cache[url]
            
        headers = self.auth_header.copy()
        async with httpx.AsyncClient(follow_redirects=True) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                result = (response.content, response.headers.get("Content-Type", "image/jpeg"))
                
                if url not in self._image_cache:
                    self._image_cache[url] = result
                    self._image_cache_keys.append(url)
                    if len(self._image_cache_keys) > self._MAX_IMAGE_CACHE:
                        oldest = self._image_cache_keys.pop(0)
                        self._image_cache.pop(oldest, None)
                        
                return result
            except httpx.HTTPStatusError as e:
                raise HTTPException(status_code=e.response.status_code, detail="Spire API error fetching image")
            except httpx.RequestError as e:
                raise HTTPException(status_code=500, detail="Failed to connect to Spire API for image")

    async def get_customer(self, customer_no: str):
        filter_query = json.dumps({"customerNo": customer_no})
        res = await self._request("GET", "customers/", params={"filter": filter_query})
        records = res.get("records", [])
        if not records:
            raise HTTPException(status_code=404, detail="Customer not found in Spire")
        return records[0]

    @staticmethod
    def _normalize_email(email: str) -> str:
        return str(email or "").strip().lower()

    @classmethod
    def _extract_emails(cls, value):
        emails = []
        if isinstance(value, dict):
            for key, nested in value.items():
                key_name = str(key).lower()
                if key_name in {"email", "emailaddress", "email_address"}:
                    emails.append(nested)
                if isinstance(nested, (dict, list)):
                    emails.extend(cls._extract_emails(nested))
        elif isinstance(value, list):
            for item in value:
                emails.extend(cls._extract_emails(item))
        return emails

    @classmethod
    def _record_matches_email(cls, record: dict, email: str) -> bool:
        target = cls._normalize_email(email)
        for candidate in cls._extract_emails(record):
            parts = re.split(r"[\s,;]+", str(candidate or ""))
            if target in {cls._normalize_email(part) for part in parts}:
                return True
        return False

    async def get_customer_by_email(self, email: str):
        normalized_email = self._normalize_email(email)
        res = await self._request("GET", "customers/", params={"q": normalized_email, "limit": 100})
        for record in res.get("records", []):
            if self._record_matches_email(record, normalized_email):
                return record
        return None

    async def create_customer(self, customer_data: dict):
        return await self._request("POST", "customers/", json=customer_data)
        
    async def update_customer(self, customer_no: str, customer_data: dict):
        customer = await self.get_customer(customer_no)
        customer_id = customer.get("id")
        return await self._request("PUT", f"customers/{customer_id}", json=customer_data)

    async def get_customer_pricing(self, customer_no: str, product_id: str):
        # Spire stores customer special pricing in the Inventory Price Matrix
        # You can filter by customer_no and part_no to get the specific negotiated price
        filter_query = json.dumps({"customerNo": customer_no, "partNo": product_id})
        return await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query})

    async def get_customer_all_pricing(self, customer_no: str):
        # Obtiene todas las reglas de precios especiales para un cliente
        filter_query = json.dumps({"customerNo": customer_no})
        res = await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query, "limit": 0})
        pricing_map = {}
        for record in res.get("records", []):
            part_no = record.get("partNo")
            amount = record.get("amount")
            if amount is None:
                amount = record.get("price")
            if part_no and amount is not None:
                pricing_map[part_no] = float(amount)
        return pricing_map

    async def get_deals(self):
        # En Spire, las ofertas globales se guardan en la Price Matrix con el customerNo en null
        from datetime import datetime
        current_date = datetime.now().strftime("%Y-%m-%d")
        
        # Filtramos ofertas donde el cliente sea nulo (aplica a todos) y que la fecha no esté vencida
        # Spire API no soporta facilmente filtros OR de fecha nula o mayor en la URL, 
        # así que traemos las globales y filtramos en Python por simplicidad o usamos un filtro básico.
        filter_query = json.dumps({"customerNo": None})
        res = await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query, "limit": 0, "embed": "inventory.images"})
        
        valid_deals = []
        for record in res.get("records", []):
            end_date = record.get("endDate")
            # Si no tiene fecha de fin, o la fecha de fin es igual o posterior a hoy o tiene precio $0, es una oferta válida
            try:
                price = float(record.get("price", 0))
                if price <= 0:
                    continue
            except (ValueError, TypeError):
                continue
                
            if not end_date or end_date >= current_date:
                valid_deals.append(record)
                
        return valid_deals
        
    async def create_sales_order(self, order_data: dict):
        return await self._request("POST", "sales/orders/", json=order_data)
        
    async def get_customer_orders(self, customer_no: str):
        # Alternativa: Usamos el buscador global "q" para evitar los errores 500 del parámetro "filter"
        res = await self._request("GET", "sales/orders/", params={"q": customer_no, "limit": 0})
        
        # Filtramos internamente con Python para garantizar que coincida el código de cliente exacto
        filtered_records = [
            order for order in res.get("records", [])
            if order.get("customer", {}).get("customerNo") == customer_no
        ]
        res["records"] = filtered_records
        return res

    async def get_sales_order(self, order_id: str):
        return await self._request("GET", f"sales/orders/{order_id}")

    async def get_sales_order_invoice(self, order_id: str):
        return await self._request("GET", f"sales/orders/{order_id}/invoice")

spire_client = SpireClient()
