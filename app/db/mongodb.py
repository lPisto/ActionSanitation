from motor.motor_asyncio import AsyncIOMotorClient
from app.core.config import settings

class MongoDB:
    client: AsyncIOMotorClient = None
    db = None

db_client = MongoDB()

async def connect_to_mongo():
    db_client.client = AsyncIOMotorClient(settings.MONGODB_URL)
    db_client.db = db_client.client[settings.MONGODB_DB_NAME]
    await db_client.client.admin.command("ping")
    print(f"Connected to MongoDB database: {settings.MONGODB_DB_NAME}")

async def close_mongo_connection():
    if db_client.client:
        db_client.client.close()

def get_database():
    return db_client.db
