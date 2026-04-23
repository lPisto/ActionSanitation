from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import time
from app.models.user import UserCreate, Token, UserInDB
from app.core.security import verify_password, get_password_hash, create_access_token
from app.services.spire_client import spire_client
from app.db.mongodb import get_database
import uuid

router = APIRouter()

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

def truncate(value: str, max_length: int):
    return value[:max_length] if value else value

@router.post("/register", response_model=UserInDB)
async def register(user_in: UserCreate):
    db = get_database()
    
    if user_in.password != user_in.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    
    existing_user = await db["users"].find_one({"email": user_in.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Generate a unique customer number for Spire
    generated_customer_no = f"W{uuid.uuid4().hex[:9]}"

    # 1. Map user_in to Spire Customer format
    spire_customer_data = {
    "customerNo": truncate(generated_customer_no, 12),
    "name": truncate(f"{user_in.first_name} {user_in.last_name}", 40),
    "status": "A",
    "contact": {
        "firstName": truncate(user_in.first_name, 20),
        "lastName": truncate(user_in.last_name, 30),
        "email": truncate(user_in.email, 50),
        "phone": truncate(user_in.phone_number, 20)
    },
    "address": {
        "city": truncate(user_in.city, 30),
        "line1": truncate(user_in.street_address, 50),
        "postalZip": truncate(user_in.zip, 10),
        "provState": truncate(user_in.state_province, 20),
        "country": truncate(user_in.country, 3).upper() if user_in.country else ""
    }
}
    
    # 2. Create customer in Spire ERP
    spire_response = await spire_client.create_customer(spire_customer_data)
    
    # Assuming Spire returns the generated customer number (e.g. 'customerNo')
    spire_customer_no = spire_response.get("customerNo", generated_customer_no)

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
