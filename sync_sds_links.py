import asyncio
import json
import sys
import os

# Agregamos el directorio actual al path para importar módulos de la app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, get_database

# El archivo JSON se encuentra en la raíz del proyecto (un nivel arriba de Backend)
JSON_FILE_PATH = os.path.join("..", "sds_to_products_mapping.json")
COLLECTION_NAME = "products_metadata"

async def sync_sds_links():
    if not os.path.exists(JSON_FILE_PATH):
        print(f"❌ Error: No se encontró el archivo {JSON_FILE_PATH}.")
        return

    await connect_to_mongo()
    db = get_database()
    collection = db[COLLECTION_NAME]

    print(f"📖 Leyendo mapeos desde {JSON_FILE_PATH}...")
    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        mappings = json.load(f)

    print(f"🚀 Iniciando actualización de {len(mappings)} enlaces SDS...")

    updated_count = 0
    for item in mappings:
        sku = item.get("sku")
        url_sds = item.get("url_sds")

        if not sku or url_sds is None:
            continue

        # Actualizamos el campo sds_url basándonos en el SKU
        # Usamos $set para no sobrescribir otros campos como descripciones o categorías
        result = await collection.update_many(
            {"sku": sku},
            {"$set": {"sds_url": url_sds}}
        )
        updated_count += result.matched_count

    print(f"✅ Proceso completado.")
    print(f"📊 Resumen: {len(mappings)} mapeos procesados, {updated_count} productos actualizados en BD.")
    
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(sync_sds_links())