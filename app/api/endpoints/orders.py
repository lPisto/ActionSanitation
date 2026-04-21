from fastapi import APIRouter, Depends, HTTPException
from typing import List
from app.services.spire_client import spire_client
from app.api.deps import get_current_user
from app.models.user import UserInDB
from app.models.order import OrderCreate, OrderResponse

router = APIRouter()

@router.post("/", response_model=OrderResponse)
async def create_order(order: OrderCreate, current_user: UserInDB = Depends(get_current_user)):
    # 1. Map to Spire Order format
    spire_order = {
        "customer": {"customerNo": current_user.spire_customer_no},
        "items": [
            {
                "inventory": {"partNo": item.product_id},
                "orderQty": item.quantity,
                "unitPrice": item.price
            } for item in order.items
        ],
        "shipping": {"address": order.shipping_address}
        # Add more Spire required fields
    }
    
    # 2. Send to Spire
    try:
        spire_response = await spire_client.create_sales_order(spire_order)
        return {
            "order_id": spire_response.get("orderNo", "UNKNOWN"),
            "status": "Created",
            "total_amount": sum(item.quantity * item.price for item in order.items)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/history")
async def get_order_history(current_user: UserInDB = Depends(get_current_user)):
    # Fetch orders from Spire for this customer
    return await spire_client.get_customer_orders(current_user.spire_customer_no)

@router.get("/{order_id}/invoice")
async def view_invoice(order_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Fetch specific invoice details from Spire
    return {"message": f"Invoice details for order {order_id}"}

@router.post("/{order_id}/repeat")
async def repeat_purchase(order_id: str, current_user: UserInDB = Depends(get_current_user)):
    # Logic to fetch the old order, duplicate it (or add to cart)
    return {"message": f"Order {order_id} repeated and added to cart."}
