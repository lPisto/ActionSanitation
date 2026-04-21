from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException
from app.services.email_service import send_contact_email
import json
import os
from datetime import datetime

router = APIRouter()

CONTACTS_FILE = "contacts.json"

def save_contact(data: dict):
    contacts = []
    if os.path.exists(CONTACTS_FILE):
        try:
            with open(CONTACTS_FILE, "r") as f:
                contacts = json.load(f)
        except json.JSONDecodeError:
            contacts = []
            
    contacts.append(data)
    
    with open(CONTACTS_FILE, "w") as f:
        json.dump(contacts, f, indent=4)

class ContactForm(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str

@router.post("/")
async def submit_contact_form(form: ContactForm):
    try:
        # 1. Guardar en base de datos local (JSON)
        contact_data = form.model_dump()
        contact_data["timestamp"] = datetime.utcnow().isoformat()
        save_contact(contact_data)

        # 2. Enviar email al representante
        await send_contact_email(form.name, form.email, form.subject, form.message)
        
        return {"message": "Message saved and sent successfully to sales representative."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to process contact form: {str(e)}")
