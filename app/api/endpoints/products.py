from fastapi import APIRouter, Depends, Query, Response, HTTPException, Request, Header
import base64
import os
import json
from datetime import datetime
from app.core.config import settings
from app.services.spire_client import spire_client
from app.services.product_rules import (
    clean_dangerous_good_marker,
    clean_text_encoding_artifacts,
    product_is_dangerous_good,
    product_upload_is_enabled,
    text_has_encoding_artifacts,
)
from app.api.deps import get_current_user, get_optional_current_user, get_database
from app.models.user import UserInDB
from typing import Optional, List
from pydantic import BaseModel

router = APIRouter()

def is_product_active(product_data: dict) -> bool:
    if not product_upload_is_enabled(product_data):
        return False

    # For deals (price_matrix), description is often nested
    description = product_data.get("inventory", {}).get("description", "")
    # For direct products (inventory/items), or as a fallback, check root
    if not description:
        description = product_data.get("description", "")
    
    display_description = clean_dangerous_good_marker(description)
    normalized_description = display_description.lower().replace(" ", "")
    description_lower = display_description.lower()
    if (
        "*" in display_description
        or "discontinued" in description_lower
        or "do not use" in description_lower
        or "disc" in description_lower
        or "sample" in description_lower
        or "500ml" in normalized_description
    ):
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

class ProductReviewCreate(BaseModel):
    rating: int
    comment: Optional[str] = None

class ProductCategoryAssignment(BaseModel):
    department: str
    group: str
    item: Optional[str] = None

class ProductCategoryUpdateRequest(BaseModel):
    skus: List[str]
    categories: List[ProductCategoryAssignment]
    replace: bool = True

def require_products_admin(x_admin_token: Optional[str]):
    approval_token = getattr(settings, "ACCOUNT_APPROVAL_TOKEN", None)
    if not approval_token:
        raise HTTPException(status_code=503, detail="Admin token is not configured.")
    if x_admin_token != approval_token:
        raise HTTPException(status_code=403, detail="Invalid admin token.")

def normalize_category_assignment(category: ProductCategoryAssignment) -> dict:
    department = (category.department or "").strip().lower()
    group = (category.group or "").strip()
    item = (category.item or "").strip()

    if not department or not group:
        raise HTTPException(status_code=422, detail="Department and group are required.")

    normalized = {"department": department, "group": group}
    if item:
        normalized["item"] = item
    return normalized

def normalize_number_string(value) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if number <= 0:
        return None
    return f"{number:g}"

def first_value(*values):
    for value in values:
        normalized = normalize_number_string(value)
        if normalized:
            return normalized
    return None

def normalize_physical_fields(record: dict, inv: dict, uom: dict):
    record["weight"] = first_value(
        record.get("weight"),
        inv.get("weight"),
        (uom or {}).get("weight"),
        record.get("shippingWeight"),
        inv.get("shippingWeight"),
    )

    dimensions = {
        "length": first_value(
            record.get("length"),
            inv.get("length"),
            (uom or {}).get("length"),
            record.get("shipLength"),
            inv.get("shipLength"),
            (uom or {}).get("shipLength"),
            record.get("shippingLength"),
            inv.get("shippingLength"),
            (uom or {}).get("shippingLength"),
            record.get("dimLength"),
            inv.get("dimLength"),
            (uom or {}).get("dimLength"),
        ),
        "width": first_value(
            record.get("width"),
            inv.get("width"),
            (uom or {}).get("width"),
            record.get("shipWidth"),
            inv.get("shipWidth"),
            (uom or {}).get("shipWidth"),
            record.get("shippingWidth"),
            inv.get("shippingWidth"),
            (uom or {}).get("shippingWidth"),
            record.get("dimWidth"),
            inv.get("dimWidth"),
            (uom or {}).get("dimWidth"),
        ),
        "height": first_value(
            record.get("height"),
            inv.get("height"),
            (uom or {}).get("height"),
            record.get("shipHeight"),
            inv.get("shipHeight"),
            (uom or {}).get("shipHeight"),
            record.get("shippingHeight"),
            inv.get("shippingHeight"),
            (uom or {}).get("shippingHeight"),
            record.get("dimHeight"),
            inv.get("dimHeight"),
            (uom or {}).get("dimHeight"),
        ),
        "depth": first_value(
            record.get("depth"),
            inv.get("depth"),
            (uom or {}).get("depth"),
            record.get("shipDepth"),
            inv.get("shipDepth"),
            (uom or {}).get("shipDepth"),
            record.get("shippingDepth"),
            inv.get("shippingDepth"),
            (uom or {}).get("shippingDepth"),
            record.get("dimDepth"),
            inv.get("dimDepth"),
            (uom or {}).get("dimDepth"),
        ),
    }
    dimensions = {key: value for key, value in dimensions.items() if value}
    record["shipping_dimensions"] = dimensions

