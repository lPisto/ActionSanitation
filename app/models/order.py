from pydantic import BaseModel
from typing import List, Optional

class OrderItem(BaseModel):
    product_id: str
    quantity: int
    price: float

class OrderCreate(BaseModel):
    items: List[OrderItem]
    shipping_address: str
    shipping_method: Optional[str] = None
    payment_method: str
    stripe_payment_intent_id: Optional[str] = None

class OrderResponse(BaseModel):
    order_id: str
    status: str
    total_amount: float
