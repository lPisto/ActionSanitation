import os
import uuid
from dotenv import load_dotenv
from pymongo import MongoClient

# Cargar variables de entorno antes de importar Cloudinary
load_dotenv()

import cloudinary
import cloudinary.uploader

MONGO_URL = os.getenv("MONGODB_URL")
DB_NAME = os.getenv("MONGODB_DB_NAME", "action_sanitation")
CLOUDINARY_URL = os.getenv("CLOUDINARY_URL")

if not MONGO_URL:
    print("❌ Error: MONGODB_URL no está definido en el .env")
    exit(1)

if not CLOUDINARY_URL:
    print("❌ Error: CLOUDINARY_URL no está definido en el .env")
    exit(1)

# Parsear la URL de Cloudinary manualmente para evitar problemas de lectura
url_clean = CLOUDINARY_URL.replace("cloudinary://", "")
api_key_secret, cloud_name = url_clean.split("@")
api_key, api_secret = api_key_secret.split(":")

# Configurar Cloudinary explícitamente
cloudinary.config(
    cloud_name=cloud_name,
    api_key=api_key,
    api_secret=api_secret
)

client = MongoClient(MONGO_URL)
db = client[DB_NAME]

CATEGORIES = ["catalogs", "flyers", "sds", "gallery", "training"]

def upload_main_categories():
    print("\n--- Subiendo categorías principales ---")
    for category in CATEGORIES:
        folder_path = f"static/{category}"
        if not os.path.exists(folder_path):
            continue
            
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                display_name = os.path.splitext(file)[0].replace("_", " ")
                
                # Checkear si ya existe en la DB
                existing = db["resources"].find_one({"category": category, "name": display_name})
                if existing and existing.get("cloudinary_public_id"):
                    print(f"✅ Ya migrado: {display_name}")
                    continue
                    
                print(f"⏳ Subiendo {file_path}...")
                try:
                    result = cloudinary.uploader.upload(
                        file_path,
                        folder=f"action_sanitation/{category}",
                        resource_type="auto"
                    )
                    
                    url_path = result.get("secure_url")
                    public_id = result.get("public_id")
                    resource_type = result.get("resource_type", "image")
                    unique_id = uuid.uuid4().hex[:8]
                    
                    entry = {
                        "id": unique_id,
                        "category": category,
                        "name": display_name,
                        "cloudinary_public_id": public_id,
                        "cloudinary_resource_type": resource_type
                    }
                    
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
                        
                    db["resources"].insert_one(entry)
                    print(f"🚀 Guardado en DB: {display_name}")
                    
                except Exception as e:
                    print(f"❌ Error al subir {file_path}: {e}")

def upload_vendor_msds():
    print("\n--- Subiendo MSDS of Vendors Library ---")
    base_dir = "static/MSDS of Vendors Library"
    if not os.path.exists(base_dir):
        print("Carpeta MSDS of Vendors Library no encontrada, omitiendo...")
        return

    # Crear índice único para evitar duplicados en carpetas y archivos
    db["vendor_msds"].create_index([("parent_folder", 1), ("name", 1)], unique=True)
    
    for root, dirs, files in os.walk(base_dir):
        rel_root = os.path.relpath(root, base_dir)
        if rel_root == ".":
            rel_root = ""
            
        parent_folder_normalized = rel_root.replace("\\", "/")
            
        # Registrar las carpetas en la DB
        for d in dirs:
            print(f"📁 Registrando carpeta: {d} (padre: '{parent_folder_normalized}')")
            db["vendor_msds"].update_one(
                {"parent_folder": parent_folder_normalized, "name": d},
                {"$set": {
                    "type": "folder",
                    "parent_folder": parent_folder_normalized,
                    "name": d
                }},
                upsert=True
            )
            
        # Subir y registrar los archivos en la DB
        for file in files:
            file_path = os.path.join(root, file)
            
            existing = db["vendor_msds"].find_one({"parent_folder": parent_folder_normalized, "name": file})
            if existing and existing.get("cloudinary_public_id"):
                print(f"✅ Ya migrado: {file}")
                continue

            print(f"⏳ Subiendo archivo: {file_path}...")
            try:
                cloudinary_folder = f"action_sanitation/msds_vendors/{parent_folder_normalized}".strip("/")
                
                result = cloudinary.uploader.upload(
                    file_path,
                    folder=cloudinary_folder,
                    resource_type="auto"
                )
                
                url_path = result.get("secure_url")
                public_id = result.get("public_id")
                resource_type = result.get("resource_type", "image")
                
                db["vendor_msds"].update_one(
                    {"parent_folder": parent_folder_normalized, "name": file},
                    {"$set": {
                        "type": "file",
                        "parent_folder": parent_folder_normalized,
                        "name": file,
                        "url": url_path,
                        "cloudinary_public_id": public_id,
                        "cloudinary_resource_type": resource_type
                    }},
                    upsert=True
                )
                print(f"🚀 Guardado en DB: {file}")
            except Exception as e:
                print(f"❌ Error al subir {file_path}: {e}")

if __name__ == "__main__":
    print("Iniciando migración masiva a Cloudinary...")
    upload_main_categories()
    upload_vendor_msds()
    print("\n✅ ¡Migración completada!")
