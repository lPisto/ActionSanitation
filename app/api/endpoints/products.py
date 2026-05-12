from fastapi import APIRouter, Depends, Query, Response, HTTPException, Request
import base64
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
    
    if "*" in description or "discontinued" in description.lower() or "do not use" in description.lower():
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

def normalize_product_data(record: dict, request: Request = None) -> dict:
    inv = record.get("inventory", {})
    
    # Unificamos ambas listas de imágenes para nunca perder las del inventario base
    item_img_obj = record.get("images")
    inv_img_obj = inv.get("images")
    
    item_images = item_img_obj.get("records", []) if isinstance(item_img_obj, dict) else (item_img_obj if isinstance(item_img_obj, list) else [])
    inv_images = inv_img_obj.get("records", []) if isinstance(inv_img_obj, dict) else (inv_img_obj if isinstance(inv_img_obj, list) else [])
    
    images_list = item_images + inv_images
        
    part_no = record.get("id", record.get("partNo"))
    if not part_no and inv:
        part_no = inv.get("id", inv.get("partNo"))
        
    record["image"] = None
    normalized_images = []
    
    base_url = str(request.base_url).rstrip('/') if request else "http://localhost:8000"
    
    for img_data in images_list:
        img_url = None
        img_id = img_data.get("id")
        if img_id and part_no:
            img_url = f"{base_url}/api/products/{part_no}/image/{img_id}"
        elif img_data.get("url"):
            img_url = img_data.get("url")
            
        if img_url:
            normalized_images.append(img_url)

    if normalized_images:
        record["image"] = normalized_images[0]
    elif part_no:
        # Fallback a la ruta genérica de imagen por si Spire omitió el embed en la lista
        record["image"] = f"{base_url}/api/products/{part_no}/image"
        # También lo agregamos a la lista por si el frontend usa 'images[0]' para mostrar la foto principal
        normalized_images.append(record["image"])
        
    # Sobrescribir "images" con la lista plana de URLs para el frontend
    record["images"] = normalized_images

    # Normalizar departamento
    sales_dept = record.get("salesDepartment") or inv.get("salesDepartment")
    if isinstance(sales_dept, dict):
        code = sales_dept.get("code")
        record["salesDept"] = int(code) if code and code.isdigit() else sales_dept.get("id", 0)
    
    # Normalizar UoM
    measure_code = record.get("sellMeasureCode") or inv.get("sellMeasureCode") or "EACH"
    uom = record.get("uom") or inv.get("uom")
    if uom:
        record["unitOfMeasures"] = {measure_code: uom}
    elif "unitOfMeasures" not in record:
        record["unitOfMeasures"] = {measure_code: {"description": measure_code}}
        
    # Normalizar Precios
    pricing = record.get("pricing") or inv.get("pricing")
    if pricing and "sellPrice" in pricing:
        sell_prices = pricing["sellPrice"]
        record["pricing"] = {measure_code: {"sellPrices": sell_prices}}

    return record

@router.get("")
@router.get("/")
async def get_products(
    request: Request,
    limit: int = 0, 
    start: int = 0, 
    skip: Optional[int] = None, 
    group: Optional[str] = None, 
    department: Optional[str] = None,
    category: Optional[str] = None,
    search: Optional[str] = None,
    on_sale: Optional[bool] = None,
    sort: Optional[str] = None
):
    actual_start = skip if skip is not None else start
    
    if on_sale:
        deals = await spire_client.get_deals()
        # Filter out deals with '*' or 'discontinued' in the description
        deals = [d for d in deals if is_product_active(d)]
        
        for d in deals:
            normalize_product_data(d, request)
            
        # Pagination for deals
        return {
            "records": deals[actual_start : actual_start + limit],
            "count": len(deals),
            "start": actual_start,
            "limit": limit
        }

    q_param = search
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
            if not q_param: q_param = department

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
            if not q_param: q_param = group

    # 3. Handle Category (always text search)
    if category:
        if not q_param: q_param = category

    # 4. Refine q_param for Spire (first word, handle plurals)
    if q_param and not search:
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
        q=q_param,
        sort=sort
    )

    # Normalize response to match detail endpoint structure expected by the frontend
    if "records" in res:
        # Filter out records with '*' or 'discontinued' in the description
        res["records"] = [r for r in res["records"] if is_product_active(r)]
        
        for record in res["records"]:
            normalize_product_data(record, request)

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
async def get_product(product_id: str, request: Request):
    product = await spire_client.get_product(product_id)
    return normalize_product_data(product, request)

@router.get("/{product_id}/image")
@router.get("/{product_id}/image/{image_id}")
async def get_product_image(product_id: str, image_id: Optional[str] = None):
    try:
        product = await spire_client.get_product(product_id)
        inv = product.get("inventory", {})
        
        item_img_obj = product.get("images")
        inv_img_obj = inv.get("images")
        
        item_images = item_img_obj.get("records", []) if isinstance(item_img_obj, dict) else (item_img_obj if isinstance(item_img_obj, list) else [])
        inv_images = inv_img_obj.get("records", []) if isinstance(inv_img_obj, dict) else (inv_img_obj if isinstance(inv_img_obj, list) else [])
        
        all_images = item_images + inv_images
        
        if not all_images:
            raise HTTPException(status_code=404, detail="No image found for this product")
            
        target_image = None
        if image_id:
            target_image = next((img for img in all_images if str(img.get("id")) == str(image_id)), None)
            
        if not target_image:
            target_image = all_images[0]
            
        links = target_image.get("links", {})
        data_url = links.get("data")
        
        if not data_url:
            img_id = target_image.get("id")
            data_url = f"{spire_client.base_url}inventory/items/{product_id}/images/{img_id}/data"
            
        content, content_type = await spire_client.get_image_data_from_url(data_url)
        return Response(
            content=content, 
            media_type=content_type,
            headers={"Cache-Control": "public, max-age=86400"} # Cachear 24 horas en el navegador
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/{product_id}/pricing")
async def get_special_pricing(product_id: str, response: Response, current_user: UserInDB = Depends(get_current_user)):
    # Fetch negotiated price for this specific customer
    # Garantizamos que los precios especiales nunca se guarden en caché para evitar interferir con el carrito o checkout
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    try:
        pricing = await spire_client.get_customer_pricing(current_user.spire_customer_no, product_id)
        return {"product_id": product_id, "special_price": pricing.get("price"), "currency": pricing.get("currency")}
    except Exception as e:
        # Fallback if no special pricing exists
        return {"product_id": product_id, "special_price": None, "message": "No special pricing found"}

@router.get("/deals/all")
async def get_deals(request: Request):
    # Obtiene todas las ofertas activas desde Spire
    deals = await spire_client.get_deals()
    deals = [d for d in deals if is_product_active(d)]
    for d in deals:
        normalize_product_data(d, request)
    return deals
