# Al Ameen Pharmacy E-Commerce Platform

A full-stack e-commerce platform for [Al Ameen Pharmacy](https://www.ameenpharmacy.ae/) — a DHA licensed pharmacy with 4 branches in Dubai.

**Live site:** https://www.ameenpharmacy.ae/

---

## Features

### Customer-Facing
- **Product Catalog** — 107 products across 10 parent / 33 child categories, sourced from the official brochure
- **Multi-Image Gallery** — Multiple images per product with arrow/thumbnail navigation
- **Category & Price Filters** — Sticky sidebar with 10 categories + AED price range bands; active filters display with clear functionality
- **Full-Text Search** — Two-tier Postgres search: ILIKE for short queries (<3 chars), weighted full-text ranking for longer ones
- **show_price** — Price shown only when enabled per-product; otherwise a WhatsApp inquiry link appears
- **Quick View Modal** — Product quick-view without leaving the catalog page
- **Product Detail Page** — Full page with image gallery, pharmacy fields (dosage, pack size, active ingredient)
- **Shopping Cart** — Add/remove, quantity stepper with optimistic UI updates
- **Checkout** — Saved addresses, UAE emirate field, cash on delivery
- **Order History** — Viewable in profile page
- **Password Reset** — Via Gmail SMTP email link
- **WhatsApp Integration** — Order and wholesale enquiry buttons throughout the site
- **Responsive Design** — Mobile-first; full-screen filter overlay on small screens, Load More pagination

### Admin
- **Product Management UI** — React-based CRUD with image upload, brand/category dropdowns, inline creation
- **Category Management** — Hierarchical (parent → child), tab-based UI
- **Order Management** — Status updates: pending → processing → shipped → delivered → cancelled
- **show_price toggle** — Flippable per-product in Django admin list view without opening the edit form
- **Product Status Workflow** — draft / active / archived
- **Django Admin** — Full model admin at `/admin`

---

## Tech Stack

### Backend
- **Django 5.x** + **Django REST Framework**
- **PostgreSQL** (Neon serverless)
- **JWT Authentication** — `djangorestframework-simplejwt`
- **Cloudinary** — Image storage and CDN (`products/` and `brands/` paths)
- **Gmail SMTP** — Transactional emails (password reset)
- **Railway** — Deployment with smart migration runner
- **Python 3.13**

### Frontend
- **React 18**
- **React Router v6**
- **Axios** with JWT interceptors
- **Context API** — `AuthContext`, `CartContext`
- **Railway** — Frontend deployment

---

## Prerequisites

- Python 3.11+
- Node.js 16+
- PostgreSQL database (Neon free tier works)
- Cloudinary account (free tier works)
- Git

---

## Quick Start

### Backend

```bash
cd backend
python -m venv .venv
source .venv/Scripts/activate   # Windows Git Bash
# .venv\Scripts\activate.bat    # Windows CMD

pip install -r requirements.txt
cp .env.example .env
# Edit .env — fill in DATABASE_URL, DJANGO_SECRET_KEY, CLOUDINARY_URL, email credentials

python manage.py migrate
python manage.py seed_brochure_catalog   # Seeds 107 products from brochure data
python manage.py createsuperuser
python manage.py runserver
```

Backend: http://localhost:8000

### Frontend

```bash
cd frontend
npm install
npm start
```

Frontend: http://localhost:3000

---

## API Endpoints

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/register/` | Register new user |
| POST | `/api/token/` | Login — returns access + refresh JWT |
| POST | `/api/token/refresh/` | Refresh access token |
| GET | `/api/me/` | Current user info |
| POST | `/api/password-reset/` | Request password reset email |
| POST | `/api/password-reset/confirm/` | Confirm reset with token |

### Catalog (Public, slug-based)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/products/` | List active products |
| GET | `/api/products/{slug}/` | Product detail (includes `images` array) |
| GET | `/api/products/?search=query` | Full-text search |
| GET | `/api/products/?category=slug` | Filter by category slug |
| GET | `/api/products/?brand=slug` | Filter by brand slug |
| GET | `/api/products/?featured=true` | Featured products |
| GET | `/api/products/?in_stock=true` | In-stock only |
| GET | `/api/categories/` | Hierarchical categories |
| GET | `/api/categories/?flat=true` | Flat list (for dropdowns) |
| GET | `/api/categories/?root=true` | Root/parent categories only |
| GET | `/api/categories/{slug}/` | Category detail |
| GET | `/api/brands/` | List brands |
| GET | `/api/brands/{slug}/` | Brand detail |
| GET | `/api/product-images/?product={id}` | Images for a product |

### Cart (auth required)

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/cart/` | — |
| POST | `/api/cart/add_item/` | `{product_id, quantity}` |
| PATCH | `/api/cart/update_item/` | `{item_id, quantity}` |
| DELETE | `/api/cart/remove_item/` | `{item_id}` |
| DELETE | `/api/cart/clear/` | — |

### Orders (auth required)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders/` | User's order history |
| POST | `/api/orders/` | Create order from cart |
| PATCH | `/api/orders/{id}/update_status/` | Admin: update order status |

### Addresses (auth required)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/addresses/` | List addresses |
| POST | `/api/addresses/` | Add address |
| PUT/DELETE | `/api/addresses/{id}/` | Update / delete |

### Admin Only

- Full CRUD: `/api/products/`, `/api/brands/`, `/api/categories/`
- Full CRUD: `/api/product-images/`, `/api/suppliers/`, `/api/product-suppliers/`

---

## Project Structure

```
pharmacy-ecommerce/
├── backend/
│   ├── api/
│   │   ├── models.py                      # All Django models
│   │   ├── serializers.py                 # DRF serializers
│   │   ├── views.py                       # API ViewSets
│   │   ├── urls.py                        # URL routing
│   │   ├── admin.py                       # Django admin config
│   │   ├── emails.py                      # Email utilities
│   │   ├── password_reset.py              # Password reset logic
│   │   └── management/commands/
│   │       ├── seed_brochure_catalog.py   # Seeds 107 products from brochure
│   │       ├── seed_catalog.py            # Seeds 50 OTC products
│   │       ├── validate_catalog.py        # Catalog integrity check
│   │       ├── add_product_images.py      # Bulk image assignment
│   │       └── import_brochure_images.py  # Import images from brochure PDF
│   ├── pharmacy_api/                      # Django project settings
│   ├── BACKEND_SCHEMA.md                  # Full backend reference
│   ├── manage.py
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── api.js                         # Axios API client
│   │   ├── App.js                         # Root component & routes
│   │   ├── pages/
│   │   │   ├── Home.js                    # Hero, filter sidebar, product grid, footer
│   │   │   ├── ProductDetail.js           # Product detail with image gallery
│   │   │   ├── Checkout.js
│   │   │   ├── OrderConfirmation.js
│   │   │   ├── AdminDashboard.js
│   │   │   ├── Profile.js                 # User profile + order history
│   │   │   ├── Login.js / Register.js
│   │   │   ├── ForgotPassword.js / ResetPassword.js
│   │   │   └── About.js
│   │   ├── components/
│   │   │   ├── ProductGrid.js             # Product cards with quantity stepper
│   │   │   ├── ProductModal.js            # Quick-view modal with image gallery
│   │   │   ├── ProductManagement.js       # Admin product CRUD UI
│   │   │   ├── OrderManagement.js         # Admin order status UI
│   │   │   ├── Cart.js
│   │   │   ├── Navbar.js                  # Header with search
│   │   │   ├── WhatsAppButton.js
│   │   │   └── ScrollToTop.js
│   │   ├── context/
│   │   │   ├── CartContext.js
│   │   │   └── AuthContext.js
│   │   ├── styles/                        # Per-component CSS
│   │   └── utils/
│   │       └── axios.js                   # Axios instance with JWT interceptors
│   └── package.json
│
├── current_status.md    # Detailed project status, API reference, catalog breakdown
├── SECURITY.md
├── README.md
└── .gitignore
```

---

## Database Models

| Model | Description |
|-------|-------------|
| `Brand` | Manufacturers/brands with slug and Cloudinary logo |
| `Category` | Hierarchical (self-FK), 10 parent + 33 child; `full_path` property |
| `Product` | Full pharmacy product — price, stock, SEO, `show_price`, `requires_manual_review`, pharmacy-specific fields |
| `ProductImage` | Multiple images per product on Cloudinary; `is_primary` auto-unsets others; `source_type` tracking |
| `Supplier` | Vendor contact info |
| `ProductSupplier` | Product ↔ Supplier M2M with cost price and preferred flag |
| `Cart` | One cart per user (1:1); `total_price` and `total_items` properties |
| `CartItem` | Line items with quantity; `subtotal` property |
| `Address` | Delivery addresses with UAE emirate field |
| `Order` | Denormalized delivery snapshot + status + payment info + Stripe fields; order number format `ORD-YYYYMMDDHHMMSS-XXXX` |
| `OrderItem` | Price snapshot per line item; `subtotal` property |

**Stock management:** decremented on order creation, restored on cancellation, checked before checkout.

---

## Catalog

Sourced exclusively from the Al Ameen Pharmacy brochure PDF.

| Category | Sub-categories | Products |
|----------|---------------|---------|
| First Aid | First Aid Kits, Wound Care, Bandages, Eye & Ear, Resuscitation | 21 |
| Medical Disposables | Gloves, Gowns, Sterilization, General Disposables | 9 |
| Orthopaedic | Collars & Slings, Splints, Mobility Aids, Pillows | 9 |
| Anaesthesia & Airway | Airway Management, Oxygen Therapy, Suction | 9 |
| Medical Devices & Lab | Lab Consumables, Surgical Instruments, Diagnostics | 14 |
| Emergency & Transport | Stretchers, Immobilisation, Nebulisers, Walking Aids | 12 |
| Furniture & Equipment | Examination, Hospital Furniture, Trolleys | 8 |
| Plastic Products | Patient Hygiene, Basins & Bowls | 6 |
| Urology | Catheters, Urine Collection, Irrigation | 6 |
| Gynaecology | Instruments, Obstetrics | 3 |
| **Total** | | **107 products, 20 brands, 43 categories** |

**Images:** 98 of 107 products have Cloudinary images (150 images total). 9 products appear only in composite brochure grid images with no individually extractable image.

All products seeded with `show_price=False` and placeholder price `AED 1.00`. Enable pricing per-product via Django admin.

Seed: `python manage.py seed_brochure_catalog [--clear]`
Import images: `python manage.py import_brochure_images [--pdf path] [--force] [--dry-run]`

---

## Environment Variables

### Backend (`backend/.env`)

```env
DATABASE_URL=postgresql://...@neon.tech/...
DJANGO_SECRET_KEY=...
DEBUG=1
ALLOWED_HOSTS=127.0.0.1,localhost
CORS_ALLOWED_ORIGINS=http://localhost:3000
CLOUDINARY_URL=cloudinary://...
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
```

### Frontend (`frontend/.env`)

```env
REACT_APP_API_URL=http://localhost:8000/api
CI=false
```

---

## Deployment

| Service | URL |
|---------|-----|
| Live Site | https://www.ameenpharmacy.ae/ |
| Backend API | https://al-ameen-pharmacy-production.up.railway.app/api |
| Django Admin | https://al-ameen-pharmacy-production.up.railway.app/admin |

**Infrastructure:** Railway (backend + frontend), Neon PostgreSQL, Cloudinary CDN

---

## Roadmap

- [x] Django backend with REST API
- [x] PostgreSQL via Neon
- [x] Cloudinary image storage and CDN
- [x] 107-product catalog from brochure (20 brands, 43 categories)
- [x] Multi-image gallery per product with source tracking
- [x] show_price per-product toggle
- [x] Brand and supplier management
- [x] Two-tier full-text search
- [x] Shopping cart
- [x] JWT authentication + password reset via email
- [x] Order management with status workflow
- [x] React frontend — full auth, cart, checkout, order history
- [x] Admin dashboard (React) — products, categories, orders
- [x] Category + price filter sidebar (sticky, scrollable)
- [x] WhatsApp integration (orders + wholesale)
- [x] Responsive homepage (hero, branches, trust signals, stats, footer)
- [x] Deployed on Railway with custom domain (ameenpharmacy.ae)
- [ ] Payment gateway (Stripe/Telr)
- [ ] Email order notifications
- [ ] Customer reviews and ratings
- [ ] Delivery zone / shipping calculator

---

## Author

Built for Al Ameen Pharmacy by Sihan.

## License

MIT License — see LICENSE file for details.
