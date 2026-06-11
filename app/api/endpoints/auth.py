from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import time
from app.models.user import UserCreate, Token, UserInDB
from app.core.security import verify_password, get_password_hash, create_access_token
from app.core.config import settings
from app.services.spire_client import spire_client
from app.db.mongodb import get_database
import uuid
import secrets
import re
from datetime import datetime, timedelta
from pydantic import BaseModel
from app.services.email_service import send_password_reset_email
from typing import Optional

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

class AccountApprovalRequest(BaseModel):
    email: str
    spire_customer_no: Optional[str] = None
    create_spire_customer: bool = False
    approved: bool = True

class ImpersonateRequest(BaseModel):
    email: Optional[str] = None
    spire_customer_no: Optional[str] = None

def require_account_admin(x_admin_token: Optional[str]):
    approval_token = getattr(settings, "ACCOUNT_APPROVAL_TOKEN", None)
    if not approval_token:
        raise HTTPException(status_code=503, detail="Account approval token is not configured.")
    if x_admin_token != approval_token:
        raise HTTPException(status_code=403, detail="Invalid account approval token.")

def build_spire_customer_payload(user_in: UserCreate, customer_no: str) -> dict:
    return {
        "customerNo": truncate(customer_no, 12),
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

def user_create_from_record(user_record: dict) -> UserCreate:
    user = user_record.get("registration_payload") or user_record.get("user", {})
    return UserCreate(
        first_name=user.get("first_name", ""),
        last_name=user.get("last_name", ""),
        company=user.get("company"),
        phone_number=user.get("phone_number", ""),
        city=user.get("city", ""),
        street_address=user.get("street_address", ""),
        zip=user.get("zip", ""),
        state_province=user.get("state_province", ""),
        country=user.get("country", ""),
        billing_street_address=user.get("billing_street_address"),
        billing_city=user.get("billing_city"),
        billing_state_province=user.get("billing_state_province"),
        billing_zip=user.get("billing_zip"),
        billing_country=user.get("billing_country"),
        email=user.get("email", user_record.get("email", "")),
        password="placeholder",
        confirm_password="placeholder",
    )

def summarize_user_record(record: dict) -> dict:
    user = record.get("user", {})
    account_status = record.get("account_status") or user.get("account_status", "approved")
    approved = record.get("approved")
    if approved is None:
        approved = user.get("approved", account_status == "approved")

    return {
        "email": record.get("email"),
        "name": f"{user.get('first_name', '')} {user.get('last_name', '')}".strip(),
        "first_name": user.get("first_name"),
        "last_name": user.get("last_name"),
        "company": user.get("company"),
        "phone_number": user.get("phone_number"),
        "city": user.get("city"),
        "street_address": user.get("street_address"),
        "zip": user.get("zip"),
        "spire_customer_no": user.get("spire_customer_no"),
        "account_status": account_status,
        "approved": bool(approved),
        "spire_match_found": record.get("spire_match_found", False),
        "created_at": record.get("created_at"),
        "approved_at": record.get("approved_at"),
        "updated_at": record.get("updated_at"),
    }

@router.post("/register", response_model=UserInDB)
async def register(user_in: UserCreate):
    db = get_database()
    
    if user_in.password != user_in.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match")
    
    existing_user = await db["users"].find_one({"email": user_in.email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered")

    try:
        spire_customer = await spire_client.get_customer_by_email(user_in.email)
    except Exception as e:
        print(f"Error checking Spire for existing customer: {e}")
        spire_customer = None

    spire_customer_no = spire_customer.get("customerNo") if spire_customer else ""

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
        country=user_in.country,
        billing_street_address=user_in.billing_street_address,
        billing_city=user_in.billing_city,
        billing_state_province=user_in.billing_state_province,
        billing_zip=user_in.billing_zip,
        billing_country=user_in.billing_country,
        account_status="pending_approval",
        approved=False
    )
    
    user_doc = {
        "email": user_in.email,
        "user": user_db.model_dump(),
        "hashed_password": get_password_hash(user_in.password),
        "account_status": "pending_approval",
        "approved": False,
        "spire_match_found": bool(spire_customer),
        "registration_payload": user_in.model_dump(exclude={"password", "confirm_password"}),
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }
    
    await db["users"].insert_one(user_doc)

    return user_db

@router.get("/pending-accounts")
async def list_pending_accounts(x_admin_token: Optional[str] = Header(None)):
    require_account_admin(x_admin_token)
    db = get_database()
    records = await db["users"].find({"account_status": "pending_approval"}).to_list(length=None)
    return [summarize_user_record(record) for record in records]

@router.post("/approve-account")
async def approve_account(req: AccountApprovalRequest, x_admin_token: Optional[str] = Header(None)):
    require_account_admin(x_admin_token)
    db = get_database()
    user_record = await db["users"].find_one({"email": req.email})

    if not user_record:
        raise HTTPException(status_code=404, detail="User not found")

    user_doc = user_record.get("user", {})

    if not req.approved:
        user_doc["account_status"] = "rejected"
        user_doc["approved"] = False
        await db["users"].update_one(
            {"email": req.email},
            {"$set": {
                "user": user_doc,
                "account_status": "rejected",
                "approved": False,
                "updated_at": datetime.utcnow().isoformat()
            }}
        )
        return {"message": "Account rejected"}

    requested_spire_customer_no = (req.spire_customer_no or "").strip()
    existing_spire_customer_no = (user_doc.get("spire_customer_no") or "").strip()
    spire_customer_no = requested_spire_customer_no or existing_spire_customer_no

    if req.create_spire_customer:
        if spire_customer_no:
            raise HTTPException(
                status_code=400,
                detail="This account already has a Spire customer number. Approve the existing Spire customer instead of creating a new one."
            )
        generated_customer_no = f"W{uuid.uuid4().hex[:9]}".upper()
        user_in = user_create_from_record(user_record)
        spire_customer_data = build_spire_customer_payload(user_in, generated_customer_no)
        try:
            spire_response = await spire_client.create_customer(spire_customer_data)
            spire_customer_no = spire_response.get("customerNo", generated_customer_no)
        except HTTPException as e:
            raise HTTPException(status_code=400, detail=f"Spire ERP Error: {e.detail}")
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Unexpected error communicating with Spire: {str(e)}")

    if not spire_customer_no:
        raise HTTPException(status_code=400, detail="Approval requires an existing Spire customer number or create_spire_customer=true.")

    try:
        await spire_client.get_customer(spire_customer_no)
    except HTTPException as e:
        raise HTTPException(status_code=400, detail=f"Spire customer could not be verified: {e.detail}")

    user_doc["spire_customer_no"] = spire_customer_no
    user_doc["account_status"] = "approved"
    user_doc["approved"] = True

    await db["users"].update_one(
        {"email": req.email},
        {"$set": {
            "user": user_doc,
            "account_status": "approved",
            "approved": True,
            "approved_at": datetime.utcnow().isoformat(),
            "updated_at": datetime.utcnow().isoformat()
        }}
    )

    return {"message": "Account approved", "spire_customer_no": spire_customer_no}

@router.post("/impersonate", response_model=Token)
async def impersonate(req: ImpersonateRequest, x_admin_token: Optional[str] = Header(None)):
    require_account_admin(x_admin_token)
    db = get_database()

    if req.email:
        query = {"email": req.email}
    elif req.spire_customer_no:
        query = {"user.spire_customer_no": req.spire_customer_no}
    else:
        raise HTTPException(status_code=400, detail="email or spire_customer_no is required")

    user_record = await db["users"].find_one(query)
    if not user_record:
        raise HTTPException(status_code=404, detail="User not found")

    user_data = user_record.get("user", {})
    account_status = user_record.get("account_status") or user_data.get("account_status", "approved")
    approved = user_record.get("approved")
    if approved is None:
        approved = user_data.get("approved", account_status == "approved")
    if not approved or account_status != "approved":
        raise HTTPException(status_code=403, detail="Cannot impersonate an account that is not approved.")

    access_token = create_access_token(subject=user_record["email"])
    return {"access_token": access_token, "token_type": "bearer"}

@router.get("/admin/users")
async def list_admin_users(
    q: Optional[str] = None,
    account_status: Optional[str] = None,
    limit: int = 50,
    x_admin_token: Optional[str] = Header(None),
):
    require_account_admin(x_admin_token)
    db = get_database()

    query = {}
    if account_status and account_status != "all":
        query["account_status"] = account_status

    if q:
        escaped = re.escape(q.strip())
        if escaped:
            query["$or"] = [
                {"email": {"$regex": escaped, "$options": "i"}},
                {"user.first_name": {"$regex": escaped, "$options": "i"}},
                {"user.last_name": {"$regex": escaped, "$options": "i"}},
                {"user.company": {"$regex": escaped, "$options": "i"}},
                {"user.spire_customer_no": {"$regex": escaped, "$options": "i"}},
            ]

    safe_limit = min(max(limit, 1), 200)
    records = await db["users"].find(query).sort("updated_at", -1).limit(safe_limit).to_list(length=safe_limit)
    return [summarize_user_record(record) for record in records]

@router.post("/login", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    db = get_database()
    user_record = await db["users"].find_one({"email": form_data.username})
    
    if not user_record:
        raise HTTPException(status_code=400, detail="Incorrect email or password")
    
    if not verify_password(form_data.password, user_record["hashed_password"]):
        raise HTTPException(status_code=400, detail="Incorrect email or password")

    user_data = user_record.get("user", {})
    account_status = user_record.get("account_status") or user_data.get("account_status", "approved")
    approved = user_record.get("approved")
    if approved is None:
        approved = user_data.get("approved", account_status == "approved")
    if not approved or account_status != "approved":
        raise HTTPException(status_code=403, detail="Your account is pending approval. Please contact Action Sanitation if you need access sooner.")

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
