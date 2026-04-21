from fastapi import APIRouter, Depends, HTTPException
from app.models.user import UserInDB, UserUpdate
from app.api.deps import get_current_user
from app.services.spire_client import spire_client
from app.api.endpoints.auth import fake_users_db

router = APIRouter()

@router.get("/me", response_model=UserInDB)
async def read_users_me(current_user: UserInDB = Depends(get_current_user)):
    return current_user

@router.put("/me", response_model=UserInDB)
async def update_user_me(user_update: UserUpdate, current_user: UserInDB = Depends(get_current_user)):
    # 1. Update Spire ERP
    spire_update_data = {}
    if user_update.first_name or user_update.last_name:
        spire_update_data["name"] = f"{user_update.first_name or current_user.first_name} {user_update.last_name or current_user.last_name}"
    
    # Need to merge with existing data in Spire realistically,
    # but for simplicity we assume PUT updates provided fields
    await spire_client.update_customer(current_user.spire_customer_no, spire_update_data)

    # 2. Update local DB
    if user_update.first_name: current_user.first_name = user_update.first_name
    if user_update.last_name: current_user.last_name = user_update.last_name
    if user_update.company: current_user.company = user_update.company
    
    # Save back to fake DB
    fake_users_db[current_user.email]["user"] = current_user
    
    return current_user
