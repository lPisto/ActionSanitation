from pydantic_settings import BaseSettings
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

from typing import Optional

load_dotenv()

class Settings(BaseSettings):
    # MongoDB
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "action_sanitation"
    

    # Spire ERP
    SPIRE_BASE_URL: str
    SPIRE_USERNAME: str
    SPIRE_PASSWORD: str
    

    # Security
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 hours

    # CONVERGE_URL: str
    # ELAVON_MERCHANT_ID: str
    # ELAVON_PROCESSOR_ID: str
    # ELAVON_PUBLIC_KEY: str
    # ELAVON_SECRET_KEY: str
    # # El alias suele ser el nombre comercial, guárdalo por si acaso
    # ELAVON_MERCHANT_ALIAS: str

    ACCOUNT_APPROVAL_TOKEN: str
    
    

    # Email
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_PORT: int = 587
    MS_CLIENT_ID: str
    MS_OBJECT_ID: str
    MS_TENANT_ID: str
    MS_CLIENT_SECRET: str
    
    # Microsoft Graph (Mandatory in .env)
    MS_CLIENT_ID: str
    MS_OBJECT_ID: str
    MS_TENANT_ID: str
    MS_CLIENT_SECRET: str
    
    MAIL_SERVER: str = ""
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    SALES_EMAIL: str = ""

    SSH_CPANEL_PASSWORD: str | None = None
    CPANEL_HOST: str | None = None
    CPANEL_USERNAME: str | None = None
    CPANEL_TOKEN: str | None = None

    FRONTEND_URLS: str

    CLOUDINARY_URL: str
  

    class Config:
        env_file = ".env"



settings = Settings()