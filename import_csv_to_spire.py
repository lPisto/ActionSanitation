import asyncio
import csv
import uuid
import sys
import os

# Agregamos el directorio actual al path para poder importar módulos de FastAPI
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.services.spire_client import spire_client
from app.db.mongodb import connect_to_mongo, close_mongo_connection, get_database

CSV_FILE_PATH = "users.csv" # <-- CAMBIA ESTO por el nombre real de tu archivo

def truncate(value: str, max_length: int):
    return value[:max_length] if value else value

async def process_csv():
    if not os.path.exists(CSV_FILE_PATH):
        print(f"Error: No se encontró el archivo {CSV_FILE_PATH}.")
        print("Asegúrate de colocar tu export en esta carpeta y llamarlo 'users.csv'")
        return

    await connect_to_mongo()
    db = get_database()
    
    with open(CSV_FILE_PATH, mode='r', encoding='utf-8-sig') as csvfile:
        reader = csv.DictReader(csvfile)
        
        # OJO: Estos nombres dependen de las columnas exactas de tu CSV.
        # Ajusta los nombres dentro de row[] a los encabezados de tu archivo.
        for row in reader:
            email = row.get("Email", "").strip()
            
            if not email:
                continue

            existing_user = await db["users"].find_one({"email": email})
            if existing_user:
                print(f"Usuario {email} ya existe en BD local. Saltando...")
                continue
            
            # El campo Name trae el nombre completo, lo dividimos en First y Last Name
            full_name = row.get("Name", "").strip().split(" ", 1)
            first_name = row.get("Billing Firstname", "").strip() or (full_name[0] if len(full_name) > 0 else "Web")
            last_name = row.get("Billing Lastname", "").strip() or (full_name[1] if len(full_name) > 1 else "User")
            
            phone = row.get("Phone", "").strip() or "555-5555"
            company = row.get("Company", "").strip()
            zip_code = row.get("ZIP", "").strip()
            country = row.get("Country", "").strip()
            state_prov = row.get("State/Province", "").strip()
            city = row.get("City", "").strip()
            street = row.get("Street Address", "").strip()
            
            # Al no haber contraseñas en el CSV, generamos una contraseña aleatoria imposible de adivinar.
            # Los usuarios tendrán que usar la función de "Olvidé mi contraseña" en la nueva web.
            random_password = uuid.uuid4().hex + uuid.uuid4().hex
            from app.core.security import get_password_hash
            new_password_hash = get_password_hash(random_password)

            print(f"Procesando a {email}...")

            # 1. Crear el cliente en Spire ERP
            generated_customer_no = f"W{uuid.uuid4().hex[:9]}".upper()
            spire_customer_data = {
                "customerNo": truncate(generated_customer_no, 12),
                "name": truncate(f"{first_name} {last_name}", 40),
                "status": "A",
                "address": {
                    "name": truncate(f"{first_name} {last_name}", 40),
                    "city": truncate(city, 30),
                    "line1": truncate(street, 50),
                    "postalCode": truncate(zip_code, 10),
                    "provState": truncate(state_prov, 20),
                    "country": truncate(country, 3).upper() if country else "",
                    "email": truncate(email, 50),
                    "phone": {
                        "number": truncate(phone, 20)
                    },
                    "contacts": [
                        {
                            "name": truncate(f"{first_name} {last_name}", 40),
                            "email": truncate(email, 50),
                            "phone": {
                                "number": truncate(phone, 20)
                            }
                        }
                    ]
                }
            }

            try:
                # Opcional: Primero podrías intentar buscar si el cliente ya existe en Spire por email
                spire_response = await spire_client.create_customer(spire_customer_data)
                customer_no = spire_response.get("customerNo", generated_customer_no)
                print(f"  -> Cliente creado en Spire con Customer No: {customer_no}")
            except Exception as e:
                print(f"  -> Error al crear {email} en Spire: {e}. Usando código autogenerado temporal.")
                customer_no = generated_customer_no

            # 2. Guardar en BD Local (MongoDB)
            user_count = await db["users"].count_documents({})
            
            user_doc = {
                "email": email,
                "user": {
                    "id": str(user_count + 1),
                    "spire_customer_no": customer_no,
                    "email": email,
                    "first_name": first_name,
                    "last_name": last_name,
                    "company": company
                },
                "hashed_password": new_password_hash 
            }
            
            await db["users"].insert_one(user_doc)

    await close_mongo_connection()
    print("Importación finalizada.")

if __name__ == "__main__":
    asyncio.run(process_csv())
