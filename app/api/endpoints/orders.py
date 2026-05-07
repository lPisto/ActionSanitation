import stripe
import os
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.services.spire_client import spire_client
from app.api.deps import get_current_user
from app.models.user import UserInDB
from app.models.order import OrderCreate, OrderResponse
from app.core.config import settings
from app.db.mongodb import get_database

router = APIRouter()
stripe.api_key = settings.STRIPE_API_KEY

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
    await db["orders"].update_one(
        {"id": order.stripe_payment_intent_id},
        {"$set": {
            "spire_order_no": spire_order_no,
            "customer_email": current_user.email,
            "total_amount": total_amount,
            "items": [{"product_id": i.product_id, "quantity": i.quantity, "price": i.price} for i in order.items],
            "status": "Paid",
            "updated_at": datetime.utcnow().isoformat()
        }},
        upsert=True
    )

    return {
        "order_id": spire_order_no,
        "status": "Paid & Created",
        "total_amount": total_amount
    }

@router.get("/me")
@router.get("/history")
async def get_order_history(current_user: UserInDB = Depends(get_current_user)):
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
        rec_copy["status"] = f"{payment_status} & {spire_status}"
        
        # Extraemos el total real cobrado en Stripe desde la BD local. Si no existe, usamos el de Spire.
        local_total = local_info.get("total_amount")
        raw_total = local_total if local_total is not None else (rec.get("grandTotal") or rec.get("total") or rec.get("subtotal") or 0)
        try:
            rec_copy["total_amount"] = float(raw_total)
        except (ValueError, TypeError):
            rec_copy["total_amount"] = 0.0
            
        formatted_orders.append(rec_copy)
        
    return formatted_orders

@router.get("/{order_id}/invoice")
async def view_invoice(order_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Fetch specific invoice details from Spire
    return await spire_client.get_sales_order_invoice(order_id)

@router.post("/{order_id}/repeat")
async def repeat_purchase(order_id: str, current_user: UserInDB = Depends(get_current_user)):
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
                items_to_cart.append({
                    "product_id": part_no,
                    "name": item.get("description", part_no),  # React espera la propiedad 'name'
                    "quantity": item.get("orderQty", 1),
                    "price": float(item.get("unitPrice", 0))   # Aseguramos que el precio sea número
                })

        return {
            "message": f"Order {order_id} fetched successfully for repeat purchase.",
            "cart_items": items_to_cart
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
