# Al Ameen Pharmacy - Project Status

**Last Updated:** 2026-04-13
**Status:** Production
**Schema Version:** v2.0 (clean reset)

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
- PostgreSQL (Neon) - fresh v2 schema
- JWT authentication (djangorestframework-simplejwt)
- Cloudinary for image storage
- Gmail SMTP for transactional emails

### Frontend
- React 18
- React Router v6
- Axios with JWT interceptors
- Context API (AuthContext, CartContext)

---

## Database Models

| Model | Purpose |
|-------|---------|
| Brand | Product brands/manufacturers |
| Category | Hierarchical categories (self-FK for nesting) |
| Product | Product catalog with pharmacy-specific fields |
| ProductImage | Multiple images per product |
| Supplier | Vendor info for inventory management |
| ProductSupplier | Product-supplier M2M relationship |
| Cart | User shopping cart (1:1 with User) |
| CartItem | Items in cart |
| Address | Dubai-focused delivery addresses |
| Order | Completed orders with denormalized delivery info |
| OrderItem | Order line items with price snapshots |

### Product Fields

```
Basic:        name, slug (auto-generated, unique)
Relations:    brand (FK, nullable), category (FK, nullable)
Content:      short_description (required), detailed_description (optional)
Inventory:    price, stock_quantity, sku, barcode
Pharmacy:     requires_prescription, dosage, pack_size, active_ingredient
Status:       status (draft/active/archived), is_featured
SEO:          meta_title, meta_description
```

### ProductImage

- Multiple images per product via `product` FK
- `is_primary` flag (auto-unsets others when set)
- `source_type`: manual_upload | manufacturer | supplier | placeholder
- Images stored on Cloudinary via `products/` path

---

## API Reference

### Authentication

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/token/` | JWT login (returns access + refresh) |
| POST | `/api/token/refresh/` | Refresh access token |
| POST | `/api/register/` | Create account |
| GET | `/api/me/` | Current user info |
| POST | `/api/password-reset/` | Request reset email |
| POST | `/api/password-reset/confirm/` | Confirm with token |

### Catalog (Public, slug-based)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/products/` | List active products |
| GET | `/api/products/{slug}/` | Product detail (includes `images` array) |
| GET | `/api/products/?search=query` | Full-text search |
| GET | `/api/products/?category={slug}` | Filter by category |
| GET | `/api/products/?brand={slug}` | Filter by brand |
| GET | `/api/products/?featured=true` | Featured products |
| GET | `/api/products/?in_stock=true` | In-stock only |
| GET | `/api/brands/` | List brands |
| GET | `/api/brands/{slug}/` | Brand detail |
| GET | `/api/categories/` | Hierarchical categories |
| GET | `/api/categories/?flat=true` | Flat list for dropdowns |
| GET | `/api/categories/{slug}/` | Category detail |

### Cart (Authenticated)

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/cart/` | - |
| POST | `/api/cart/add_item/` | `{product_id, quantity}` |
| PATCH | `/api/cart/update_item/` | `{item_id, quantity}` |
| DELETE | `/api/cart/remove_item/` | `{item_id}` |
| DELETE | `/api/cart/clear/` | - |

### Orders (Authenticated)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders/` | User's order history |
| POST | `/api/orders/` | Create from cart |
| PATCH | `/api/orders/{id}/update_status/` | Admin: update status |

### Addresses (Authenticated)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/addresses/` | List addresses |
| POST | `/api/addresses/` | Create address |
| PUT/DELETE | `/api/addresses/{id}/` | Update/delete |

### Admin Only (IsAdminUser)

- CRUD: `/api/products/`, `/api/brands/`, `/api/categories/`
- CRUD: `/api/product-images/`, `/api/suppliers/`, `/api/product-suppliers/`

---

## API Response Format

### Product List Item

```json
{
  "id": 1,
  "name": "Panadol Extra",
  "slug": "panadol-extra",
  "short_description": "Fast pain relief",
  "price": "15.00",
  "stock_quantity": 50,
  "category": 1,
  "category_name": "Pain Relief",
  "brand": 1,
  "brand_name": "Panadol",
  "primary_image_url": "https://res.cloudinary.com/.../image.jpg",
  "dosage": "500mg",
  "pack_size": "24 tablets",
  "requires_prescription": false,
  "is_featured": true,
  "status": "active",
  "in_stock": true
}
```

### Product Detail (adds)

```json
{
  "detailed_description": "Full usage info...",
  "sku": "PAN-EXT-500",
  "barcode": "123456789",
  "active_ingredient": "Paracetamol",
  "meta_title": "Buy Panadol Extra Online",
  "images": [
    {
      "id": 1,
      "image_url": "https://...",
      "is_primary": true,
      "display_order": 0
    }
  ]
}
```

---

## Frontend Structure

