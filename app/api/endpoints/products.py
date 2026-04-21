from fastapi import APIRouter, Depends, Query
from app.services.spire_client import spire_client
from app.api.deps import get_current_user
from app.models.user import UserInDB
from typing import Optional

router = APIRouter()

@router.get("/")
async def get_products(limit: int = 100, start: int = 0, group: Optional[str] = None, department: Optional[str] = None):
    return await spire_client.get_products(limit=limit, start=start, group_no=group, department_code=department)

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
    return deals
