# AL AMEEN PHARMACY - Project Status

**Last Updated:** 2026-04-10
**Status:** Production
**Backend Schema:** v2.0

---

## Deployment

| Service | URL |
|---------|-----|
| Backend | https://al-ameen-pharmacy-production.up.railway.app |
| Frontend | https://al-ameen-pharmacy-production-8378.up.railway.app |
| Admin | https://al-ameen-pharmacy-production.up.railway.app/admin |
| API Base | https://al-ameen-pharmacy-production.up.railway.app/api |

**Infrastructure:** Railway (backend + frontend), Neon PostgreSQL, Cloudinary CDN

---

## Tech Stack

### Backend
- Django 5.x + Django REST Framework
- PostgreSQL (Neon)
- JWT authentication (djangorestframework-simplejwt)
- Cloudinary for images
- Gmail SMTP for emails

### Frontend
- React 18
- React Router v6
- Axios with JWT interceptors
- Context API (AuthContext, CartContext)

---

## Backend Schema v2.0

### Models

| Model | Purpose | Key Fields |
|-------|---------|------------|
| Brand | Product brands | name, slug, logo |
| Category | Hierarchical categories | name, slug, parent (self-FK), is_active |
| Product | Product catalog | name, slug, brand (FK), category (FK), price, status, short_description |
| ProductImage | Multiple images per product | product (FK), image, is_primary, alt_text |
| Supplier | Vendor info | name, slug, contact details |
| ProductSupplier | Product-supplier links | supplier_sku, is_preferred |
| Cart | User shopping cart | user (1:1) |
| CartItem | Items in cart | cart (FK), product (FK), quantity |
| Address | Delivery addresses | Dubai-specific fields, is_default |
| Order | Completed orders | order_number, denormalized delivery info, status |
| OrderItem | Order line items | product_name, price_at_purchase (snapshots) |

### Product Fields
- `name`, `slug` (auto-generated, unique)
- `brand` (FK, nullable), `category` (FK, nullable)
- `short_description` (required), `detailed_description` (optional)
- `price`, `stock_quantity`, `sku`, `barcode`
- `status`: draft | active | archived
- `requires_prescription`, `is_featured`
- `dosage`, `pack_size`, `active_ingredient`
- `meta_title`, `meta_description` (SEO)

### Serializer Output Fields
Products return these fields to frontend:
- `id`, `name`, `slug`
- `brand`, `brand_name`, `brand_slug`
- `category`, `category_name`, `category_slug`
- `short_description`, `detailed_description`
- `price`, `stock_quantity`, `in_stock`
- `primary_image_url` (computed from ProductImage)
- `images` (array, detail view only)
- `dosage`, `pack_size`, `requires_prescription`, `is_featured`, `status`

---

## API Endpoints

### Authentication
| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/token/` | JWT login |
| POST | `/api/token/refresh/` | Refresh token |
| POST | `/api/register/` | Register user |
| GET | `/api/me/` | Current user info |
| POST | `/api/password-reset/` | Request reset |
| POST | `/api/password-reset/confirm/` | Confirm reset |

### Catalog (Public, slug-based lookup)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/brands/` | List brands |
| GET | `/api/brands/{slug}/` | Brand by slug |
| GET | `/api/categories/` | Hierarchical categories |
| GET | `/api/categories/?flat=true` | Flat list for dropdowns |
| GET | `/api/categories/{slug}/` | Category by slug |
| GET | `/api/products/` | List active products |
| GET | `/api/products/?search=query` | Full-text search |
| GET | `/api/products/?category=slug` | Filter by category |
| GET | `/api/products/?brand=slug` | Filter by brand |
| GET | `/api/products/?featured=true` | Featured only |
| GET | `/api/products/{slug}/` | Product by slug |

### Cart (Authenticated)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cart/` | Get user's cart |
| POST | `/api/cart/add_item/` | Add item (product_id, quantity) |
| PATCH | `/api/cart/update_item/` | Update quantity |
| DELETE | `/api/cart/remove_item/` | Remove item |
| DELETE | `/api/cart/clear/` | Clear cart |

