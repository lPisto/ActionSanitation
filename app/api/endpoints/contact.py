from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException
from app.services.email_service import send_contact_email
from datetime import datetime
from app.db.mongodb import get_database

router = APIRouter()

class ContactForm(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str

@router.post("/")
async def submit_contact_form(form: ContactForm):
    try:
        # 1. Guardar en base de datos local (MongoDB)
        contact_data = form.model_dump()
        contact_data["timestamp"] = datetime.utcnow().isoformat()
        
        db = get_database()
        await db["contacts"].insert_one(contact_data)

        # 2. Enviar email al representante
        await send_contact_email(form.name, form.email, form.subject, form.message)
        
        return {"message": "Message saved and sent successfully to sales representative."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process contact form: {str(e)}")
