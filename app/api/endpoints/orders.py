import stripe
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from typing import List, Optional
from pydantic import BaseModel
from app.services.spire_client import spire_client
from app.api.deps import get_current_user
from app.models.user import UserInDB
from app.models.order import OrderCreate, OrderResponse
from app.core.config import settings
from app.db.mongodb import get_database
from app.services.email_service import send_order_confirmation_email
import json
import httpx
import xml.etree.ElementTree as ET

router = APIRouter()
stripe.api_key = settings.STRIPE_API_KEY

class ShippingItem(BaseModel):
    product_id: str
    quantity: int
    weight_kg: float = 0.0
    is_dangerous_good: bool = False

class ShippingRequest(BaseModel):
    postal_code: str
    items: List[ShippingItem]

def get_product_weight(product_id: str) -> float:
    try:
        with open("products.json", "r", encoding="utf-8") as f:
            data = json.load(f)
            for item in data.get("records", []):
                if item.get("partNo") == product_id:
                    weight_str = item.get("weight", "0")
                    return float(weight_str)
    except Exception as e:
        print(f"Error reading weight for {product_id}: {e}")
    return 0.0

@router.post("/calculate-shipping")
async def calculate_shipping(req: ShippingRequest):
    total_weight = 0.0
    for item in req.items:
        w = get_product_weight(item.product_id)
        # If weight is 0, we assume at least 0.5 kg for shipping purposes
        weight_to_add = w if w > 0 else 0.5
        total_weight += (weight_to_add * item.quantity)
    
    if total_weight == 0:
        total_weight = 1.0

    # Ensure max length for postal code and formatting (Canada Post expects without spaces)
    dest_zip = req.postal_code.replace(" ", "").upper()

    xml_request = f"""<?xml version="1.0" encoding="UTF-8"?>
<mailing-scenario xmlns="http://www.canadapost.ca/ws/ship/rate-v4">
  <customer-number>1234567</customer-number>
  <parcel-characteristics>
    <weight>{total_weight:.2f}</weight>
  </parcel-characteristics>
  <origin-postal-code>K2B8J6</origin-postal-code>
  <destination>
    <domestic>
      <postal-code>{dest_zip}</postal-code>
    </domestic>
  </destination>
</mailing-scenario>"""

    # We will try to call the Canada Post API.
    # Note: Without a valid API key, this will likely fail. We provide a fallback.
    url = "https://ct.soa-gw.canadapost.ca/rs/ship/price"
    
    # We use a dummy API key for the development environment. In production, these should be env vars.
    api_user = os.getenv("CANADA_POST_USER", "dummy")
    api_pass = os.getenv("CANADA_POST_PASS", "dummy")
    
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                url,
                content=xml_request,
                headers={"Content-Type": "application/vnd.cpc.ship.rate-v4+xml", "Accept": "application/vnd.cpc.ship.rate-v4+xml"},
                auth=(api_user, api_pass),
                timeout=5.0
            )
            
        if resp.status_code == 200:
            root = ET.fromstring(resp.text)
            # Find all prices and return the minimum or standard (DOM.RP)
            namespaces = {'ns': 'http://www.canadapost.ca/ws/ship/rate-v4'}
            quotes = root.findall('.//ns:price-quote', namespaces)
            prices = []
            for quote in quotes:
                due = quote.find('.//ns:due', namespaces)
                if due is not None:
                    prices.append(float(due.text))
            
            if prices:
                return {"shipping_cost": min(prices)}
    except Exception as e:
        print(f"Canada Post API Error: {e}")
    
    # Fallback flat rate if API fails or we don't have credentials
    return {"shipping_cost": 15.0}

