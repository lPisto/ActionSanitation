from pydantic import BaseModel, EmailStr
from typing import Optional

class UserCreate(BaseModel):
    first_name: str
    last_name: str
    company: Optional[str] = None
    phone_number: str
    city: str
    street_address: str
    zip: str
    state_province: str
    country: str
    email: EmailStr
    password: str
    confirm_password: str

class UserUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    phone_number: Optional[str] = None
    city: Optional[str] = None
    street_address: Optional[str] = None
    zip: Optional[str] = None
    state_province: Optional[str] = None
    country: Optional[str] = None
    email: Optional[EmailStr] = None

class UserInDB(BaseModel):
    id: str
    spire_customer_no: str
    email: str
    first_name: str
    last_name: str
    company: Optional[str] = None
    phone_number: str
    city: str
    street_address: str
    zip: str
    state_province: str
    country: str

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None
