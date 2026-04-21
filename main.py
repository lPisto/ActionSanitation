import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.endpoints import auth, products, users, orders, resources, contact, stripe_pay
from app.core.config import settings

app = FastAPI(title="Action Sanitation API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Update this to the React frontend URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/api/users", tags=["Users"])
app.include_router(products.router, prefix="/api/products", tags=["Products"])
app.include_router(orders.router, prefix="/api/orders", tags=["Orders"])
app.include_router(resources.router, prefix="/api/resources", tags=["Resources"])
app.include_router(contact.router, prefix="/api/contact", tags=["Contact"])
app.include_router(stripe_pay.router, prefix="/api/stripe", tags=["Stripe"])

@app.get("/api/health")
def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