async def get_review_summary_map(db, product_ids: list[str]) -> dict:
    product_ids = [pid for pid in product_ids if pid]
    if not product_ids:
        return {}

    pipeline = [
        {"$match": {"product_id": {"$in": product_ids}}},
        {"$group": {"_id": "$product_id", "rating": {"$avg": "$rating"}, "reviews": {"$sum": 1}}},
    ]
    rows = await db["product_reviews"].aggregate(pipeline).to_list(length=None)
    return {
        row["_id"]: {
            "rating": round(float(row.get("rating") or 0), 1),
            "reviews": int(row.get("reviews") or 0),
        }
        for row in rows
    }

def apply_review_summary(record: dict, summary_map: dict):
    sku = record.get("partNo") or record.get("inventory", {}).get("partNo") or record.get("id")
    summary = summary_map.get(str(sku), {"rating": 0, "reviews": 0})
    record["rating"] = summary["rating"]
    record["reviews"] = summary["reviews"]

def normalize_product_data(record: dict, request: Request = None, customer_pricing: dict = None, is_authenticated: bool = True, metadata: dict = None) -> dict:
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
        
    actual_part_no = record.get("partNo")
    if not actual_part_no and inv:
        actual_part_no = inv.get("partNo")
        
    record["image"] = None
    normalized_images = []
    
    base_url = str(request.base_url).rstrip('/') if request else "http://localhost:8000"
    
    for img_data in images_list:
        img_url = None
        img_id = img_data.get("id")
        if img_id and part_no:
            img_url = f"{base_url}/api/products/{part_no}/image/{img_id}?no_bg=true"
        elif img_data.get("url"):
            img_url = img_data.get("url")

        if img_url:
            normalized_images.append(img_url)

    if normalized_images:
        record["image"] = normalized_images[0]
    elif part_no:
        # Fallback a la ruta genérica de imagen por si Spire omitió el embed en la lista
        record["image"] = f"{base_url}/api/products/{part_no}/image?no_bg=true"
        # También lo agregamos a la lista por si el frontend usa 'images[0]' para mostrar la foto principal
        normalized_images.append(record["image"])
        
    # Sobrescribir "images" con la lista plana de URLs para el frontend
    record["images"] = normalized_images

    # Normalizar departamento
    sales_dept = record.get("salesDepartment") or inv.get("salesDepartment")
    if isinstance(sales_dept, dict):
        code = sales_dept.get("code")
        record["salesDept"] = int(code) if code and code.isdigit() else sales_dept.get("id", 0)

    # --- Gestión de Descripciones ---
    # En Spire, 'description' es el Nombre/Título del producto.
    raw_product_name = clean_text_encoding_artifacts(record.get("description") or inv.get("description", ""))
    is_dangerous_good = product_is_dangerous_good(record, metadata)
    product_name = clean_dangerous_good_marker(raw_product_name)
    
    record["title"] = product_name
    # Mantenemos 'name' y 'description' como el título limpio para el frontend.
    record["name"] = product_name
    record["description"] = product_name
    record["short_description"] = product_name

    raw_mongo_long = (metadata.get("description") or "") if metadata else ""
    raw_spire_ext = record.get("extendedDescription") or inv.get("extendedDescription") or ""
    mongo_long = clean_text_encoding_artifacts(raw_mongo_long)
    spire_ext = clean_text_encoding_artifacts(raw_spire_ext)
    
    # Keep Spire first so ERP long-description edits appear without waiting for Mongo metadata refresh.
    # If Spire has legacy encoding artifacts and Mongo has a clean version, prefer Mongo for display.
    # Si es igual al nombre del producto, la dejamos vacía para evitar duplicidad visual.
    long_desc = mongo_long if text_has_encoding_artifacts(raw_spire_ext) and mongo_long else spire_ext or mongo_long
    record["long_description"] = long_desc if long_desc not in (product_name, raw_product_name) else ""
    record["frontend_categories"] = metadata.get("subcategories", []) if metadata else []
    record["sds_url"] = metadata.get("sds_url") if metadata else None
    record["data_sheet_url"] = metadata.get("data_sheet_url") if metadata else None
    record["is_dangerous_good"] = is_dangerous_good
    record["upload"] = product_upload_is_enabled(record)
    
    # Normalizar UoM
    measure_code = record.get("sellMeasureCode") or inv.get("sellMeasureCode") or "EACH"
    uom = record.get("uom") or inv.get("uom")
    if uom:
        record["unitOfMeasures"] = {measure_code: uom}
    elif "unitOfMeasures" not in record:
        record["unitOfMeasures"] = {measure_code: {"description": measure_code}}

    normalize_physical_fields(record, inv, uom if isinstance(uom, dict) else {})
        
    # Normalizar Precios
    pricing = record.get("pricing") or inv.get("pricing")
    sell_prices = []
    
    if pricing:
        if "sellPrice" in pricing:
            sell_prices = [float(p) if p else 0.0 for p in pricing["sellPrice"]]
            record["pricing"] = {measure_code: {"sellPrices": sell_prices}}
        else:
            normalized_pricing = {}
            for m_code, m_data in pricing.items():
                if isinstance(m_data, dict) and "sellPrices" in m_data:
                    float_prices = [float(p) if p else 0.0 for p in m_data["sellPrices"]]
                    normalized_pricing[m_code] = {"sellPrices": float_prices}
                    if m_code == measure_code:
                        sell_prices = float_prices
            record["pricing"] = normalized_pricing

    if "price" not in record or record["price"] is None:
        if sell_prices and len(sell_prices) > 0:
            record["price"] = sell_prices[0]

    for field in ["price", "currentCost", "averageCost", "standardCost", "list_price", "sale_price"]:
        if field in record and record[field] is not None:
            try:
                record[field] = float(record[field])
            except (ValueError, TypeError):
                pass

    if not is_authenticated:
        if "pricing" in record:
            for m_code, m_data in record["pricing"].items():
                if isinstance(m_data, dict) and "sellPrices" in m_data:
                    m_data["sellPrices"] = [round(p * 1.05, 2) for p in m_data["sellPrices"]]
        for field in ["price", "list_price", "sale_price"]:
            if field in record and isinstance(record[field], (int, float)):
                record[field] = round(record[field] * 1.05, 2)

    # Apply special customer pricing if provided
    if customer_pricing and actual_part_no and actual_part_no in customer_pricing:
        special_price = float(customer_pricing[actual_part_no])
        # Set sale_price to force the frontend to show it as the active price
        record["sale_price"] = special_price
        # Set on_sale to ensure the UI badges it properly
        record["on_sale"] = True
        
        # Try to calculate list_price for the strikethrough if not already there
        if "list_price" not in record:
            if sell_prices and len(sell_prices) > 0:
                record["list_price"] = sell_prices[0]

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
    sort: Optional[str] = None,
    current_user: Optional[UserInDB] = Depends(get_optional_current_user)
):
    actual_start = skip if skip is not None else start
    
    db = get_database()
    customer_pricing = {}
    if current_user and current_user.spire_customer_no:
        customer_pricing = await spire_client.get_customer_all_pricing(current_user.spire_customer_no)
    
    if on_sale:
        deals = await spire_client.get_deals()
        # Filter out deals with '*' or 'discontinued' in the description
        deals = [d for d in deals if is_product_active(d)]
        
        # Extraer SKUs para buscar metadatos en una sola consulta
        skus = [d.get("partNo") or d.get("inventory", {}).get("partNo") or d.get("id") for d in deals]
        metadata_list = await db["products_metadata"].find({"sku": {"$in": skus}}).to_list(length=None)
        metadata_map = {m["sku"]: m for m in metadata_list}
        review_summary_map = await get_review_summary_map(db, [str(sku) for sku in skus])

        for d in deals:
            sku = d.get("partNo") or d.get("inventory", {}).get("partNo") or d.get("id")
            normalize_product_data(d, request, customer_pricing, is_authenticated=bool(current_user), metadata=metadata_map.get(sku))
            apply_review_summary(d, review_summary_map)
            
        # Pagination for deals (limit 0 means all)
        end = actual_start + limit if limit > 0 else None
        return {
            "records": deals[actual_start : end],
            "count": len(deals),
            "start": actual_start,
            "limit": limit
        }

    q_param = search
    final_group = None
    final_dept = None
    
    # 1. Direct Spire code mappings (for backward compatibility if digits/short codes are sent)
    if department and department.isdigit():
        final_dept = department
    
    if group and len(group) <= 4 and group.isupper():
        final_group = group

    # 2. Add text search (only if explicit search param or if native code mappings failed)
    if not q_param and search:
        q_param = search

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
        
        # Extraer SKUs para buscar metadatos en una sola consulta
        skus = []
        for r in res["records"]:
            sku = r.get("partNo") or r.get("inventory", {}).get("partNo") or r.get("id")
            if sku: skus.append(sku)
            
        # Obtener metadatos desde MongoDB
        metadata_list = await db["products_metadata"].find({"sku": {"$in": skus}}).to_list(length=None)
        metadata_map = {m["sku"]: m for m in metadata_list}
        review_summary_map = await get_review_summary_map(db, [str(sku) for sku in skus])
        
        filtered_records = []
        
        for record in res["records"]:
            actual_part_no = record.get("partNo") or record.get("inventory", {}).get("partNo") or record.get("id")
            meta = metadata_map.get(actual_part_no)
            cats = meta.get("subcategories", []) if meta else []
            
            normalize_product_data(record, request, customer_pricing, is_authenticated=bool(current_user), metadata=meta)
            apply_review_summary(record, review_summary_map)
            
            # Lógica de filtrado en memoria
            # Si el cliente está filtrando usando códigos directos de Spire (final_dept/final_group) o búsqueda de texto (q_param), 
            # Spire ya filtró. Pero si usamos los nombres textuales del frontend (Detailing, Chemicals, etc), debemos filtrar aquí.
            
            include_record = True
            
            # Solo filtramos manualmente si NO se usaron como códigos exactos de Spire
            if department and not final_dept:
                if not any(c.get("department") == department.lower() for c in cats):
                    include_record = False
                    
            if include_record and group and not final_group:
                if not any(c.get("group") == group for c in cats):
                    include_record = False
                    
            if include_record and category:
                if not any(c.get("item") == category for c in cats):
                    include_record = False
                    
            if include_record:
                filtered_records.append(record)
                
        res["records"] = filtered_records

    return res

