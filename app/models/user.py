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
    billing_street_address: Optional[str] = None
    billing_city: Optional[str] = None
    billing_state_province: Optional[str] = None
    billing_zip: Optional[str] = None
    billing_country: Optional[str] = None
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
    billing_street_address: Optional[str] = None
    billing_city: Optional[str] = None
    billing_state_province: Optional[str] = None
    billing_zip: Optional[str] = None
    billing_country: Optional[str] = None
    email: Optional[EmailStr] = None

class UserInDB(BaseModel):
    id: str
    spire_customer_no: str
    internal_customer_id: Optional[str] = None
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
    billing_street_address: Optional[str] = None
    billing_city: Optional[str] = None
    billing_state_province: Optional[str] = None
    billing_zip: Optional[str] = None
    billing_country: Optional[str] = None
    account_status: str = "approved"
    approved: bool = True
    free_delivery: Optional[bool] = False
    # Consolidated dealer sub-accounts: this user is a single dealership under a shared
    # parent Spire customer, tied to one ship-to location (sees only its own orders).
    assigned_ship_to_code: Optional[str] = None
    assigned_ship_to_name: Optional[str] = None

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None
