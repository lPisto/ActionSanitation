from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    MONGODB_URL: str
    MONGODB_DB_NAME: str = "action_sanitation"
    
    SPIRE_BASE_URL: str
    SPIRE_USERNAME: str
    SPIRE_PASSWORD: str
    
    STRIPE_API_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    
    SECRET_KEY: str
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440 # 24 hours
    
    MAIL_USERNAME: str = ""
    MAIL_PASSWORD: str = ""
    MAIL_FROM: str = ""
    MAIL_PORT: int = 587
    MAIL_SERVER: str = ""
    MAIL_STARTTLS: bool = True
    MAIL_SSL_TLS: bool = False
    SALES_EMAIL: str = ""

    class Config:
        env_file = ".env"

settings = Settings()