@router.get("/categories")
async def get_categories():
    # En Spire las categorias suelen ser los "Inventory Groups"
    return await spire_client.get_inventory_groups()
    
@router.get("/departments")
async def get_departments():
    # Alternativamente, departamentos más amplios
    return await spire_client.get_sales_departments()

@router.get("/admin/categories")
async def get_product_category_metadata(x_admin_token: Optional[str] = Header(None)):
    require_products_admin(x_admin_token)

    db = get_database()
    records = await db["products_metadata"].find(
        {},
        {"_id": 0, "sku": 1, "subcategories": 1}
    ).to_list(length=None)

    return [
        {
            "sku": record.get("sku"),
            "subcategories": record.get("subcategories", [])
        }
        for record in records
        if record.get("sku")
    ]

@router.patch("/admin/categories")
async def update_product_categories(
    request: ProductCategoryUpdateRequest,
    x_admin_token: Optional[str] = Header(None)
):
    require_products_admin(x_admin_token)

    skus = []
    seen = set()
    for sku in request.skus:
        cleaned = str(sku or "").strip()
        if cleaned and cleaned not in seen:
            skus.append(cleaned)
            seen.add(cleaned)

    if not skus:
        raise HTTPException(status_code=422, detail="At least one SKU is required.")

    categories = [normalize_category_assignment(category) for category in request.categories]
    if not categories:
        raise HTTPException(status_code=422, detail="At least one category is required.")

    db = get_database()
    now = datetime.utcnow().isoformat()

    if request.replace:
        result = await db["products_metadata"].update_many(
            {"sku": {"$in": skus}},
            {"$set": {"subcategories": categories, "updated_at": now}},
            upsert=False
        )
    else:
        result = await db["products_metadata"].update_many(
            {"sku": {"$in": skus}},
            {
                "$addToSet": {"subcategories": {"$each": categories}},
                "$set": {"updated_at": now}
            },
            upsert=False
        )

    existing = await db["products_metadata"].distinct("sku", {"sku": {"$in": skus}})
    existing_set = set(existing)
    missing = [sku for sku in skus if sku not in existing_set]
    if missing:
        await db["products_metadata"].insert_many([
            {"sku": sku, "subcategories": categories, "updated_at": now, "created_at": now}
            for sku in missing
        ])

    return {
        "updated": len(skus),
        "matched": result.matched_count,
        "modified": result.modified_count,
        "inserted": len(missing),
        "skus": skus,
        "categories": categories,
    }

