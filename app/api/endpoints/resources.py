import os
import uuid
import shutil
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Query, Request
from typing import List, Optional
from app.db.mongodb import get_database

router = APIRouter()

# Initial structure if file does not exist (not needed to initialize on disk anymore)
CATEGORIES = ["catalogs", "flyers", "sds", "gallery", "training"]

# Create static directories if they don't exist
for category in CATEGORIES:
    os.makedirs(f"static/{category}", exist_ok=True)

@router.get("")
async def get_all_resources(request: Request, type: Optional[str] = None):
    base_url = str(request.base_url).rstrip('/')
    db = get_database()
    category = type
    if type == "catalog": category = "catalogs"
    elif type == "flyer": category = "flyers"
    
    if category not in CATEGORIES:
        return []
        
    items = await db["resources"].find({"category": category}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
        # Normalize title
        if "name" in item and "title" not in item:
            item["title"] = item["name"]
        if "image_url" in item and "url" not in item:
            item["url"] = item["image_url"]
        if "video_url" in item and "url" not in item:
            item["url"] = item["video_url"]
            
        if item.get("url") and item["url"].startswith("/"):
            item["url"] = f"{base_url}{item['url']}"

    # Append local files that are not in the DB for specific categories
    if category in ["sds", "catalogs", "flyers"]:
        local_files = []
        for root, _, files in os.walk(f"static/{category}"):
            for file in files:
                if file.lower().endswith(".pdf"):
                    # create relative url
                    rel_url = "/" + os.path.join(root, file).replace("\\", "/")
                    title = os.path.splitext(file)[0].replace("_", " ")
                    # check if already in items
                    if not any(i.get("url") == f"{base_url}{rel_url}" or i.get("url") == rel_url for i in items):
                        local_files.append({
                            "id": uuid.uuid4().hex[:8],
                            "category": category,
                            "title": title,
                            "url": f"{base_url}{rel_url}",
                        })
        items.extend(local_files)
        
    return items

# --- GET ENDPOINTS ---

@router.get("/downloads/catalogs")
async def get_catalogs():
    db = get_database()
    items = await db["resources"].find({"category": "catalogs"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
    return items

@router.get("/downloads/flyers")
async def get_flyers():
    db = get_database()
    items = await db["resources"].find({"category": "flyers"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
    return items

@router.get("/downloads/sds")
async def get_sds():
    db = get_database()
    items = await db["resources"].find({"category": "sds"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
    return items

@router.get("/gallery")
async def get_gallery():
    db = get_database()
    items = await db["resources"].find({"category": "gallery"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
    return items

@router.get("/training")
async def get_training_materials():
    db = get_database()
    items = await db["resources"].find({"category": "training"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
    return items

# --- VENDOR MSDS BROWSER ENDPOINT ---

@router.get("/vendor-msds")
async def get_vendor_msds(request: Request, folder: str = Query("")):
    # Definimos la ruta base de la carpeta dentro de tu directorio "static"
    base_dir = os.path.abspath(os.path.join(os.getcwd(), "static", "MSDS of Vendors Library"))
    
    # Si la carpeta aún no existe, devolvemos una lista vacía
    if not os.path.exists(base_dir):
        return []
        
    target_dir = os.path.abspath(os.path.join(base_dir, folder))
    
    # Seguridad: Prevenir ataques de Directory Traversal (ej. folder="../../../")
    if not target_dir.startswith(base_dir):
        raise HTTPException(status_code=400, detail="Ruta de carpeta inválida")
        
    if not os.path.exists(target_dir) or not os.path.isdir(target_dir):
        raise HTTPException(status_code=404, detail="Carpeta no encontrada")
        
    items = []
    base_url = str(request.base_url).rstrip('/')
    try:
        for item in os.listdir(target_dir):
            item_path = os.path.join(target_dir, item)
            
            # Si es una carpeta, la agregamos como tipo 'folder'
            if os.path.isdir(item_path):
                items.append({
                    "name": item,
                    "type": "folder"
                })
            else:
                # Si es un archivo, verificamos que sea un documento válido
                if item.lower().endswith(('.pdf', '.doc', '.docx', '.xls', '.xlsx')):
                    rel_path = f"/static/MSDS of Vendors Library/{folder}/{item}" if folder else f"/static/MSDS of Vendors Library/{item}"
                    rel_path = rel_path.replace("\\", "/").replace("//", "/")
                    items.append({
                        "name": item,
                        "type": "file",
                        "url": f"{base_url}{rel_path}"
                    })
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
            
    # Ordenar: primero las carpetas, luego los archivos (alfabéticamente)
    items.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
    return items

# --- UPLOAD (POST) ENDPOINTS ---

@router.post("/upload/{category}")
async def upload_resource(
    category: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None)
):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category. Must be: catalogs, flyers, sds, gallery, training")

    # Ensure secure filename and generate unique ID
    extension = os.path.splitext(file.filename)[1]
    unique_id = uuid.uuid4().hex[:8]
    safe_filename = f"{unique_id}_{file.filename.replace(' ', '_')}"
    file_path = f"static/{category}/{safe_filename}"
    
    # URL to be accessed by frontend
    url_path = f"/static/{category}/{safe_filename}"

    # Save physical file
    try:
        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not save file: {str(e)}")

    # Display name (use provided title or filename without extension)
    display_name = title if title else os.path.splitext(file.filename)[0]

    # Build DB entry
    db = get_database()
    entry = {
        "id": unique_id,
        "category": category,
        "name": display_name,
    }
    
    # Adjust field names based on category to maintain backward compatibility
    if category in ["catalogs", "flyers", "sds"]:
        entry["url"] = url_path
    elif category == "gallery":
        entry["title"] = display_name
        entry["image_url"] = url_path
        del entry["name"]
    elif category == "training":
        entry["title"] = display_name
        entry["video_url"] = url_path
        del entry["name"]

    # Save to database
    await db["resources"].insert_one(entry.copy())

    return {"message": f"Successfully uploaded to {category}", "data": entry}

@router.delete("/{category}/{item_id}")
async def delete_resource(category: str, item_id: str):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category.")

    db = get_database()
    
    # Find item
    item_to_delete = await db["resources"].find_one({"category": category, "id": item_id})
    
    if not item_to_delete:
        raise HTTPException(status_code=404, detail="Item not found")

    # Determine file path from URL
    url_field = "url" if category in ["catalogs", "flyers", "sds"] else ("image_url" if category == "gallery" else "video_url")
    file_url = item_to_delete.get(url_field, "")
    
    if file_url.startswith("/"):
        file_path = file_url.lstrip("/") # Remove leading slash to get relative local path
        if os.path.exists(file_path):
            os.remove(file_path)

    # Remove from DB
    await db["resources"].delete_one({"category": category, "id": item_id})

    return {"message": "Resource deleted successfully"}
