# Action Sanitation API Documentation

Este documento detalla todos los endpoints disponibles en el backend de Action Sanitation (FastAPI). Sirve como guía y base fundamental para el desarrollo del Frontend.

**URL Base Backend (Producción):** `https://action-sanitation-backend.vercel.app`
**URL Base Frontend (Producción):** `https://action-sanitation-webpage.vercel.app`
**URL Base (Local):** `http://localhost:8000`

---

## Índice

- [Autenticación (Auth)](#1-autenticación-auth)
- [Usuarios (Users)](#2-usuarios-users)
- [Productos (Products)](#3-productos-products)
- [Pedidos (Orders)](#4-pedidos-orders)
- [Recursos (Resources)](#5-recursos-resources)
- [Contacto (Contact)](#6-contacto-contact)
- [Stripe (Pagos)](#7-stripe-pagos)
- [Health Check](#8-health-check)

---

## 1. Autenticación (Auth)

Endpoints encargados del registro y login de usuarios. Todas las rutas tienen el prefijo `/api/auth`.

### Registro de Usuario
- **Endpoint:** `POST /api/auth/register`
- **Descripción:** Crea un nuevo usuario en la base de datos local y lo sincroniza creando un cliente en Spire ERP.
- **Request Body (JSON):**
  ```json
  {
    "first_name": "string",
    "last_name": "string",
    "company": "string (Opcional)",
    "phone_number": "string",
    "city": "string",
    "street_address": "string",
    "zip": "string",
    "state_province": "string",
    "country": "string",
    "email": "user@example.com",
    "password": "string",
    "confirm_password": "string"
  }
  ```
- **Respuesta Exitosa (200 OK):** Retorna el modelo `UserInDB`.
  ```json
  {
    "id": "string",
    "spire_customer_no": "string",
    "email": "user@example.com",
    "first_name": "string",
    "last_name": "string",
    "company": "string",
    "phone_number": "string",
    "city": "string",
    "street_address": "string",
    "zip": "string",
    "state_province": "string",
    "country": "string"
  }
  ```
- **Errores Posibles:** `400 Bad Request` (Las contraseñas no coinciden, Email ya registrado, o Error en Spire ERP), `500 Internal Server Error`.

### Login de Usuario
- **Endpoint:** `POST /api/auth/login`
- **Descripción:** Autentica a un usuario y retorna un token JWT para peticiones protegidas.
- **Request Body (`application/x-www-form-urlencoded` - Ojo, no es JSON):**
  - `username`: Email del usuario
  - `password`: Contraseña
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "access_token": "string",
    "token_type": "bearer"
  }
  ```
- **Errores Posibles:** `400 Bad Request` (Email o contraseña incorrectos).

---

## 2. Usuarios (Users)

Endpoints para la gestión del usuario actual. Todas las rutas tienen el prefijo `/api/users` y requieren el Header `Authorization: Bearer <token>`.

### Obtener Perfil Actual
- **Endpoint:** `GET /api/users/me`
- **Descripción:** Retorna la información del usuario autenticado.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Respuesta Exitosa (200 OK):** Retorna el modelo `UserInDB` (Ver formato en Registro).

### Actualizar Perfil Actual
- **Endpoint:** `PUT /api/users/me`
- **Descripción:** Actualiza los datos del usuario en la base de datos local y los sincroniza con Spire ERP.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Request Body (JSON):** Todos los campos son opcionales (`UserUpdate`). Solo envía los que desees modificar.
  ```json
  {
    "first_name": "string",
    "last_name": "string",
    "company": "string",
    "phone_number": "string",
    "city": "string",
    "street_address": "string",
    "zip": "string",
    "state_province": "string",
    "country": "string",
    "email": "user@example.com"
  }
  ```
- **Respuesta Exitosa (200 OK):** Retorna el modelo `UserInDB` actualizado.
- **Errores Posibles:** `400 Bad Request` (Error en Spire ERP).

---

## 3. Productos (Products)

Endpoints para obtener información de productos desde Spire ERP. Todas las rutas tienen el prefijo `/api/products`.

### Obtener Productos
- **Endpoint:** `GET /api/products/`
- **Descripción:** Retorna la lista de productos paginados/filtrados desde Spire ERP.
- **Parámetros de Query:**
  - `limit` (int, por defecto 100)
  - `start` (int, por defecto 0)
  - `group` (string, opcional): Filtrar por grupo de inventario.
  - `department` (string, opcional): Filtrar por código de departamento.
- **Respuesta:** JSON devuelto directamente desde Spire ERP.

### Obtener Categorías
- **Endpoint:** `GET /api/products/categories`
- **Descripción:** Obtiene los Grupos de Inventario (Inventory Groups) de Spire.
- **Respuesta:** JSON devuelto por Spire ERP.

### Obtener Departamentos
- **Endpoint:** `GET /api/products/departments`
- **Descripción:** Obtiene los Departamentos de Ventas (Sales Departments) de Spire.
- **Respuesta:** JSON devuelto por Spire ERP.

### Obtener Producto por ID
- **Endpoint:** `GET /api/products/{product_id}`
- **Parámetros de Path:** `product_id` (string)
- **Respuesta:** Detalle completo del producto devuelto por Spire ERP.

### Obtener Precio Especial
- **Endpoint:** `GET /api/products/{product_id}/pricing`
- **Descripción:** Obtiene el precio negociado/especial para el cliente autenticado en un producto específico.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Parámetros de Path:** `product_id` (string)
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "product_id": "string",
    "special_price": 10.50,
    "currency": "USD"
  }
  ```
  O si no hay precio especial:
  ```json
  {
    "product_id": "string",
    "special_price": null,
    "message": "No special pricing found"
  }
  ```

### Obtener Ofertas Activas (Deals)
- **Endpoint:** `GET /api/products/deals/all`
- **Descripción:** Obtiene todas las ofertas activas configuradas en Spire.
- **Respuesta:** JSON de deals devuelto por Spire.

---

## 4. Pedidos (Orders)

Gestión de órdenes, compras recurrentes e historial. Prefijo: `/api/orders`.
Requieren `Authorization: Bearer <token>`.

### Crear un Pedido
- **Endpoint:** `POST /api/orders/`
- **Descripción:** Valida el pago en Stripe, crea la Sales Order en Spire y la guarda en la base de datos local.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Request Body (JSON):**
  ```json
  {
    "items": [
      {
        "product_id": "string",
        "quantity": 2,
        "price": 15.99
      }
    ],
    "shipping_address": "string",
    "shipping_method": "string (Opcional)",
    "payment_method": "string",
    "stripe_payment_intent_id": "string (Obligatorio, provisto por Stripe tras autorizar pago)"
  }
  ```
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "order_id": "string (Spire Order No)",
    "status": "Paid & Created",
    "total_amount": 31.98
  }
  ```
- **Errores:** `400 Bad Request` (Error en validación Stripe o en la creación en Spire ERP).

### Historial de Pedidos
- **Endpoint:** `GET /api/orders/history`
- **Descripción:** Retorna el historial de compras para el cliente logueado desde Spire ERP.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Respuesta:** JSON devuelto por Spire.

### Ver Factura de Pedido (Invoice)
- **Endpoint:** `GET /api/orders/{order_id}/invoice`
- **Parámetros de Path:** `order_id` (string)
- **Descripción:** Retorna el detalle de la factura desde Spire ERP.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Respuesta:** JSON de factura devuelto por Spire.

### Repetir un Pedido (Repeat Purchase)
- **Endpoint:** `POST /api/orders/{order_id}/repeat`
- **Parámetros de Path:** `order_id` (string)
- **Descripción:** Busca una orden antigua y devuelve los ítems listos para ser agregados al carrito (React state). Valida que la orden pertenezca al usuario logueado.
- **Header Requerido:** `Authorization: Bearer <token>`
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "message": "Order 12345 fetched successfully for repeat purchase.",
    "cart_items": [
      {
        "product_id": "string",
        "description": "string",
        "quantity": 2,
        "price": 15.99
      }
    ]
  }
  ```
- **Errores:** `403 Forbidden` si la orden no le pertenece.

---

## 5. Recursos (Resources)

Gestión de descargas, galería y capacitaciones. Prefijo: `/api/resources`.

### Obtener Recursos
- `GET /api/resources/downloads/catalogs`
- `GET /api/resources/downloads/flyers`
- `GET /api/resources/downloads/sds`
- `GET /api/resources/gallery`
- `GET /api/resources/training`

**Respuesta Exitosa (200 OK):** Lista de recursos, ejemplo (varían según categoría):
```json
[
  {
    "_id": "string",
    "id": "string",
    "category": "catalogs",
    "name": "Catálogo 2024",
    "url": "/static/catalogs/abcd_catalogo.pdf"
  }
]
```
*(Nota: `gallery` usa `image_url` y `title`. `training` usa `video_url` y `title` en lugar de `url` y `name`).*

### Subir Recurso
- **Endpoint:** `POST /api/resources/upload/{category}`
- **Parámetros de Path:** `category` (catalogs, flyers, sds, gallery, training)
- **Request Body (`multipart/form-data`):**
  - `file`: (UploadFile, obligatorio) Archivo físico.
  - `title`: (string, opcional) Título a mostrar, si se omite, usa el nombre del archivo.
- **Respuesta Exitosa:** `{"message": "...", "data": { ... }}`

### Eliminar Recurso
- **Endpoint:** `DELETE /api/resources/{category}/{item_id}`
- **Parámetros de Path:** `category` (string), `item_id` (string)
- **Respuesta:** `{"message": "Resource deleted successfully"}`

---

## 6. Contacto (Contact)

Prefijo: `/api/contact`.

### Enviar Formulario de Contacto
- **Endpoint:** `POST /api/contact/`
- **Descripción:** Guarda el mensaje en base de datos y envía un email al representante de ventas.
- **Request Body (JSON):**
  ```json
  {
    "name": "string",
    "email": "user@example.com",
    "subject": "string",
    "message": "string"
  }
  ```
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "message": "Message saved and sent successfully to sales representative."
  }
  ```

---

## 7. Stripe (Pagos)

Prefijo: `/api/stripe`.

### Crear Intención de Pago (Payment Intent)
- **Endpoint:** `POST /api/stripe/create-payment-intent`
- **Descripción:** Se debe llamar a este endpoint antes de procesar un pago en el frontend. El frontend usará el `clientSecret` para el elemento de Stripe.
- **Request Body (JSON):**
  ```json
  {
    "amount": 1500, // IMPORTANTE: En centavos. Ejemplo 1500 = $15.00
    "currency": "usd", // Opcional, defecto "usd"
    "order_id": "string (Opcional)",
    "customer_email": "user@example.com (Opcional)"
  }
  ```
- **Respuesta Exitosa (200 OK):**
  ```json
  {
    "clientSecret": "pi_12345_secret_67890",
    "paymentIntentId": "pi_12345"
  }
  ```

### Webhook de Stripe
- **Endpoint:** `POST /api/stripe/webhook`
- **Descripción:** Endpoint usado exclusivamente por los servidores de Stripe para avisar sobre el éxito o fallo de un pago de forma asíncrona.
- **Requerido:** Header `stripe-signature` y cuerpo crudo del evento.

---

## 8. Health Check

- **Endpoint:** `GET /api/health`
- **Descripción:** Retorna el estado de salud del servidor (usado para monitoreo o pings de vida).
- **Respuesta (200 OK):**
  ```json
  {
    "status": "ok"
  }
  ```