@router.get("/{product_id}")
async def get_product(product_id: str, request: Request, current_user: Optional[UserInDB] = Depends(get_optional_current_user)):
    product = await spire_client.get_product(product_id)
    if not product_upload_is_enabled(product):
        raise HTTPException(status_code=404, detail="Product not found")

    db = get_database()
    
    customer_pricing = {}
    if current_user and current_user.spire_customer_no:
        try:
            actual_part_no = product.get("partNo") or product.get("inventory", {}).get("partNo") or product_id
            
            # We can use get_customer_pricing for single product optimization or just pull it
            pricing_data = await spire_client.get_customer_pricing(current_user.spire_customer_no, actual_part_no)
            records = pricing_data.get("records", []) if isinstance(pricing_data, dict) else (pricing_data if isinstance(pricing_data, list) else [])
            if records and len(records) > 0:
                record = records[0]
                amount = record.get("amount") if record.get("amount") is not None else record.get("price")
                if amount is not None:
                    customer_pricing[actual_part_no] = float(amount)
        except Exception:
            pass

    actual_part_no = product.get("partNo") or product.get("inventory", {}).get("partNo") or product_id
    # Buscar metadata individual
    metadata = await db["products_metadata"].find_one({"sku": actual_part_no})

    normalized = normalize_product_data(product, request, customer_pricing, is_authenticated=bool(current_user), metadata=metadata)
    review_summary_map = await get_review_summary_map(db, [str(actual_part_no)])
    apply_review_summary(normalized, review_summary_map)
    return normalized