```
frontend/src/
├── components/
│   ├── Navbar.js              # Header with search
│   ├── ProductGrid.js         # Product cards with quantity stepper controls
│   ├── ProductModal.js        # Quick view modal with image gallery
│   ├── Cart.js                # Cart drawer
│   ├── ProductManagement.js   # Admin: Products + Categories tabs
│   └── OrderManagement.js     # Admin: Order status management
├── pages/
│   ├── Home.js                # Hero + filter sidebar + product grid
│   ├── ProductDetail.js       # Full product page with image gallery
│   ├── Login.js, Register.js
│   ├── Profile.js             # User profile + order history
│   ├── Checkout.js            # Checkout flow
│   ├── AdminDashboard.js      # Admin panel
│   └── ...
├── context/
│   ├── AuthContext.js         # JWT auth state
│   └── CartContext.js         # Shopping cart state
├── styles/
│   ├── Home.css               # Homepage with filter sidebar styles
│   └── ProductGrid.css        # Product card design (compact, teal theme)
└── utils/
    └── axios.js               # Axios instance with JWT interceptors
```

### Key Frontend Behaviors

1. **Product display**: Uses `primary_image_url`, `short_description`, `brand_name`
2. **Product navigation**: Uses `slug` for URLs and API calls (e.g., `/product/panadol-extra`)
3. **Cart operations**: Uses numeric `product.id` for add/update/remove
4. **Image gallery**: Products can have multiple images, navigable via arrows/thumbnails
5. **Admin UI**: Tab-based Products/Categories management, inline brand/category creation
6. **Filter sidebar**: Category filter (conversion-psychology ordered) + price range filter
7. **Product cards**: Compact design with price left, Add/quantity stepper right
8. **Pagination**: Load More pattern with product count display

---

## Product Creation Flow

### Frontend (Admin)

1. User fills form in ProductManagement modal
2. Selects brand/category from dropdowns (or creates inline)
3. Uploads image(s) via file input
4. Submits as `multipart/form-data`

### Backend (ProductViewSet)

1. `create()` extracts image file from `request.FILES`
2. Validates and saves Product via `ProductCreateUpdateSerializer`
3. Calls `_handle_product_image()`:
   - Unsets existing primary images
   - Creates new `ProductImage` with `is_primary=True`
4. Returns full product via `ProductDetailSerializer`

Additional images can be uploaded via `/api/product-images/` endpoint.

---

## Search Implementation

Backend uses two-tier search:

1. **Short queries (<3 chars)**: Simple ILIKE on `name`, `brand.name`, `sku` - limit 10
2. **Long queries (3+ chars)**: Postgres full-text search with weighted ranking
   - Weight A: name
   - Weight B: short_description
   - Weight C: brand.name, active_ingredient
   - Limit 20 results

---

## Design System

### Colors (CSS variables)

```css
--primary: #0D9488;       /* Healthcare teal */
--primary-dark: #0F766E;
--secondary: #0EA5E9;     /* Trust blue */
--accent: #F59E0B;        /* Warm amber */
--success: #22C55E;
--error: #EF4444;
```

### Breakpoints

- Desktop: > 1024px
- Tablet: 768px - 1024px
- Mobile: < 768px

---

## Features

### Customer
- Product browsing with category filter sidebar and price range filters
- Active filters display with clear functionality
- Load More pagination for products
- Redesigned product cards: compact layout, price left, controls right
- Larger quantity control buttons (34px) for better usability
- Quick view modal + full product detail page
- Multi-image gallery with navigation
- Cart with optimistic UI updates
- Checkout with saved addresses
- Order history in profile
- Password reset via email
- Mobile-responsive filter panel (full-screen overlay)

### Admin
- Product CRUD with image management
- Category CRUD (hierarchical)
- Brand dropdown + inline creation
- Product status workflow (draft/active/archived)
- Order status management
- Django admin at `/admin`

---

## Environment Variables

### Backend (.env)

```
DATABASE_URL=postgresql://...@neon.tech/...
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
| `backend/api/models.py` | All Django models |
| `backend/api/serializers.py` | DRF serializers |
| `backend/api/views.py` | API ViewSets |
| `backend/BACKEND_SCHEMA.md` | Full backend documentation |
| `frontend/src/context/CartContext.js` | Cart state management |
| `frontend/src/components/ProductManagement.js` | Admin product CRUD |
| `frontend/src/components/ProductModal.js` | Product quick view |

---

## Development Notes

1. **Slug-based lookups**: Products, categories, brands use `slug` field
2. **Cart uses numeric ID**: `add_item` expects `product_id` (integer)
3. **Image handling**: `primary_image_url` computed from ProductImage table
4. **Status visibility**: Non-admin users only see `status='active'` products
5. **Admin UI**: Brand/category dropdowns, status selector, multi-image management
