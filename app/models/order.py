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

class OrderAddress(BaseModel):
    name: Optional[str] = None
    line1: Optional[str] = None
    city: Optional[str] = None
    prov_state: Optional[str] = None
    postal_code: Optional[str] = None
    country: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None

class OrderCreate(BaseModel):
    items: List[OrderItem]
    shipping_address: str
    billing_address: Optional[str] = None
    shipping_address_details: Optional[OrderAddress] = None
    billing_address_details: Optional[OrderAddress] = None
    shipping_method: Optional[str] = None
    payment_method: str
    stripe_payment_intent_id: Optional[str] = None
    local_order_id: Optional[str] = None
    payment_session_id: Optional[str] = None
    po_number: Optional[str] = None
    order_notes: Optional[str] = None
    free_tshirt_size: Optional[str] = None
    shipping_cost: Optional[float] = 0.0
    tax_amount: Optional[float] = 0.0
    total_amount: Optional[float] = None

class OrderResponse(BaseModel):
    order_id: str
    status: str
    total_amount: float
