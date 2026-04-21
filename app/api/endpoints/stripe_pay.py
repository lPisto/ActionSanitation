import stripe
from fastapi import APIRouter, HTTPException, Depends, Request, Header
from pydantic import BaseModel
from typing import Optional
from app.core.config import settings

router = APIRouter()

stripe.api_key = settings.STRIPE_API_KEY

class PaymentIntentRequest(BaseModel):
    amount: int  # Amount in cents
    currency: str = "usd"
    order_id: Optional[str] = None
    customer_email: Optional[str] = None

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
        # El pago con tarjeta falló (ej: fondos insuficientes, tarjeta expirada)
        err = e.error
        raise HTTPException(status_code=402, detail=f"Card Error: {err.message}")
    except stripe.error.RateLimitError as e:
        # Demasiadas peticiones a la API de Stripe
        raise HTTPException(status_code=429, detail="Too many requests to Stripe. Please try again later.")
    except stripe.error.InvalidRequestError as e:
        # Parámetros inválidos enviados a la API de Stripe
        raise HTTPException(status_code=400, detail=f"Invalid request to Stripe: {e.error.message}")
    except stripe.error.AuthenticationError as e:
        # Error de autenticación con las keys de Stripe (configuración incorrecta)
        raise HTTPException(status_code=401, detail="Stripe Authentication Error. Check your API keys.")
    except stripe.error.APIConnectionError as e:
        # Error de red al comunicarse con Stripe
        raise HTTPException(status_code=503, detail="Network error communicating with Stripe.")
    except stripe.error.StripeError as e:
        # Error genérico de Stripe
        raise HTTPException(status_code=500, detail="An error occurred while processing the payment with Stripe.")
    except Exception as e:
        # Cualquier otro error en nuestra aplicación
        raise HTTPException(status_code=500, detail=f"An unexpected error occurred: {str(e)}")

@router.post("/webhook")
async def stripe_webhook(request: Request, stripe_signature: str = Header(None)):
    """
    Endpoint para recibir eventos asincrónicos desde Stripe (Webhooks).
    Por ejemplo, cuando un pago que requería autenticación 3D Secure finalmente se completa.
    """
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET
    
    # Stripe requiere el payload crudo (raw body) para verificar la firma digital
    payload = await request.body()

    try:
        event = stripe.Webhook.construct_event(
            payload, stripe_signature, webhook_secret
        )
    except ValueError as e:
        # Payload inválido
        raise HTTPException(status_code=400, detail="Invalid payload")
    except stripe.error.SignatureVerificationError as e:
        # Firma inválida (alguien más intentó llamar a nuestro webhook)
        raise HTTPException(status_code=400, detail="Invalid signature")

    # Manejar los distintos tipos de eventos
    if event['type'] == 'payment_intent.succeeded':
        payment_intent = event['data']['object']
        order_id = payment_intent.get('metadata', {}).get('order_id')
        amount = payment_intent.get('amount')
        
        # AQUÍ: Deberías actualizar el estado de tu orden en Spire o en tu BD local
        # marcando la orden como "Pagada" / "Paid"
        print(f"💰 ¡Pago exitoso recibido! Orden ID: {order_id}, Monto: {amount}")

    elif event['type'] == 'payment_intent.payment_failed':
        payment_intent = event['data']['object']
        error_message = payment_intent.get('last_payment_error', {}).get('message')
        order_id = payment_intent.get('metadata', {}).get('order_id')
        
        # AQUÍ: Deberías notificar al usuario o actualizar la orden a "Pago Fallido"
        print(f"❌ Pago fallido para la Orden ID: {order_id}. Razón: {error_message}")

    # Stripe espera un 200 OK para confirmar que recibimos el webhook
    return {"status": "success"}
