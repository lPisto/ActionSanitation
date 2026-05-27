import httpx
from app.core.config import settings

async def get_graph_token():
    url = f"https://login.microsoftonline.com/{settings.MS_TENANT_ID}/oauth2/v2.0/token"
    data = {
        "client_id": getattr(settings, "MS_CLIENT_ID", ""),
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": getattr(settings, "MS_CLIENT_SECRET", ""),
        "grant_type": "client_credentials"
    }
    async with httpx.AsyncClient() as client:
        response = await client.post(url, data=data)
        response.raise_for_status()
        return response.json().get("access_token")

async def send_email_via_graph(subject: str, recipients: list, html_content: str):
    if not getattr(settings, "MS_CLIENT_ID", None):
        print("Microsoft Graph not configured, skipping email send.")
        print(f"To: {recipients}, Subject: {subject}")
        return

    try:
        token = await get_graph_token()
    except Exception as e:
        print(f"Error obtaining Graph token: {e}")
        return

    # Endpoint to send email from the specific mailbox defined in MAIL_FROM
    send_mail_url = f"https://graph.microsoft.com/v1.0/users/{settings.MAIL_FROM}/sendMail"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }
    
    to_recipients = [{"emailAddress": {"address": email}} for email in recipients]
    
    email_msg = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_content
            },
            "toRecipients": to_recipients
        },
        "saveToSentItems": "true"
    }
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(send_mail_url, headers=headers, json=email_msg)
            response.raise_for_status()
            print("Email sent successfully via MS Graph.")
        except httpx.HTTPStatusError as e:
            print(f"Failed to send email via MS Graph: {e.response.text}")
        except Exception as e:
            print(f"Error sending email via MS Graph: {e}")

async def send_contact_email(name: str, email: str, subject: str, message: str):
    html = f"""
    <b> New contact form submission from: </b><p> {name} ({email})</p>
    <p><strong>Subject:</strong> {subject}</p>
    <p><strong>Message:</strong></p>
    <p>{message}</p>
    """

    await send_email_via_graph(
        subject=f"New Contact Form: {subject}", 
        recipients=[settings.SALES_EMAIL],
        html_content=html
    )

async def send_order_confirmation_email(to_email: str, name: str, order_id: str, items: list, total_amount: float, shipping_address: str):
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

    await send_email_via_graph(
        subject=f"Order Confirmation #{order_id} - Action Sanitation", 
        recipients=[to_email], 
        html_content=html
    )

async def send_newsletter_notification_email(subscriber_email: str):
    html = f"""
    <div style="font-family: Arial, sans-serif; color: #333;">
        <h2 style="color: #2563eb;">New Newsletter Subscriber!</h2>
        <p>A new user has just signed up for the newsletter on the website.</p>
        <p><strong>Email address:</strong> <a href="mailto:{subscriber_email}">{subscriber_email}</a></p>
    </div>
    """

    await send_email_via_graph(
        subject="New Newsletter Subscription", 
        recipients=[settings.SALES_EMAIL], 
        html_content=html
    )

async def send_password_reset_email(to_email: str, otp: str):
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; color: #333;">
        <h2 style="color: #2563eb;">Password Reset Request</h2>
        <p>You recently requested to reset your password for your Action Sanitation account.</p>
        <p>Your 6-digit verification code is:</p>
        <div style="background-color: #f3f4f6; padding: 16px; text-align: center; font-size: 24px; font-weight: bold; letter-spacing: 6px; border-radius: 6px; margin: 24px 0;">
            {otp}
        </div>
        <p>This code will expire in 15 minutes. If you did not request a password reset, please ignore this email.</p>
        <br>
        <p>Best regards,<br><strong>Action Sanitation</strong></p>
    </div>
    """

    await send_email_via_graph(
        subject="Password Reset Code - Action Sanitation", 
        recipients=[to_email], 
        html_content=html
    )
