import uvicorn
from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager
import os
from app.api.endpoints import auth, products, users, orders, resources, contact, stripe_pay, newsletter
from app.core.config import settings
from app.db.mongodb import connect_to_mongo, close_mongo_connection
from fastapi.responses import RedirectResponse

os.makedirs("static", exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await connect_to_mongo()
    yield
    # Shutdown
    await close_mongo_connection()

app = FastAPI(title="Action Sanitation API", version="1.0.0", lifespan=lifespan)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
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
app.include_router(newsletter.router, prefix="/api/newsletter", tags=["newsletter"])

@app.get("/api/health")
def health_check():
    return {"status": "ok", "environment": "production"}

@app.get("/")
def read_root():
    # Devolver JSON para evitar que Apache en cPanel tire 400 por redirección relativa
    return {"message": "Welcome to Action Sanitation API", "status": "online", "docs_url": "/actionsanitation/docs"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
