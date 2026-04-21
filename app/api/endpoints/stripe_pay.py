import stripe
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from app.core.config import settings

router = APIRouter()

stripe.api_key = settings.STRIPE_API_KEY

class PaymentIntentRequest(BaseModel):
    amount: int # Amount in cents
    currency: str = "usd"

@router.post("/create-payment-intent")
async def create_payment_intent(request: PaymentIntentRequest):
    try:
        intent = stripe.PaymentIntent.create(
            amount=request.amount,
            currency=request.currency,
            # automatic_payment_methods={"enabled": True},
        )
        return {"clientSecret": intent.client_secret}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