@router.post("/", response_model=OrderResponse)
async def create_order(order: OrderCreate, current_user: UserInDB = Depends(get_current_user)):
    total_amount = 0.0

    # 1. Validate Stripe Payment Intent
    if order.stripe_payment_intent_id:
        try:
            intent = stripe.PaymentIntent.retrieve(order.stripe_payment_intent_id)
            if intent.status != "succeeded":
                raise HTTPException(status_code=400, detail=f"Payment not successful. Status: {intent.status}")
            # Extraemos el total real cobrado por Stripe (incluye taxes y shipping)
            total_amount = intent.amount / 100.0
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe Error: {e.error.message}")
    else:
        raise HTTPException(status_code=400, detail="Missing stripe_payment_intent_id. Cannot create order without a valid payment.")

    # 2. Map to Spire Order format according to the API schema
    spire_order = {
        "customer": {
            "customerNo": current_user.spire_customer_no
        },
        "status": "O",
        "salesperson": "00",
        "items": [
            {
                "partNo": item.product_id,
                "orderQty": item.quantity,
                "unitPrice": str(item.price) # Spire often expects strings or decimals
            } for item in order.items
        ],
        "shippingAddress": {
            "name": f"{current_user.first_name} {current_user.last_name}"[:40],
            "line1": order.shipping_address[:50] if order.shipping_address else "",
            "salesperson": {
                "code":"WEB"   
            },
            "territory": {
                "code":"WEB"   
            },
        },
        "referenceNo": order.stripe_payment_intent_id[:20],
        "shippingCarrier": order.shipping_method[:15] if order.shipping_method else ""
    }
    
    # 3. Send to Spire with explicit error handling for the frontend
    try:
        spire_response = await spire_client.create_sales_order(spire_order)
        spire_order_no = spire_response.get("orderNo") or spire_response.get("id") or "UNKNOWN"
        if spire_order_no == "UNKNOWN":
            print(f"Warning: Spire response missing orderNo. Response: {spire_response}")
            
            # Fallback: Recuperar la orden recién creada consultando por el referenceNo (Stripe ID)
            customer_orders = await spire_client.get_customer_orders(current_user.spire_customer_no)
            for rec in customer_orders.get("records", []):
                if rec.get("referenceNo") == order.stripe_payment_intent_id[:20]:
                    spire_order_no = str(rec.get("orderNo") or rec.get("id") or "UNKNOWN")
                    break
    except HTTPException as e:
        # Registra el error detallado en la consola del backend
        print(f"Spire ERP Error details: {e.detail}")
        raise HTTPException(status_code=400, detail="There was an issue processing your order with our side. Please contact support.")
    except Exception as e:
        print(f"Unexpected error communicating with Spire: {str(e)}")
        raise HTTPException(status_code=500, detail="Unexpected error communicating with the server. Please try again later.")

    # 4. Actualizar la orden local en la base de datos de "Pending" a "Paid"
    db = get_database()
    existing_order = await db["orders"].find_one({"id": order.stripe_payment_intent_id})
    
    if existing_order and existing_order.get("items"):
        saved_items = existing_order.get("items")
    else:
        # Intentamos rescatar los campos extra si el modelo lo permite, sino usamos valores por defecto
        saved_items = []
        for i in order.items:
            saved_items.append({"product_id": i.product_id, "quantity": i.quantity, "price": i.price, "name": getattr(i, "name", ""), "sku": getattr(i, "sku", i.product_id), "image": getattr(i, "image", None)})

    await db["orders"].update_one(
        {"id": order.stripe_payment_intent_id},
        {"$set": {
            "spire_order_no": spire_order_no,
            "customer_email": current_user.email,
            "total_amount": total_amount,
            "shipping_address": order.shipping_address,
            "items": saved_items,
            "status": "Paid",
            "updated_at": datetime.utcnow().isoformat()
        }},
        upsert=True
    )

    # 5. Enviar email de confirmación
    try:
        await send_order_confirmation_email(
            to_email=current_user.email,
            name=f"{current_user.first_name} {current_user.last_name}".strip(),
            order_id=spire_order_no,
            items=saved_items,
            total_amount=total_amount,
            shipping_address=order.shipping_address or "N/A"
        )
    except Exception as e:
        print(f"Error sending order confirmation email: {e}")

    return {
        "order_id": spire_order_no,
        "status": "Paid & Created",
        "total_amount": total_amount
    }

