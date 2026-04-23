from fastapi import APIRouter, Depends, HTTPException
from app.models.user import UserInDB, UserUpdate
from app.api.deps import get_current_user
from app.services.spire_client import spire_client
from app.api.endpoints.auth import truncate
from app.db.mongodb import get_database

router = APIRouter()

@router.get("/me", response_model=UserInDB)
async def read_users_me(current_user: UserInDB = Depends(get_current_user)):
    return current_user

@router.put("/me", response_model=UserInDB)
async def update_user_me(user_update: UserUpdate, current_user: UserInDB = Depends(get_current_user)):
    # 1. Update Spire ERP
    spire_update_data = {}
    
    if user_update.first_name or user_update.last_name:
        fname = user_update.first_name or current_user.first_name
        lname = user_update.last_name or current_user.last_name
        spire_update_data["name"] = truncate(f"{fname} {lname}", 40)
        
    contact_data = {}
    if user_update.first_name: contact_data["firstName"] = truncate(user_update.first_name, 20)
    if user_update.last_name: contact_data["lastName"] = truncate(user_update.last_name, 30)
    if user_update.email: contact_data["email"] = truncate(user_update.email, 50)
    if user_update.phone_number: contact_data["phone"] = truncate(user_update.phone_number, 20)
    
    if contact_data:
        spire_update_data["contact"] = contact_data
        
    address_data = {}
    if user_update.city: address_data["city"] = truncate(user_update.city, 30)
    if user_update.street_address: address_data["line1"] = truncate(user_update.street_address, 50)
    if user_update.zip: address_data["postalZip"] = truncate(user_update.zip, 10)
    if user_update.state_province: address_data["provState"] = truncate(user_update.state_province, 20)
    if user_update.country: address_data["country"] = truncate(user_update.country, 3).upper()

    if address_data:
        spire_update_data["address"] = address_data
    
    if spire_update_data:
        await spire_client.update_customer(current_user.spire_customer_no, spire_update_data)

    # 2. Update local DB (MongoDB)
    if user_update.first_name: current_user.first_name = user_update.first_name
    if user_update.last_name: current_user.last_name = user_update.last_name
    if user_update.company: current_user.company = user_update.company
    if user_update.phone_number: current_user.phone_number = user_update.phone_number
    if user_update.city: current_user.city = user_update.city
    if user_update.street_address: current_user.street_address = user_update.street_address
    if user_update.zip: current_user.zip = user_update.zip
    if user_update.state_province: current_user.state_province = user_update.state_province
    if user_update.country: current_user.country = user_update.country
    
    # Save back to MongoDB
    db = get_database()
    await db["users"].update_one(
        {"email": current_user.email},
        {"$set": {"user": current_user.model_dump()}}
    )
    
    return current_user
