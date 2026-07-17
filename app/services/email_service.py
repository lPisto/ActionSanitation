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

    reply_to = str(
        getattr(settings, "MAIL_REPLY_TO", "")
        or getattr(settings, "MAIL_FROM", "")
        or ""
    ).strip()
    if reply_to:
        email_msg["message"]["replyTo"] = [{"emailAddress": {"address": reply_to}}]

    sender_candidates = []
    configured_mail_from = str(getattr(settings, "MAIL_FROM", "") or "").strip()
    sender_sources = (
        (configured_mail_from,)
        if configured_mail_from
        else (
            getattr(settings, "MS_GRAPH_SENDER", ""),
            getattr(settings, "MAIL_USERNAME", ""),
        )
    )
    for sender in sender_sources:
        sender = str(sender or "").strip()
        if sender and sender not in sender_candidates:
            sender_candidates.append(sender)

    if not sender_candidates:
        print("No Graph sender mailbox configured, skipping email send.")
        return

    async with httpx.AsyncClient() as client:
        for index, sender in enumerate(sender_candidates):
            send_mail_url = f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
            try:
                response = await client.post(send_mail_url, headers=headers, json=email_msg)
                response.raise_for_status()
                print(f"Email sent successfully via MS Graph from {sender}.")
                return
            except httpx.HTTPStatusError as e:
                error_text = e.response.text
                if index < len(sender_candidates) - 1 and "ErrorInvalidUser" in error_text:
                    print(f"Graph sender {sender} is invalid, retrying next configured sender.")
                    continue
                print(f"Failed to send email via MS Graph: {error_text}")
                return
            except Exception as e:
                print(f"Error sending email via MS Graph: {e}")
                return

async def send_pending_account_notification(name: str, email: str, company: str = ""):
    """Alert staff that a new website account is awaiting admin approval."""
    # Alerts go to the order mailbox (order@actionsanitationsupply.com = MAIL_FROM).
    # ADMIN_ALERT_EMAIL can override the destination if ever needed.
    recipient = (
        getattr(settings, "ADMIN_ALERT_EMAIL", "")
        or getattr(settings, "MAIL_FROM", "")
        or getattr(settings, "SALES_EMAIL", "")
    ).strip()
    if not recipient:
        print("No admin alert recipient configured; skipping pending-account email.")
        return
    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; color: #333;">
        <h2 style="color: #175b1e;">New account pending approval</h2>
        <p>A new website account was created and is waiting for approval in the admin panel.</p>
        <p><strong>Name:</strong> {name or 'N/A'}<br>
           <strong>Email:</strong> {email or 'N/A'}<br>
           <strong>Company:</strong> {company or 'N/A'}</p>
        <p>Review it under <strong>Admin → Pending Approvals</strong>.</p>
    </div>
    """
    try:
        await send_email_via_graph(
            subject="New account pending approval — Action Sanitation",
            recipients=[recipient],
            html_content=html,
        )
    except Exception as e:
        print(f"Could not send pending-account notification: {e}")

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

async def send_order_confirmation_email(
    to_email: str,
    name: str,
    order_id: str,
    items: list,
    total_amount: float,
    shipping_address: str,
    shipping_method: str = "delivery",
    promo_note: str = "",
):
    items_html = "".join([
        f"<li><strong>{item.get('name', 'Item')}</strong> (SKU: {item.get('sku', '')})<br>"
        f"Qty: {item.get('quantity', 1)} | Price: ${float(item.get('price', 0)):.2f}</li>"
        for item in items
    ])

    is_pickup = str(shipping_method or "").strip().lower() == "pickup"
    # Show the relevant address label (pickup vs delivery); the body message is kept
    # simple per client request — no shipment-confirmation promises.
    fulfillment_html = (
        "<p><strong>Fulfillment:</strong> Pickup at our store.</p>"
        if is_pickup
        else f"<p><strong>Delivery address:</strong> {shipping_address}</p>"
    )

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; color: #333;">
        <h2 style="color: #2563eb;">Thank you for your order, {name}!</h2>
        <p>Your order <strong>#{order_id}</strong> has been received and is now being processed.</p>

        <h3 style="border-bottom: 1px solid #ccc; padding-bottom: 5px;">Order Details</h3>
        <ul>
            {items_html}
        </ul>
        {f"<p style='background:#ecfdf5; border:1px solid #bbf7d0; padding:12px; border-radius:8px;'><strong>{promo_note}</strong></p>" if promo_note else ""}
        <p><strong>Total Amount (incl. shipping & taxes):</strong> ${total_amount:.2f}</p>
        {fulfillment_html}
        <br>
        <p>If you have any questions, feel free to reply to this email.</p>
        <p>Best regards,<br><strong>Action Sanitation</strong></p>
    </div>
    """

    await send_email_via_graph(
        subject=f"Order Confirmation #{order_id} - Action Sanitation", 
        recipients=[to_email], 
        html_content=html
    )

