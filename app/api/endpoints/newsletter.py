from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from datetime import datetime
from app.db.mongodb import get_database
from app.services.email_service import send_newsletter_notification_email

router = APIRouter()

class SubscribeRequest(BaseModel):
    email: EmailStr

@router.post("/subscribe")
async def subscribe_newsletter(request: SubscribeRequest):
    db = get_database()
    
    # 1. Comprobar si el correo ya está suscrito
    existing = await db["newsletter_subscribers"].find_one({"email": request.email})
    if existing:
        return {"message": "Already subscribed"}

    # 2. Guardar el nuevo correo en la base de datos (MongoDB)
    new_subscriber = {
        "email": request.email,
        "subscribed_at": datetime.utcnow().isoformat()
    }
    await db["newsletter_subscribers"].insert_one(new_subscriber)

    # 3. Notificar por email a la dirección de Ventas
    try:
        await send_newsletter_notification_email(request.email)
    except Exception as e:
        print(f"Error sending newsletter notification: {e}")

    return {"message": "Subscribed successfully"}