@router.get("/{product_id}/reviews")
async def get_product_reviews(product_id: str):
    db = get_database()
    reviews = await db["product_reviews"].find({"product_id": product_id}).sort("created_at", -1).to_list(length=None)
    for review in reviews:
        review["_id"] = str(review["_id"])

    summary_map = await get_review_summary_map(db, [product_id])
    summary = summary_map.get(product_id, {"rating": 0, "reviews": 0})
    return {
        "product_id": product_id,
        "rating": summary["rating"],
        "reviews": summary["reviews"],
        "items": reviews,
    }

@router.post("/{product_id}/reviews")
async def create_product_review(product_id: str, review: ProductReviewCreate, current_user: UserInDB = Depends(get_current_user)):
    if review.rating < 1 or review.rating > 5:
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5.")

    db = get_database()
    now = datetime.utcnow().isoformat()
    reviewer_name = f"{current_user.first_name} {current_user.last_name}".strip()
    await db["product_reviews"].update_one(
        {"product_id": product_id, "user_email": current_user.email},
        {
            "$set": {
                "product_id": product_id,
                "user_email": current_user.email,
                "user_name": reviewer_name,
                "rating": review.rating,
                "comment": (review.comment or "").strip(),
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )

    summary_map = await get_review_summary_map(db, [product_id])
    summary = summary_map.get(product_id, {"rating": 0, "reviews": 0})
    return {
        "message": "Review saved",
        "product_id": product_id,
        "rating": summary["rating"],
        "reviews": summary["reviews"],
    }

@router.get("/{product_id}/image")
@router.get("/{product_id}/image/{image_id}")
async def get_product_image(product_id: str, image_id: Optional[str] = None, no_bg: bool = False):
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

        # if no_bg:
        #     try:
        #         import io
        #         import numpy as np
        #         from PIL import Image

        #         # Usar numpy para vectorizar el procesamiento, lo cual es miles de veces más rápido
        #         # y evita bloquear el servidor cuando se cargan 20 imágenes al mismo tiempo
        #         input_image = Image.open(io.BytesIO(content)).convert("RGBA")
        #         data = np.array(input_image)
                
        #         # Identificar únicamente los píxeles que son 100% blanco puro (255, 255, 255)
        #         white_pixels = (data[:, :, 0] == 255) & (data[:, :, 1] == 255) & (data[:, :, 2] == 255)
                
        #         # Cambiar la transparencia (canal Alpha = 3) a 0 para los píxeles blancos
        #         data[white_pixels, 3] = 0
                
        #         output_image = Image.fromarray(data)
                
        #         img_byte_arr = io.BytesIO()
        #         output_image.save(img_byte_arr, format='PNG')
        #         content = img_byte_arr.getvalue()
        #         content_type = "image/png"
        #     except Exception as e:
        #         print(f"Error removing pure white background: {e}")

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
        pricing_data = await spire_client.get_customer_pricing(current_user.spire_customer_no, product_id)
        
        # Spire returns a list of records for the price matrix
        records = pricing_data.get("records", []) if isinstance(pricing_data, dict) else (pricing_data if isinstance(pricing_data, list) else [])
        
        if records and len(records) > 0:
            record = records[0]
            
            # The special price is often stored in the 'amount' field in the price matrix
            special_price = record.get("amount")
            if special_price is None:
                special_price = record.get("price")
                
            currency_info = record.get("currency", {})
            currency_code = currency_info.get("code") if isinstance(currency_info, dict) else None
            
            return {"product_id": product_id, "special_price": special_price, "currency": currency_code}
            
        return {"product_id": product_id, "special_price": None, "message": "No special pricing found"}
    except Exception as e:
        # Fallback if no special pricing exists or an error occurs
        return {"product_id": product_id, "special_price": None, "message": str(e)}

@router.get("/deals/all")
async def get_deals(request: Request):
    # Obtiene todas las ofertas activas desde Spire
    db = get_database()
    deals = await spire_client.get_deals()
    deals = [d for d in deals if is_product_active(d)]

    skus = [d.get("partNo") or d.get("inventory", {}).get("partNo") or d.get("id") for d in deals]
    metadata_list = await db["products_metadata"].find({"sku": {"$in": skus}}).to_list(length=None)
    metadata_map = {m["sku"]: m for m in metadata_list}
    review_summary_map = await get_review_summary_map(db, [str(sku) for sku in skus])

    for d in deals:
        sku = d.get("partNo") or d.get("inventory", {}).get("partNo") or d.get("id")
        normalize_product_data(d, request, is_authenticated=False, metadata=metadata_map.get(sku))
        apply_review_summary(d, review_summary_map)
    return deals