async def send_pending_payment_order_notification_email(
    customer_name: str,
    customer_email: str,
    customer_company: str,
    payment_method: str,
    order_id: str,
    local_order_id: str,
    items: list,
    total_amount: float,
    shipping_address: str,
    billing_address: str = "",
    po_number: str = "",
    order_notes: str = "",
):
    if not getattr(settings, "SALES_EMAIL", None):
        print("SALES_EMAIL not configured, skipping pending payment notification.")
        return

    payment_label = {
        "e_transfer": "E-Transfer Pending",
        "on_account": "On Account / COD",
        "cod": "Cash on Delivery",
        "cash_on_delivery": "Cash on Delivery",
    }.get(payment_method, payment_method)

    items_html = "".join([
        f"<tr>"
        f"<td style='padding: 8px; border-bottom: 1px solid #e5e7eb;'>{item.get('sku') or item.get('product_id') or ''}</td>"
        f"<td style='padding: 8px; border-bottom: 1px solid #e5e7eb;'>{item.get('name') or 'Item'}</td>"
        f"<td style='padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;'>{item.get('quantity', 1)}</td>"
        f"<td style='padding: 8px; border-bottom: 1px solid #e5e7eb; text-align: right;'>${float(item.get('price') or 0):.2f}</td>"
        f"</tr>"
        for item in items
    ])

    html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 760px; margin: auto; color: #333;">
        <h2 style="color: #111827;">New order awaiting payment</h2>
        <p>A customer placed an order that was not paid online.</p>

        <table style="width: 100%; border-collapse: collapse; margin: 16px 0;">
            <tr><td style="padding: 6px 0;"><strong>Payment status:</strong></td><td>{payment_label}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Spire / order ID:</strong></td><td>{order_id}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Local order ID:</strong></td><td>{local_order_id}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Customer:</strong></td><td>{customer_name} ({customer_email})</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Company:</strong></td><td>{customer_company or 'N/A'}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>PO number:</strong></td><td>{po_number or 'N/A'}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Total:</strong></td><td>${total_amount:.2f}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Shipping address:</strong></td><td>{shipping_address or 'N/A'}</td></tr>
            <tr><td style="padding: 6px 0;"><strong>Billing address:</strong></td><td>{billing_address or 'Same as shipping / N/A'}</td></tr>
        </table>

        <h3 style="border-bottom: 1px solid #ccc; padding-bottom: 5px;">Items</h3>
        <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr>
                    <th style="padding: 8px; text-align: left; border-bottom: 1px solid #d1d5db;">SKU</th>
                    <th style="padding: 8px; text-align: left; border-bottom: 1px solid #d1d5db;">Product</th>
                    <th style="padding: 8px; text-align: right; border-bottom: 1px solid #d1d5db;">Qty</th>
                    <th style="padding: 8px; text-align: right; border-bottom: 1px solid #d1d5db;">Price</th>
                </tr>
            </thead>
            <tbody>{items_html}</tbody>
        </table>

        {f"<h3>Order notes</h3><p>{order_notes}</p>" if order_notes else ""}
    </div>
    """

    await send_email_via_graph(
        subject=f"Order awaiting payment - {payment_label} - #{order_id}",
        recipients=[settings.SALES_EMAIL],
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
