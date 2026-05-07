from fastapi import APIRouter, Depends, Query
from app.services.spire_client import spire_client
from app.api.deps import get_current_user
from app.models.user import UserInDB
from typing import Optional

router = APIRouter()

def is_product_active(product_data: dict) -> bool:
    # For deals (price_matrix), description is often nested
    description = product_data.get("inventory", {}).get("description", "")
    # For direct products (inventory/items), or as a fallback, check root
    if not description:
        description = product_data.get("description", "")
    
    if "*" in description or "discontinued" in description.lower():
        return False
        
    # Extraer el precio dependiendo de si es una oferta o un producto regular de inventario
    price = product_data.get("price")
    if price is None:
        sell_prices = product_data.get("pricing", {}).get("sellPrice")
        
    try:
        numeric_price = float(price) if price is not None else 0.0
    except (ValueError, TypeError):
        numeric_price = 0.0
        
    # Si no tiene precio explícito o es 0, buscamos el precio base del catálogo
    if numeric_price <= 0:
        # En las ofertas (deals), el precio base está anidado en 'inventory'
        sell_prices = product_data.get("inventory", {}).get("pricing", {}).get("sellPrice")
        
        # En los productos normales, está directamente en la raíz
        if not sell_prices:
            sell_prices = product_data.get("pricing", {}).get("sellPrice")
            
        if isinstance(sell_prices, list) and len(sell_prices) > 0:
            price = sell_prices[0]
        else:
            price = sell_prices
        
            
    try:
        if price is not None and float(price) <= 0:
            return False
    except (ValueError, TypeError):
        pass
        
    return True

@router.get("")
@router.get("/")
async def get_products(
    limit: int = 100, 
    start: int = 0, 
    skip: Optional[int] = None, 
    group: Optional[str] = None, 
    department: Optional[str] = None,
    category: Optional[str] = None,
    on_sale: Optional[bool] = None
):
    actual_start = skip if skip is not None else start
    
    if on_sale:
        deals = await spire_client.get_deals()
        # Filter out deals with '*' or 'discontinued' in the description
        deals = [d for d in deals if is_product_active(d)]
        # Pagination for deals
        return {
            "records": deals[actual_start : actual_start + limit],
            "count": len(deals),
            "start": actual_start,
            "limit": limit
        }

    q_param = None
    final_group = None
    final_dept = None
    
    # 1. Handle Department
    if department:
        # If it's a number like "21", it's a Spire exact department code
        if department.isdigit():
            final_dept = department
        # Map known text to exact group (legacy support)
        elif department.lower() == "detailing":
            final_group = "DET"
        elif department.lower() == "sanitation":
            final_group = "SAN"
        else:
            q_param = department

    # 2. Handle Group
    if group:
        # If it's an exact Spire group code (e.g. DET, DG02, SAN)
        if len(group) <= 4 and group.isupper():
            final_group = group
        elif group.lower() == "detailing":
            final_group = "DET"
        elif group.lower() == "sanitation":
            final_group = "SAN"
        else:
            q_param = group

    # 3. Handle Category (always text search)
    if category:
        q_param = category

    # 4. Refine q_param for Spire (first word, handle plurals)
    if q_param:
        first_word = q_param.split()[0]
        
        # Plural mappings
        if first_word.lower() == "accessories":
            q_param = "Accessory"
        elif first_word.lower() == "chemicals":
            q_param = "Chemical"
        elif first_word.lower() == "aerosols":
            q_param = "Aerosol"
        elif first_word.lower() == "brooms":
            q_param = "Broom"
        elif first_word.lower() == "brushes":
            q_param = "Brush"
        else:
            q_param = first_word

    res = await spire_client.get_products(
        limit=limit, 
        start=actual_start, 
        group_no=final_group, 
        department_code=final_dept,
        q=q_param
    )

    # Normalize response to match detail endpoint structure expected by the frontend
    if "records" in res:
        # Filter out records with '*' or 'discontinued' in the description
        res["records"] = [r for r in res["records"] if is_product_active(r)]
        
        for record in res["records"]:
            # Normalize salesDept
            if "salesDepartment" in record and isinstance(record["salesDepartment"], dict):
                code = record["salesDepartment"].get("code")
                record["salesDept"] = int(code) if code and code.isdigit() else record["salesDepartment"].get("id", 0)
            
            # Normalize unitOfMeasures
            measure_code = record.get("sellMeasureCode", "EACH")
            if "uom" in record and record["uom"]:
                record["unitOfMeasures"] = {measure_code: record["uom"]}
            elif "unitOfMeasures" not in record:
                record["unitOfMeasures"] = {measure_code: {"description": measure_code}}
                
            # Normalize pricing
            if "pricing" in record and "sellPrice" in record["pricing"]:
                sell_prices = record["pricing"]["sellPrice"]
                record["pricing"] = {measure_code: {"sellPrices": sell_prices}}

    return res

@router.get("/categories")
async def get_categories():
    # En Spire las categorias suelen ser los "Inventory Groups"
    return await spire_client.get_inventory_groups()
    
@router.get("/departments")
async def get_departments():
    # Alternativamente, departamentos más amplios
    return await spire_client.get_sales_departments()

@router.get("/{product_id}")
async def get_product(product_id: str):
    return await spire_client.get_product(product_id)

@router.get("/{product_id}/pricing")
async def get_special_pricing(product_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Fetch negotiated price for this specific customer
    try:
        pricing = await spire_client.get_customer_pricing(current_user.spire_customer_no, product_id)
        return {"product_id": product_id, "special_price": pricing.get("price"), "currency": pricing.get("currency")}
    except Exception as e:
        # Fallback if no special pricing exists
        return {"product_id": product_id, "special_price": None, "message": "No special pricing found"}

@router.get("/deals/all")
async def get_deals():
    # Obtiene todas las ofertas activas desde Spire
    deals = await spire_client.get_deals()
    deals = [d for d in deals if is_product_active(d)]
    return deals