@router.get("/me")
@router.get("/history")
async def get_order_history(request: Request, current_user: UserInDB = Depends(get_current_user)):
    # Fetch orders from Spire for this customer
    response = await spire_client.get_customer_orders(current_user.spire_customer_no)
    records = response.get("records", [])
    
    # Extraemos el estado local de pagos desde MongoDB
    db = get_database()
    local_orders = await db["orders"].find({"customer_email": current_user.email}).to_list(length=None)
    local_data = {str(o.get("spire_order_no")): o for o in local_orders if o.get("spire_order_no")}
    
    status_map = {"O": "Processing", "C": "Completed", "H": "On Hold", "Q": "Quote", "I": "Invoiced"}
    
    formatted_orders = []
    for rec in records:
        rec_copy = rec.copy()
        
        order_no = str(rec.get("orderNo") or rec.get("id"))
        local_info = local_data.get(order_no, {})
        payment_status = local_info.get("status", "Paid") # Por defecto 'Paid' si ya logró entrar a Spire
        spire_status = status_map.get(rec.get("status", "O"), rec.get("status", "O"))
        
        # Combinamos ambos estados para mayor claridad al cliente
        rec_copy["status"] = f"{payment_status}"
        
        # Extraemos el total real cobrado en Stripe desde la BD local. Si no existe, usamos el de Spire.
        local_total = local_info.get("total_amount")
        raw_total = local_total if local_total is not None else (rec.get("grandTotal") or rec.get("total") or rec.get("subtotal") or 0)
        try:
            rec_copy["total_amount"] = float(raw_total)
        except (ValueError, TypeError):
            rec_copy["total_amount"] = 0.0
            
        # Extraemos la dirección de envío y la fecha
        shipping_addr = rec.get("shippingAddress", {})
        rec_copy["ship_to"] = shipping_addr.get("line1") or local_info.get("shipping_address") or ""
        rec_copy["shipping_city"] = shipping_addr.get("city") or ""
        rec_copy["created_at"] = local_info.get("created_at") or rec.get("orderDate") or ""
            
        # Extraemos los items combinando Spire (cantidades reales) y local (nombres que Spire no devuelve)
        spire_items = rec.get("items") or []
        local_items = local_info.get("items") or []
        
        mapped_items = []
        if spire_items:
            for it in spire_items:
                part_no = it.get("partNo") or it.get("inventory", {}).get("partNo")
                
                # Buscamos si tenemos guardado el nombre original en nuestra BD local
                local_match = next((li for li in local_items if li.get("product_id") == part_no), {})
                
                name = it.get("description")
                image = local_match.get("image")
                
                if not name or str(name).strip() == "":
                    name = local_match.get("name")
                    
                    # Si es una orden antigua y la BD local no tiene el nombre, consultamos al inventario de Spire
                    if not name:
                        try:
                            from app.api.endpoints.products import normalize_product_data
                            product = await spire_client.get_product(part_no)
                            product = normalize_product_data(product, request)
                            name = product.get("description") or part_no
                            image = product.get("image")
                        except Exception:
                            name = part_no

                mapped_items.append({
                    "product_id": part_no,
                    "name": name,
                    "sku": local_match.get("sku") or part_no,
                    "quantity": it.get("orderQty", 1),
                    "price": float(it.get("unitPrice", 0)) if it.get("unitPrice") is not None else 0.0,
                    "image": image
                })
        else:
            mapped_items = local_items
            
        rec_copy["items"] = mapped_items

        formatted_orders.append(rec_copy)
        
    return formatted_orders

@router.get("/{order_id}/invoice")
async def view_invoice(order_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Fetch specific invoice details from Spire
    return await spire_client.get_sales_order_invoice(order_id)

@router.post("/{order_id}/repeat")
async def repeat_purchase(order_id: str, request: Request, current_user: UserInDB = Depends(get_current_user)):
    try:
        # Fetch the old order from Spire
        old_order = await spire_client.get_sales_order(order_id)
        
        # Security check: Ensure this order actually belongs to the current user
        if old_order.get("customer", {}).get("customerNo") != current_user.spire_customer_no:
            raise HTTPException(status_code=403, detail="You do not have permission to access this order.")

        # Extract the items to send back to the frontend cart
        items_to_cart = []
        for item in old_order.get("items", []):
            # Buscamos el partNo en la raíz (o dentro de inventory por seguridad)
            part_no = item.get("partNo") or item.get("inventory", {}).get("partNo")
            if part_no:
                name = item.get("description") or part_no
                image = None
                
                # Intentamos recuperar el producto desde el inventario para obtener la imagen y nombre real
                try:
                    from app.api.endpoints.products import normalize_product_data
                    product = await spire_client.get_product(part_no)
                    product = normalize_product_data(product, request)
                    name = product.get("description") or name
                    image = product.get("image")
                except Exception:
                    pass

                items_to_cart.append({
                    "product_id": part_no,
                    "name": name,  # React espera la propiedad 'name'
                    "quantity": item.get("orderQty", 1),
                    "price": float(item.get("unitPrice", 0)),   # Aseguramos que el precio sea número
                    "image": image
                })

        return {
            "message": f"Order {order_id} fetched successfully for repeat purchase.",
            "cart_items": items_to_cart
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
