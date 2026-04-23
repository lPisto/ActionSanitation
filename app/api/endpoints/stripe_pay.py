import stripe
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from pydantic import BaseModel
from typing import Optional
from app.core.config import settings
from app.db.mongodb import get_database

router = APIRouter()

stripe.api_key = settings.STRIPE_API_KEY

class PaymentIntentRequest(BaseModel):
    amount: int  # Amount in cents
    currency: str = "usd"
    order_id: Optional[str] = None
    customer_email: Optional[str] = None

async def update_local_order_status(intent_id: str, new_status: str, error_msg: str = None):
    try:
        db = get_database()
        update_data = {"status": new_status}
        if error_msg:
            update_data["error_message"] = error_msg
            
        await db["orders"].update_one(
            {"id": intent_id},
            {"$set": update_data}
        )
    except Exception as e:
        print(f"Error updating local order in MongoDB: {e}")

@router.post("/create-payment-intent")
async def create_payment_intent(request: PaymentIntentRequest):
    try:
        # Crea el PaymentIntent con la metadata necesaria para identificar la orden luego
        intent_params = {
            "amount": request.amount,
            "currency": request.currency,
            "metadata": {
                "order_id": request.order_id if request.order_id else "N/A"
            }
        }
        
        if request.customer_email:
            intent_params["receipt_email"] = request.customer_email

        intent = stripe.PaymentIntent.create(**intent_params)
        
        return {
            "clientSecret": intent.client_secret,
            "paymentIntentId": intent.id
        }
        
    except stripe.error.CardError as e:
        err = e.error
        raise HTTPException(status_code=402, detail=f"Card Error: {err.message}")
    except stripe.error.RateLimitError as e:
        raise HTTPException(status_code=429, detail="Too many requests to Stripe. Please try again later.")
    except stripe.error.InvalidRequestError as e:
        raise HTTPException(status_code=400, detail=f"Invalid request to Stripe: {e.error.message}")
    except stripe.error.AuthenticationError as e:
        raise HTTPException(status_code=401, detail="Stripe Authentication Error. Check your API keys.")
    except stripe.error.APIConnectionError as e:
        raise HTTPException(status_code=503, detail="Network error communicating with Stripe.")
    except stripe.error.StripeError as e:
        raise HTTPException(status_code=500, detail="An error occurred while processing the payment with Stripe.")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, webhook_secret
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Manejar los distintos tipos de eventos
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        intent_id = payment_intent.get('id')
        
        # Sincronizamos la base de datos local (marcar como Pagada)
        await update_local_order_status(intent_id, "Paid")
        print(f"💰 ¡Webhook: Pago exitoso procesado para Intent ID: {intent_id}")

    elif event['type'] == 'payment_intent.payment_failed':
        payment_intent = event['data']['object']
        error_message = payment_intent.get('last_payment_error', {}).get('message')
        intent_id = payment_intent.get('id')
        
        # Sincronizamos la base de datos local (marcar como Fallida y guardar el error)
        await update_local_order_status(intent_id, "Payment Failed", error_message)
        print(f"❌ Webhook: Pago fallido para Intent ID: {intent_id}. Razón: {error_message}")

    return {"status": "success"}
