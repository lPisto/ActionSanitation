from pydantic import BaseModel, EmailStr
from fastapi import APIRouter, HTTPException
from app.services.email_service import send_contact_email

router = APIRouter()

class ContactForm(BaseModel):
    name: str
    email: EmailStr
    subject: str
    message: str

@router.post("/")
async def submit_contact_form(form: ContactForm):
    try:
        await send_contact_email(form.name, form.email, form.subject, form.message)
        return {"message": "Message sent successfully to sales representative."}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Failed to send email.")
