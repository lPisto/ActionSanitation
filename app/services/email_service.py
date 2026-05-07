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

async def send_order_confirmation_email(to_email: str, name: str, order_id: str, items: list, total_amount: float, shipping_address: str):
    if not settings.MAIL_USERNAME:
        print("Email not configured, skipping order confirmation send.")
        return
        
    items_html = "".join([
        f"<li><strong>{item.get('name', 'Item')}</strong> (SKU: {item.get('sku', '')})<br>"
        f"Qty: {item.get('quantity', 1)} | Price: ${float(item.get('price', 0)):.2f}</li>"
        for item in items
    ])

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; color: #333;">
        <h2 style="color: #2563eb;">Thank you for your order, {name}!</h2>
        <p>Your order <strong>#{order_id}</strong> has been successfully confirmed and is now being processed.</p>
        
        <h3 style="border-bottom: 1px solid #ccc; padding-bottom: 5px;">Order Details</h3>
        <ul>
            {items_html}
        </ul>
        <p><strong>Total Amount (incl. shipping & taxes):</strong> ${total_amount:.2f}</p>
        <p><strong>Shipping Address:</strong> {shipping_address}</p>
        
        <br>
        <p>We will notify you once your order ships. If you have any questions, feel free to reply to this email.</p>
        <p>Best regards,<br><strong>Action Sanitation</strong></p>
    </div>
    """

    message_schema = MessageSchema(
        subject=f"Order Confirmation #{order_id} - Action Sanitation",
        recipients=[to_email],
        body=html,
        subtype="html"
    )

    fm = FastMail(conf)
    await fm.send_message(message_schema)

async def send_newsletter_notification_email(subscriber_email: str):
    if not settings.MAIL_USERNAME:
        print("Email not configured, skipping newsletter notification send.")
        return
        
    html = f"""
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2563eb;">New Newsletter Subscriber!</h2>
        <p>A new user has just signed up for the newsletter on the website.</p>
        <p><strong>Email address:</strong> <a href="mailto:{subscriber_email}">{subscriber_email}</a></p>
    </div>
    """

    message_schema = MessageSchema(
        subject="New Newsletter Subscription",
        recipients=[settings.SALES_EMAIL],
        body=html,
        subtype="html"
    )
    
    fm = FastMail(conf)
    await fm.send_message(message_schema)
