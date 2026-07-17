from pydantic_settings import BaseSettings
from dotenv import load_dotenv

load_dotenv()

class Settings(BaseSettings):
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "action_sanitation"
    
    SPIRE_BASE_URL: str
    SPIRE_USERNAME: str
    SPIRE_PASSWORD: str

    
    
    CONVERGE_URL: str
    ELAVON_MERCHANT_ID: str
    ELAVON_PROCESSOR_ID: str
    ELAVON_PUBLIC_KEY: str
    ELAVON_SECRET_KEY: str
    ELAVON_MERCHANT_ALIAS: str
    CONVERGE_HPP_URL: str = ""
    CONVERGE_XML_URL: str = ""
    ELAVON_CONVERGE_ACCOUNT_ID: str = ""
    ELAVON_CONVERGE_USER_ID: str = ""
    ELAVON_CONVERGE_PIN: str = ""
    ELAVON_CONVERGE_VENDOR_ID: str = ""
    # Static-IP proxy (cPanel) used to reach Converge from a whitelisted IP.
    CONVERGE_PROXY_URL: str = ""
    CONVERGE_PROXY_SECRET: str = ""
    
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 hours
    
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_REPLY_TO: str = ""
    MAIL_PORT: int = 587
    MS_CLIENT_ID: str
    MS_OBJECT_ID: str
    MS_TENANT_ID: str
    MS_CLIENT_SECRET: str
    MAIL_SERVER: str = ""
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    SALES_EMAIL: str = ""
    ADMIN_ALERT_EMAIL: str = ""  # where "account pending approval" alerts go (falls back to SALES_EMAIL)

    SSH_CPANEL_PASSWORD: str | None = None
    CPANEL_HOST: str | None = None
    CPANEL_USERNAME: str | None = None
    CPANEL_TOKEN: str | None = None

    FRONTEND_URLS: str

    CLOUDINARY_URL: str
    ACCOUNT_APPROVAL_TOKEN: str | None = None
  

    class Config:
        env_file = ".env"

settings = Settings()
