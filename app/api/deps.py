from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from jose import jwt, JWTError
from app.core.config import settings
from app.models.user import TokenData, UserInDB
from app.db.mongodb import get_database

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")
oauth2_scheme_optional = OAuth2PasswordBearer(tokenUrl="/api/auth/login", auto_error=False)

async def get_current_user(token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            raise credentials_exception
        token_data = TokenData(email=email)
    except JWTError:
        raise credentials_exception
    
    db = get_database()
    user_record = await db["users"].find_one({"email": token_data.email})
    
    if user_record is None:
        raise credentials_exception

    user_data = user_record.get("user", {}) if isinstance(user_record, dict) else {}
    account_status = user_record.get("account_status") or user_data.get("account_status", "approved")
    approved = user_record.get("approved")
    if approved is None:
        approved = user_data.get("approved", account_status == "approved")
    if not approved or account_status != "approved":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your account is pending approval.",
        )
    
    if isinstance(user_record.get("user"), dict):
        return UserInDB(**user_record["user"])
    return user_record.get("user", user_record)

async def get_optional_current_user(token: Optional[str] = Depends(oauth2_scheme_optional)) -> Optional[UserInDB]:
    if not token:
        return None
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        email: str = payload.get("sub")
        if email is None:
            return None
    except JWTError:
        return None
        
    db = get_database()
    user_record = await db["users"].find_one({"email": email})
    
    if user_record is None:
        return None

    user_data = user_record.get("user", {}) if isinstance(user_record, dict) else {}
    account_status = user_record.get("account_status") or user_data.get("account_status", "approved")
    approved = user_record.get("approved")
    if approved is None:
        approved = user_data.get("approved", account_status == "approved")
    if not approved or account_status != "approved":
        return None
        
    if isinstance(user_record.get("user"), dict):
        return UserInDB(**user_record["user"])
    return user_record.get("user", user_record)
