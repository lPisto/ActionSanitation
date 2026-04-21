# Action Sanitation Backend API

This is the FastAPI backend for the Action Sanitation Supply e-commerce webpage. It integrates with Spire Systems ERP and Stripe.

## Features Included:
- **Spire ERP Integration:** Handles connection to Spire via HTTP Basic Auth (`SpireAPI` user).
- **Authentication:** JWT-based user login and registration linked to Spire Customers.
- **User Management:** Create, Read, Update user details mapped back to Spire.
- **Products:** Fetch products, categories, deals, and customer-specific negotiated pricing from Spire.
- **Orders:** Save purchase orders to Spire, view order history, view invoices, and repeat purchases.
- **Stripe Payments:** `create-payment-intent` endpoint for Stripe integration.
- **Resources:** Endpoints to list catalogs, flyers, SDS (PDF downloads), gallery images, and training videos.
- **Contact:** Endpoint to send contact forms to the sales representative via email.

## Setup Instructions

1. **Virtual Environment:**
   Create and activate a virtual environment.
   ```bash
   python -m venv venv
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```

2. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Environment Variables:**
   Copy the `.env.example` file to `.env` and fill in your real credentials (including Stripe keys, secret keys, and email configuration).
   ```bash
   cp .env.example .env
   ```
   *Note: Ensure `SECRET_KEY` is a long random string in production.*

4. **Run the Development Server:**
   ```bash
   uvicorn main:app --reload
   ```

5. **API Documentation:**
   Once running, you can access the interactive API documentation at:
   - Swagger UI: http://localhost:8000/docs
   - ReDoc: http://localhost:8000/redoc
