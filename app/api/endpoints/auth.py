from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import time
from app.models.user import UserCreate, Token, UserInDB
from app.core.security import verify_password, get_password_hash, create_access_token
from app.services.spire_client import spire_client
from app.db.mongodb import get_database
import uuid
import secrets
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.services.email_service import send_password_reset_email

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def truncate(value: str, max_length: int):
    return value[:max_length] if value else value

class ForgotPasswordRequest(BaseModel):
    email: str

class ResetPasswordRequest(BaseModel):
    email: str
    otp: str
    new_password: str

@router.post("/register", response_model=UserInDB)
async def register(user_in: UserCreate):
    db = get_database()
    
    if user_in.password != user_in.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    
    existing_user = await db["users"].find_one({"email": user_in.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Generate a unique customer number for Spire
    generated_customer_no = f"W{uuid.uuid4().hex[:9]}".upper()

    # 1. Map user_in to Spire Customer format
    spire_customer_data = {
        "customerNo": truncate(generated_customer_no, 12),
        "name": truncate(f"{user_in.first_name} {user_in.last_name}", 40),
        "status": "A",
        "address": {
            "name": truncate(f"{user_in.first_name} {user_in.last_name}", 40),
            "city": truncate(user_in.city, 30),
            "line1": truncate(user_in.street_address, 50),
            "postalCode": truncate(user_in.zip, 10),
            "provState": truncate(user_in.state_province, 20),
            "country": truncate(user_in.country, 3).upper() if user_in.country else "",
            "email": truncate(user_in.email, 50),
            "phone": {
                "number": truncate(user_in.phone_number, 20)
            },
            "contacts": [
                {
                    "name": truncate(f"{user_in.first_name} {user_in.last_name}", 40),
                    "email": truncate(user_in.email, 50),
                    "phone": {
                        "number": truncate(user_in.phone_number, 20)
                    }
                }
            ]
        }
    }
    
    # 2. Create customer in Spire ERP
    try:
        spire_response = await spire_client.create_customer(spire_customer_data)
        # Assuming Spire returns the generated customer number (e.g. 'customerNo')
        spire_customer_no = spire_response.get("customerNo", generated_customer_no)
    except HTTPException as e:
        # Pasa el error HTTP lanzado por nuestro spire_client directamente al frontend
        raise HTTPException(status_code=400, detail=f"Spire ERP Error: {e.detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error communicating with Spire: {str(e)}")

    # 3. Save to local DB (MongoDB)
    user_count = await db["users"].count_documents({})
    user_db = UserInDB(
        id=str(user_count + 1),
        spire_customer_no=spire_customer_no,
        email=user_in.email,
        first_name=user_in.first_name,
        last_name=user_in.last_name,
        company=user_in.company,
        phone_number=user_in.phone_number,
        city=user_in.city,
        street_address=user_in.street_address,
        zip=user_in.zip,
        state_province=user_in.state_province,
        country=user_in.country
    )
    
    user_doc = {
        "email": user_in.email,
        "user": user_db.model_dump(),
        "hashed_password": get_password_hash(user_in.password)
    }
    
    await db["users"].insert_one(user_doc)

    return user_db

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = get_database()
    user_record = await db["users"].find_one({"email": form_data.username})
    
    if not user_record:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    if not verify_password(form_data.password, user_record["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    access_token = create_access_token(subject=form_data.username)
    return {"access_token": access_token, "token_type": "bearer"}

@router.post("/forgot-password")
async def forgot_password(req: ForgotPasswordRequest):
    db = get_database()
    user_record = await db["users"].find_one({"email": req.email})
    
    if not user_record:
        raise HTTPException(status_code=404, detail="User with this email not found")

    # Generamos un OTP de 6 dígitos de forma criptográficamente segura
    otp = "".join([str(secrets.choice(range(10))) for _ in range(6)])
    expiry = datetime.utcnow() + timedelta(minutes=15)

    # Lo guardamos temporalmente en el documento del usuario en MongoDB
    await db["users"].update_one(
        {"email": req.email},
        {"$set": {"reset_otp": otp, "reset_otp_expiry": expiry.isoformat()}}
    )

    try:
        await send_password_reset_email(req.email, otp)
    except Exception as e:
        print(f"Error sending OTP email: {e}")
        raise HTTPException(status_code=500, detail="Failed to send email. Please try again later.")

    return {"message": "OTP sent to email"}

@router.post("/reset-password")
async def reset_password(req: ResetPasswordRequest):
    db = get_database()
    user_record = await db["users"].find_one({"email": req.email})
    
    if not user_record:
        raise HTTPException(status_code=404, detail="User not found")

    stored_otp = user_record.get("reset_otp")
    stored_expiry_str = user_record.get("reset_otp_expiry")
    
    if not stored_otp or not stored_expiry_str:
        raise HTTPException(status_code=400, detail="No OTP requested for this email")
        
    if stored_otp != req.otp:
        raise HTTPException(status_code=400, detail="Invalid verification code")
        
    stored_expiry = datetime.fromisoformat(stored_expiry_str)
    if datetime.utcnow() > stored_expiry:
        raise HTTPException(status_code=400, detail="Verification code has expired")

    hashed_password = get_password_hash(req.new_password)
    await db["users"].update_one(
        {"email": req.email},
        {
            "$set": {"hashed_password": hashed_password},
            "$unset": {"reset_otp": "", "reset_otp_expiry": ""}
        }
    )

    return {"message": "Password reset successfully"}
