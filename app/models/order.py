from pydantic import BaseModel
from typing import List, Optional

class OrderItem(BaseModel):
    product_id: str
    quantity: int
    price: float
    sku: Optional[str] = None
    name: Optional[str] = None
    image: Optional[str] = None
    is_dangerous_good: Optional[bool] = False

class OrderCreate(BaseModel):
    items: List[OrderItem]
    shipping_address: str
    billing_address: Optional[str] = None
    shipping_method: Optional[str] = None
    payment_method: str
    stripe_payment_intent_id: Optional[str] = None
    local_order_id: Optional[str] = None
    payment_session_id: Optional[str] = None
    po_number: Optional[str] = None
    order_notes: Optional[str] = None

class OrderResponse(BaseModel):
    order_id: str
    status: str
    total_amount: float
