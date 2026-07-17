import httpx
from fastapi import HTTPException
from app.core.config import settings
import base64
import json
import os
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

    @staticmethod
    def _parse_bool(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        normalized = str(value or "").strip().lower()
        return normalized in {"1", "true", "yes", "y", "on", "free"}

    @staticmethod
    def _field_names_from_env(env_name: str, defaults: list[str]) -> list[str]:
        raw = os.getenv(env_name, "")
        values = [part.strip() for part in raw.split(",") if part.strip()]
        return values or defaults

    @classmethod
    def _lookup_named_value(cls, data, names: list[str]):
        target_names = {name.lower() for name in names if name}
        if not target_names:
            return None

        if isinstance(data, dict):
            for key, value in data.items():
                if str(key).lower() in target_names:
                    return value

            custom_containers = (
                "customFields",
                "custom_fields",
                "userDefined",
                "userDefinedFields",
                "user_def",
                "udf",
                "UDF",
            )
            for container in custom_containers:
                if container in data:
                    found = cls._lookup_named_value(data.get(container), names)
                    if found is not None:
                        return found

            for value in data.values():
                if isinstance(value, (dict, list)):
                    found = cls._lookup_named_value(value, names)
                    if found is not None:
                        return found

        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    labels = [
                        item.get("name"),
                        item.get("code"),
                        item.get("key"),
                        item.get("id"),
                        item.get("label"),
                    ]
                    if any(str(label).lower() in target_names for label in labels if label):
                        for value_key in ("value", "contents", "data", "text", "boolean"):
                            if value_key in item:
                                return item.get(value_key)
                    found = cls._lookup_named_value(item, names)
                    if found is not None:
                        return found

        return None

    def customer_has_free_delivery(self, customer: dict) -> bool:
        field_names = self._field_names_from_env(
            "SPIRE_FREE_DELIVERY_FIELDS",
            [
                "freeDelivery",
                "free_delivery",
                "webFreeDelivery",
                "freeFreight",
                "free_freight",
                "noFreight",
                "no_freight",
            ],
        )
        return self._parse_bool(self._lookup_named_value(customer or {}, field_names))

    def customer_payment_terms(self, customer: dict) -> str:
        terms = (customer or {}).get("paymentTerms")
        if isinstance(terms, dict):
            return str(terms.get("description") or terms.get("code") or "").strip()
        return str(terms or "").strip()

    def customer_terms_are_cod(self, customer: dict) -> bool:
        """True when the customer's Spire terms mean payment is due on delivery/receipt
        (COD, cash on delivery, due/upon receipt, prepaid) rather than net/on-account."""
        terms = self.customer_payment_terms(customer).lower()
        if not terms:
            return False
        return any(
            keyword in terms
            for keyword in ("cod", "c.o.d", "cash on delivery", "on delivery", "on receipt", "upon receipt", "prepaid")
        )

    def customer_ship_code(self, customer: dict) -> str:
        value = self._lookup_named_value(
            customer or {},
            ["shipCode", "ship_code", "shippingCode", "shipping_code"],
        )
        if isinstance(value, dict):
            value = value.get("code") or value.get("id") or value.get("name")
        return str(value or "").strip().upper()

    async def get_customer_free_delivery(self, customer_no: str) -> bool:
        if not customer_no:
            return False
        customer = await self.get_customer(customer_no)
        return self.customer_has_free_delivery(customer)

    async def get_customer_ship_code(self, customer_no: str) -> str:
        if not customer_no:
            return ""
        customer = await self.get_customer(customer_no)
        return self.customer_ship_code(customer)

    def internal_customer_id_payload(self, internal_customer_id: str) -> dict:
        field_name = os.getenv("SPIRE_INTERNAL_CUSTOMER_ID_FIELD", "").strip()
        if not field_name or not internal_customer_id:
            return {}

        container = os.getenv("SPIRE_CUSTOM_FIELD_CONTAINER", "customFields").strip() or "customFields"
        return {container: {field_name: str(internal_customer_id)}}

    async def create_customer(self, customer_data: dict):
        return await self._request("POST", "customers/", json=customer_data)
        
    async def update_customer(self, customer_no: str, customer_data: dict):
        customer = await self.get_customer(customer_no)
        customer_id = customer.get("id")
        return await self._request("PUT", f"customers/{customer_id}", json=customer_data)

    async def get_customer_addresses(self, customer_no: str) -> list:
        """All ship-to addresses for a customer (used by consolidated dealer accounts
        that have one billing account and many delivery locations)."""
        if not customer_no:
            return []
        customer = await self.get_customer(customer_no)
        customer_id = customer.get("id")
        if not customer_id:
            return []
        res = await self._request("GET", f"customers/{customer_id}/addresses/", params={"limit": 0})
        records = res.get("records", []) if isinstance(res, dict) else (res or [])
        addresses = []
        for record in records:
            if not isinstance(record, dict):
                continue
            addresses.append({
                "id": record.get("id"),
                "ship_id": (record.get("shipId") or "").strip(),
                "name": (record.get("name") or "").strip(),
                "line1": (record.get("line1") or "").strip(),
                "line2": (record.get("line2") or "").strip(),
                "city": (record.get("city") or "").strip(),
                "prov_state": (record.get("provState") or "").strip(),
                "postal_code": (record.get("postalCode") or "").strip(),
                "country": (record.get("country") or "").strip(),
                "email": (record.get("email") or "").strip(),
            })
        return addresses

    async def get_customer_pricing(self, customer_no: str, product_id: str):
        # Spire stores customer special pricing in the Inventory Price Matrix
        # You can filter by customer_no and part_no to get the specific negotiated price
        filter_query = json.dumps({"customerNo": customer_no, "partNo": product_id})
        return await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query})

    @staticmethod
    def price_from_rule(cost, rule) -> "float | None":
        """Compute a price from the customer's price-matrix rule and the item cost.

        Margin:  price = cost / (1 - margin%/100)
        Markup:  price = cost * (1 + markup%/100)
        """
        if not rule:
            return None
        try:
            cost = float(cost)
        except (TypeError, ValueError):
            return None
        if cost <= 0:
            return None
        try:
            value = float(rule.get("value") or 0.0)
        except (TypeError, ValueError):
            value = 0.0
        rule_type = rule.get("type")
        if rule_type == "M":  # Margin
            if value >= 100:
                return None
            return round(cost / (1.0 - value / 100.0), 2)
        if rule_type == "K":  # Markup
            return round(cost * (1.0 + value / 100.0), 2)
        return None

    @staticmethod
    def _extract_customer_price_rule(records) -> "dict | None":
        """A customer-wide price rule (no part / product code / group) applies to ALL
        of that customer's items — e.g. 'Margin X% over current cost'."""
        for record in records or []:
            if not isinstance(record, dict):
                continue
            if record.get("partNo") or record.get("productCode") or record.get("inventoryGroupNo"):
                continue
            amount_type = str(record.get("amountType") or "").strip().upper()
            if amount_type not in ("M", "K"):  # M = Margin, K = Markup
                continue
            try:
                value = float(record.get("amount") or 0)
            except (TypeError, ValueError):
                value = 0.0
            return {
                "type": amount_type,
                "value": value,
                "cost_method": str(record.get("costMethod") or "C").strip().upper(),
            }
        return None

    async def get_customer_pricing_context(self, customer_no: str) -> dict:
        """Returns {"fixed": {partNo: price}, "rule": <customer-wide margin/markup rule>}."""
        if not customer_no:
            return {"fixed": {}, "rule": None}
        filter_query = json.dumps({"customerNo": customer_no})
        res = await self._request("GET", "inventory/price_matrix/", params={"filter": filter_query, "limit": 0})
        records = res.get("records", []) if isinstance(res, dict) else []
        fixed = {}
        for record in records:
            part_no = record.get("partNo")
            if not part_no:
                continue
            amount_type = str(record.get("amountType") or "").strip().upper()
            if amount_type in ("M", "K"):
                continue  # per-part margin/markup not handled as a fixed price
            amount = record.get("amount")
            if amount is None:
                amount = record.get("price")
            if amount is not None:
                try:
                    fixed[part_no] = float(amount)
                except (TypeError, ValueError):
                    pass
        return {"fixed": fixed, "rule": self._extract_customer_price_rule(records)}

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
        
    async def get_item_stock_info(self, part_no: str, preferred_warehouse: str = "00") -> "dict | None":
        """Stock info for a part across ALL warehouses.

        Returns {"total": float, "warehouses": {whse: qty}, "fulfill_whse": code|None}
        where fulfill_whse is the warehouse to ship from: the preferred one if it has
        stock, otherwise the warehouse with the most available stock. Returns None if
        the part isn't found anywhere.
        """
        if not part_no:
            return None
        try:
            res = await self._request("GET", "inventory/items/", params={"q": part_no, "limit": 0})
        except Exception:
            return None
        records = res.get("records", []) if isinstance(res, dict) else []
        matched = [r for r in records if str(r.get("partNo")) == str(part_no)]
        if not matched:
            return None

        warehouses: dict = {}
        allow_backorder = False
        for record in matched:
            whse = str(record.get("whse") or "")
            try:
                qty = float(record.get("availableQty") or 0)
            except (TypeError, ValueError):
                qty = 0.0
            warehouses[whse] = warehouses.get(whse, 0.0) + qty
            if self._parse_bool(record.get("allowBackorders") if record.get("allowBackorders") is not None else record.get("allowBackOrders")):
                allow_backorder = True

        total = sum(warehouses.values())
        fulfill_whse = None
        if warehouses.get(preferred_warehouse, 0.0) > 0:
            fulfill_whse = preferred_warehouse
        else:
            in_stock = {w: q for w, q in warehouses.items() if q > 0}
            if in_stock:
                fulfill_whse = max(in_stock, key=in_stock.get)
        return {"total": total, "warehouses": warehouses, "fulfill_whse": fulfill_whse, "allow_backorder": allow_backorder}

    async def get_item_availability(self, part_no: str, warehouse: str = None):
        """Total available across all warehouses (or a specific one). None if not found."""
        info = await self.get_item_stock_info(part_no)
        if info is None:
            return None
        if warehouse:
            return info["warehouses"].get(str(warehouse))
        return info["total"]

    async def create_sales_order(self, order_data: dict):
        return await self._request("POST", "sales/orders/", json=order_data)

    async def create_sales_order_note(self, order_id: str, note_data: dict):
        return await self._request("POST", f"sales/orders/{order_id}/notes/", json=note_data)
        
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
