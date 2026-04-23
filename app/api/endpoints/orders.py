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
    total_amount = sum(item.quantity * item.price for item in order.items)

    # 1. Validate Stripe Payment Intent
    if order.stripe_payment_intent_id:
        try:
            intent = stripe.PaymentIntent.retrieve(order.stripe_payment_intent_id)
            if intent.status != "succeeded":
                raise HTTPException(status_code=400, detail=f"Payment not successful. Status: {intent.status}")
        except stripe.error.StripeError as e:
            raise HTTPException(status_code=400, detail=f"Stripe Error: {e.error.message}")
    else:
        raise HTTPException(status_code=400, detail="Missing stripe_payment_intent_id. Cannot create order without a valid payment.")

    # 2. Map to Spire Order format according to the API schema
    spire_order = {
        "customer": {
            "customerNo": current_user.spire_customer_no
        },
        "items": [
            {
                "inventory": {
                    "partNo": item.product_id
                },
                "orderQty": item.quantity,
                "unitPrice": str(item.price) # Spire often expects strings or decimals
            } for item in order.items
        ],
        "shippingAddress": {
            "name": f"{current_user.first_name} {current_user.last_name}",
            "line1": order.shipping_address
        },
        "referenceNo": f"Stripe ID: {order.stripe_payment_intent_id}",
        "shippingCarrier": order.shipping_method if order.shipping_method else ""
    }
    
    # 3. Send to Spire with explicit error handling for the frontend
    try:
        spire_response = await spire_client.create_sales_order(spire_order)
        spire_order_no = spire_response.get("orderNo", "UNKNOWN")
    except HTTPException as e:
        # Pasa el error HTTP lanzado por nuestro spire_client directamente al frontend
        raise HTTPException(status_code=400, detail=f"Spire ERP Error: {e.detail}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error communicating with Spire: {str(e)}")

    # 4. Save to local Database (MongoDB)
    local_order = {
        "id": order.stripe_payment_intent_id,
        "spire_order_no": spire_order_no,
        "customer_email": current_user.email,
        "total_amount": total_amount,
        "items": [{"product_id": i.product_id, "quantity": i.quantity, "price": i.price} for i in order.items],
        "status": "Paid",
        "created_at": datetime.utcnow().isoformat()
    }
    db = get_database()
    await db["orders"].insert_one(local_order)

    return {
        "order_id": spire_order_no,
        "status": "Paid & Created",
        "total_amount": total_amount
    }

@router.get("/history")
async def get_order_history(current_user: UserInDB = Depends(get_current_user)):
    # Fetch orders from Spire for this customer
    return await spire_client.get_customer_orders(current_user.spire_customer_no)

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
        # This allows the frontend to simply take this array and "setCart(items)"
        items_to_cart = []
        for item in old_order.get("items", []):
            items_to_cart.append({
                "product_id": item.get("inventory", {}).get("partNo"),
                "description": item.get("description"),
                "quantity": item.get("orderQty", 1),
                "price": item.get("unitPrice", 0)
            })

        return {
            "message": f"Order {order_id} fetched successfully for repeat purchase.",
            "cart_items": items_to_cart
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
