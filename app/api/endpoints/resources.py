import os
import json
import uuid
import shutil
from fastapi import APIRouter, File, UploadFile, Form, HTTPException
from typing import List, Optional

router = APIRouter()

RESOURCES_DB_FILE = "resources_db.json"

# Initial structure if file does not exist
INITIAL_DB = {
    "catalogs": [],
    "flyers": [],
    "sds": [],
    "gallery": [],
    "training": []
}

def load_db():
    if os.path.exists(RESOURCES_DB_FILE):
        try:
            with open(RESOURCES_DB_FILE, "r") as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return INITIAL_DB

def save_db(db):
    with open(RESOURCES_DB_FILE, "w") as f:
        json.dump(db, f, indent=4)

# Create static directories if they don't exist
for category in INITIAL_DB.keys():
    os.makedirs(f"static/{category}", exist_ok=True)

# --- GET ENDPOINTS ---

@router.get("/downloads/catalogs")
async def get_catalogs():
    db = load_db()
    return db.get("catalogs", [])

@router.get("/downloads/flyers")
async def get_flyers():
    db = load_db()
    return db.get("flyers", [])

@router.get("/downloads/sds")
async def get_sds():
    db = load_db()
    return db.get("sds", [])

@router.get("/gallery")
async def get_gallery():
    db = load_db()
    return db.get("gallery", [])

@router.get("/training")
async def get_training_materials():
    db = load_db()
    return db.get("training", [])

# --- UPLOAD (POST) ENDPOINTS ---

@router.post("/upload/{category}")
async def upload_resource(
    category: str,
    file: UploadFile = File(...),
    title: Optional[str] = Form(None)
):
    if category not in INITIAL_DB.keys():
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
    db = load_db()
    entry = {
        "id": unique_id,
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
    db[category].append(entry)
    save_db(db)

    return {"message": f"Successfully uploaded to {category}", "data": entry}

@router.delete("/{category}/{item_id}")
async def delete_resource(category: str, item_id: str):
    if category not in INITIAL_DB.keys():
        raise HTTPException(status_code=400, detail="Invalid category.")

    db = load_db()
    items = db.get(category, [])
    
    # Find item
    item_to_delete = next((item for item in items if item["id"] == item_id), None)
    
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
    db[category] = [item for item in items if item["id"] != item_id]
    save_db(db)

    return {"message": "Resource deleted successfully"}
