import asyncio
import json
import os
import sys

# Agregar el directorio actual al path para importar módulos de la app
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.db.mongodb import connect_to_mongo, close_mongo_connection, get_database

JSON_FILE_PATH = "products_mongo_seed.json"

async def seed_products():
    if not os.path.exists(JSON_FILE_PATH):
        print(f"❌ Error: No se encontró el archivo {JSON_FILE_PATH}.")
        return

    await connect_to_mongo()
    db = get_database()
    collection = db["products_metadata"]

    # Crear índice único para búsquedas rápidas por SKU
    await collection.create_index("sku", unique=True)

    print(f"📖 Leyendo {JSON_FILE_PATH}...")
    with open(JSON_FILE_PATH, "r", encoding="utf-8") as f:
        products = json.load(f)

    print(f"🚀 Iniciando carga de {len(products)} productos...")
    
    count = 0
    for p in products:
        sku = p.get("sku")
        if not sku:
            continue
            
        # Usamos upsert para actualizar si ya existe o insertar si es nuevo
        await collection.update_one(
            {"sku": sku},
            {"$set": p},
            upsert=True
        )
        count += 1

    print(f"✅ Carga completada. {count} registros procesados.")
    await close_mongo_connection()

if __name__ == "__main__":
    asyncio.run(seed_products())