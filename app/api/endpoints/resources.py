import os
import uuid
import shutil
import cloudinary
import cloudinary.uploader
from fastapi import APIRouter, File, UploadFile, Form, HTTPException, Query, Request
from typing import List, Optional
from app.db.mongodb import get_database
from dotenv import load_dotenv

load_dotenv()

# Configure Cloudinary explicitly
cloudinary_url = os.getenv("CLOUDINARY_URL")
if cloudinary_url:
    url_clean = cloudinary_url.replace("cloudinary://", "")
    api_key_secret, cloud_name = url_clean.split("@")
    api_key, api_secret = api_key_secret.split(":")
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret
    )

router = APIRouter()

CATEGORIES = ["catalogs", "flyers", "sds", "gallery", "training"]

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
        if category == "catalogs":
            if "name" in item: item["name"] = item["name"].replace("2017", "").strip()
            if "title" in item: item["title"] = item["title"].replace("2017", "").strip()

        if "image_url" in item and "url" not in item:
            item["url"] = item["image_url"]
        if "video_url" in item and "url" not in item:
            item["url"] = item["video_url"]
            
        if item.get("url") and item["url"].startswith("/"):
            item["url"] = f"{base_url}{item['url']}"

    return items

# --- GET ENDPOINTS ---

@router.get("/downloads/catalogs")
async def get_catalogs():
    db = get_database()
    items = await db["resources"].find({"category": "catalogs"}).to_list(length=None)
    for item in items:
        item["_id"] = str(item["_id"])
        if "name" in item: item["name"] = item["name"].replace("2017", "").strip()
        if "title" in item: item["title"] = item["title"].replace("2017", "").strip()
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
    db = get_database()
    # Normalize folder path to prevent slashes issues
    folder_path = folder.replace("\\", "/").strip("/")
    
    # Query MongoDB for items in this specific folder
    items = await db["vendor_msds"].find({"parent_folder": folder_path}).to_list(length=None)
    
    result = []
    base_url = str(request.base_url).rstrip('/')
    
    for item in items:
        url = item.get("url")
        if url and url.startswith("/"):
            url = f"{base_url}{url}"
            
        result.append({
            "name": item["name"],
            "type": item["type"],
            "url": url
        })
            
    # Sort: folders first, then files
    result.sort(key=lambda x: (x["type"] == "file", x["name"].lower()))
    return result

# --- UPLOAD (POST) ENDPOINTS ---

@router.post("/upload/{category}")
async def upload_resource(
    category: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None)
):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Invalid category. Must be: catalogs, flyers, sds, gallery, training")

    # Upload file to Cloudinary
    try:
        result = cloudinary.uploader.upload(
            file.file,
            folder=f"action_sanitation/{category}",
            resource_type="auto"
        )
        url_path = result.get("secure_url")
        public_id = result.get("public_id")
        resource_type = result.get("resource_type", "image")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Could not upload file to Cloudinary: {str(e)}")

    unique_id = uuid.uuid4().hex[:8]
    display_name = title if title else os.path.splitext(file.filename)[0]

    # Build DB entry
    db = get_database()
    entry = {
        "id": unique_id,
        "category": category,
        "name": display_name,
        "cloudinary_public_id": public_id,
        "cloudinary_resource_type": resource_type
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

    # If it is a Cloudinary file
    public_id = item_to_delete.get("cloudinary_public_id")
    if public_id:
        resource_type = item_to_delete.get("cloudinary_resource_type", "image")
        try:
            cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        except Exception as e:
            # We can log this, but we'll proceed to delete from DB anyway
            pass

    # Remove from DB
    await db["resources"].delete_one({"category": category, "id": item_id})

    return {"message": "Resource deleted successfully"}
