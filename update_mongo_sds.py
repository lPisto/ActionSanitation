import asyncio
import json
import sys
import os

# Agregamos el directorio actual al path para poder importar módulos de la app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, get_database

JSON_FILE_PATH = "products_mongo_seed_with_sds.json"
COLLECTION_NAME = "products_metadata"

async def update_products_metadata():
    if not os.path.exists(JSON_FILE_PATH):
        print(f"Error: No se encontró el archivo {JSON_FILE_PATH}.")
        return

    # Conectar a MongoDB
    await connect_to_mongo()
    db = get_database()
    collection = db[COLLECTION_NAME]

    print(f"Leyendo datos desde {JSON_FILE_PATH}...")
    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"Iniciando actualización de {len(products)} productos...")

    count = 0
    for item in products:
        sku = item.get("sku")
        if not sku:
            continue

        # Realizamos un upsert basado en el SKU
        # Esto actualizará el sds_url, subcategories y description
        result = await collection.update_one(
            {"sku": sku},
            {"$set": item},
            upsert=True
        )
        count += 1
        if count % 100 == 0:
            print(f"  -> Procesados {count} productos...")

    print(f"¡Hecho! Se procesaron {count} registros en la colección '{COLLECTION_NAME}'.")
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(update_products_metadata())