from fastapi_mail import FastMail, MessageSchema, ConnectionConfig
from app.core.config import settings

conf = ConnectionConfig(
    MAIL_USERNAME=settings.MAIL_USERNAME,
    MAIL_PASSWORD=settings.MAIL_PASSWORD,
    MAIL_FROM=settings.MAIL_FROM,
    MAIL_PORT=settings.MAIL_PORT,
    MAIL_SERVER=settings.MAIL_SERVER,
    MAIL_STARTTLS=settings.MAIL_STARTTLS,
    MAIL_SSL_TLS=settings.MAIL_SSL_TLS,
    USE_CREDENTIALS=True,
    VALIDATE_CERTS=True
)

async def send_contact_email(name: str, email: str, subject: str, message: str):
    if not settings.MAIL_USERNAME:
        print("Email not configured, skipping send.")
        print(f"To: {settings.SALES_EMAIL}, Subject: {subject}, Body: {message}")
        return
        
    html = f"""
    <b> New contact form submission from: </b><p> {name} ({email})</p>
    <p><strong>Subject:</strong> {subject}</p>
    <p><strong>Message:</strong></p>
    <p>{message}</p>
    """

    message_schema = MessageSchema(
        subject=f"New Contact Form: {subject}",
        recipients=[settings.SALES_EMAIL],
        body=html,
        subtype="html"
    )

    fm = FastMail(conf)
    await fm.send_message(message_schema)