### Orders (Authenticated)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders/` | List user's orders |
| POST | `/api/orders/` | Create order from cart |
| PATCH | `/api/orders/{id}/update_status/` | Admin: update status |

### Addresses (Authenticated)
| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/addresses/` | List addresses |
| POST | `/api/addresses/` | Create address |
| PUT/DELETE | `/api/addresses/{id}/` | Update/delete |

### Admin Only
- CRUD on `/api/brands/`, `/api/categories/`, `/api/products/`
- `/api/suppliers/`, `/api/product-suppliers/`
- `/api/product-images/`

---

## Frontend Structure

```
frontend/src/
├── components/
│   ├── Navbar.js            # Header, search with suggestions
│   ├── ProductGrid.js       # Product cards with quantity controls
│   ├── ProductModal.js      # Quick view modal
│   ├── Cart.js              # Cart drawer
│   ├── WhatsAppButton.js    # Floating CTA
│   ├── ProductManagement.js # Admin: product CRUD
│   └── OrderManagement.js   # Admin: order management
├── pages/
│   ├── Home.js              # Homepage (hero, products, footer)
│   ├── ProductDetail.js     # Single product page
│   ├── Login.js, Register.js
│   ├── ForgotPassword.js, ResetPassword.js
│   ├── Profile.js           # User profile & order history
│   ├── Checkout.js          # Checkout flow
│   ├── OrderConfirmation.js
│   ├── AdminDashboard.js
│   └── About.js
├── context/
│   ├── AuthContext.js       # User auth state
│   └── CartContext.js       # Shopping cart state
├── api.js                   # API helpers
├── App.js                   # Routes
└── App.css                  # Design system variables
```

### Key Frontend Behaviors
- **Product display:** Uses `primary_image_url`, `short_description`, `brand_name`
- **Product navigation:** Uses `slug` for detail pages and API calls
- **Cart operations:** Uses numeric `product.id` for add/update
- **Search:** Client-side filter on `name`, `short_description`, `category_name`, `brand_name`

---

## Design System

### Colors (App.css)
```css
--primary: #0D9488;      /* Healthcare teal */
--primary-light: #14B8A6;
--primary-dark: #0F766E;
--secondary: #0EA5E9;    /* Trust blue */
--accent: #F59E0B;       /* Warm amber */
--success: #22C55E;
--error: #EF4444;
```

### Responsive Breakpoints
- Desktop: > 1024px
- Tablet: 768px - 1024px
- Mobile: < 768px

---

## Features

### Customer
- Product browsing with search and category filter
- Quick view modal + full product detail page
- Cart with optimistic UI updates
- Checkout with saved addresses
- Order history in profile
- Password reset via email

### Admin
- Product management (create, edit, delete)
- Brand selection dropdown
- Product status workflow (draft/active/archived)
- Order status management
- Django admin at `/admin`

---

## Environment Variables

### Backend (.env)
```
DATABASE_URL=postgresql://...
DJANGO_SECRET_KEY=...
DEBUG=0
ALLOWED_HOSTS=...
CORS_ALLOWED_ORIGINS=...
CLOUDINARY_URL=cloudinary://...
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
```

### Frontend (.env)
```
REACT_APP_API_URL=https://al-ameen-pharmacy-production.up.railway.app/api
CI=false
```

---

## Key Files

| File | Purpose |
|------|---------|
| `backend/BACKEND_SCHEMA.md` | Full backend documentation |
| `backend/api/models.py` | All Django models |
| `backend/api/serializers.py` | API serializers |
| `backend/api/views.py` | ViewSets |
| `frontend/src/context/CartContext.js` | Cart state & API calls |
| `frontend/src/components/ProductGrid.js` | Product card display |
| `frontend/src/pages/ProductDetail.js` | Product detail page |

---

## Notes for Development

1. **Slug-based API:** Products, categories, and brands use slug for lookup (e.g., `/products/panadol-extra/`)
2. **Cart uses numeric ID:** `add_item` expects `product_id` (integer), not slug
3. **Image handling:** Products use `primary_image_url` computed from ProductImage table
4. **Non-admin users:** Only see products with `status='active'`
5. **Frontend admin:** Uses brand dropdown, not text field
