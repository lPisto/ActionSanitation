import os
import uuid
import shutil
from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from typing import List, Optional
from app.db.mongodb import get_database

router = APIRouter()

# Initial structure if file does not exist (not needed to initialize on disk anymore)
CATEGORIES = ["catalogs", "flyers", "sds", "gallery", "training"]

# Create static directories if they don't exist
for category in CATEGORIES:
    os.makedirs(f"static/{category}", exist_ok=True)

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
