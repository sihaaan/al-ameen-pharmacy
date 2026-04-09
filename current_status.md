# AL AMEEN PHARMACY - Project Status

**Last Updated:** 2026-04-09
**Project Status:** LIVE AND WORKING IN PRODUCTION!

---

## Current State

### Deployment Status - ALL COMPLETE!
- **Backend:** Deployed to Railway - Django + Gunicorn + Whitenoise
- **Frontend:** Deployed to Railway - React + serve
- **Database:** Neon PostgreSQL connected and migrated
- **Cloudinary:** Product images working (CDN delivery)
- **Static Files:** Collected via Whitenoise
- **CSRF & CORS:** Configured for Railway domains
- **Environment Variables:** All set correctly
- **API Connection:** Frontend successfully loading products from backend!
- **SITE IS LIVE:** Both frontend and backend fully functional!

### Recent UI/UX Improvements (April 2026)

#### Homepage Redesign
- **Premium healthcare design** with teal color scheme (#0D9488, #0F766E, #14B8A6)
- **Hero section** with brand visibility (Arabic + English name)
- **Stats section** ("Why Choose Al Ameen") - 4 columns on desktop, 2 on tablet/mobile
- **Product grid** - Clean cards with price/quantity controls
- **Wholesale section** - Dark background B2B section
- **Footer** - 4-column layout with contact, links, locations, hours

#### Product Cards
- Compact design with 4:3 aspect ratio images
- Price and quantity controls in unified background box
- Teal (+/-) buttons for quantity
- Responsive sizing for mobile

#### WhatsApp Integration
- Floating WhatsApp button (bottom-right)
- Delayed appearance (1.5s) with fade-in animation
- Tooltip on hover: "Order on WhatsApp"
- Links to: wa.me/971505456388

#### Branding Guidelines
- **Customer-facing:** "Al Ameen Pharmacy" (no LLC)
- **Legal/formal:** "Al Ameen Pharmacy LLC" (footer copyright only)
- Hero displays both Arabic (صيدلية الأمين) and English brand name

---

## Tech Stack

### Backend
- **Framework:** Django 5.2.6 + Django REST Framework
- **Database:** PostgreSQL (Neon cloud)
- **Authentication:** JWT (djangorestframework-simplejwt)
- **Email:** SMTP via Gmail (console backend for dev)
- **Image Storage:** Cloudinary CDN (25GB free tier)
- **API Base:** `https://al-ameen-pharmacy-production.up.railway.app/api`

### Frontend
- **Framework:** React 18
- **HTTP Client:** Axios with JWT interceptors
- **Routing:** React Router v6
- **State Management:** Context API (AuthContext, CartContext)
- **Styling:** CSS with custom properties

### Deployment
- **Backend:** Railway.app
  - URL: https://al-ameen-pharmacy-production.up.railway.app
  - Admin: https://al-ameen-pharmacy-production.up.railway.app/admin
- **Frontend:** Railway.app
  - URL: https://al-ameen-pharmacy-production-8378.up.railway.app
- **SSL:** Auto-configured by Railway (HTTPS enabled)

---

## Project Structure

```
pharmacy-ecommerce/
├── backend/
│   ├── api/
│   │   ├── models.py           # User, Product, Cart, Order, Address
│   │   ├── views.py            # API endpoints (ViewSets)
│   │   ├── serializers.py      # DRF serializers
│   │   ├── urls.py             # API routes
│   │   ├── emails.py           # Email sending functions
│   │   ├── password_reset.py   # Token-based password reset
│   │   ├── admin.py            # Django admin customization
│   │   └── templates/emails/   # HTML email templates
│   ├── pharmacy_api/
│   │   ├── settings.py         # Django settings
│   │   └── urls.py             # Root URL config
│   ├── .env                    # Environment variables (NOT in git)
│   └── requirements.txt        # Python dependencies
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Navbar.js           # Header with logo, search, cart
│   │   │   ├── ProductGrid.js      # Product cards display
│   │   │   ├── ProductModal.js     # Quick view modal
│   │   │   ├── Cart.js             # Cart drawer
│   │   │   ├── WhatsAppButton.js   # Floating WhatsApp CTA
│   │   │   ├── OrderManagement.js  # Admin order management
│   │   │   └── ProductManagement.js # Admin product CRUD
│   │   │
│   │   ├── pages/
│   │   │   ├── Home.js             # Homepage with hero, products, footer
│   │   │   ├── About.js            # About page
│   │   │   ├── Login.js            # Login form
│   │   │   ├── Register.js         # Registration form
│   │   │   ├── Profile.js          # User profile & orders
│   │   │   ├── Checkout.js         # Checkout flow
│   │   │   ├── ProductDetail.js    # Single product page
│   │   │   ├── AdminDashboard.js   # Admin panel
│   │   │   ├── OrderConfirmation.js # Post-order page
│   │   │   ├── ForgotPassword.js   # Password reset request
│   │   │   └── ResetPassword.js    # Password reset confirm
│   │   │
│   │   ├── styles/
│   │   │   ├── Home.css            # Homepage styles (hero, sections, footer)
│   │   │   ├── ProductGrid.css     # Product card styles
│   │   │   ├── Navbar.css          # Header/navigation styles
│   │   │   ├── Cart.css            # Cart drawer styles
│   │   │   ├── WhatsAppButton.css  # Floating button styles
│   │   │   └── ...                 # Other page-specific styles
│   │   │
│   │   ├── context/
│   │   │   ├── AuthContext.js      # User authentication state
│   │   │   └── CartContext.js      # Shopping cart state
│   │   │
│   │   ├── api.js                  # API configuration
│   │   ├── App.js                  # Root component with routes
│   │   └── App.css                 # Global styles & CSS variables
│   │
│   ├── .env                    # Frontend env vars (NOT in git)
│   └── package.json            # Node dependencies
│
├── current_status.md           # This file!
└── .gitignore                  # Protects .env files
```

---

## CSS Architecture

### Design System (App.css)
```css
:root {
  /* Primary Colors - Healthcare Teal */
  --primary: #0D9488;
  --primary-dark: #0F766E;
  --primary-light: #14B8A6;

  /* Text Colors */
  --text-primary: #111827;
  --text-secondary: #6b7280;

  /* Background Colors */
  --bg-primary: #ffffff;
  --bg-secondary: #f9fafb;

  /* Borders & Shadows */
  --border-color: #e5e7eb;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --shadow-md: 0 4px 6px rgba(0,0,0,0.1);
}
```

### Page-Specific Styles
- **Home.css:** Hero, branches, products section, stats, wholesale, footer
- **ProductGrid.css:** Product cards, quantity controls, badges
- **Navbar.css:** Header, search bar, mobile menu
- **Cart.css:** Cart drawer, cart items, totals
- **WhatsAppButton.css:** Floating button, tooltip

### Responsive Breakpoints
- Desktop: > 1024px (4-column product grid, 4-column stats)
- Tablet: 768px - 1024px (2-3 column grids)
- Mobile: 480px - 768px (2-column product grid)
- Small Mobile: < 480px (2-column grid, smaller text)

---

## Key Components

### Hero Section (Home.js)
```jsx
<section className="hero-section">
  <div className="hero-brand">
    <span className="hero-brand-arabic">صيدلية الأمين</span>
    <span className="hero-brand-english">AL AMEEN PHARMACY</span>
  </div>
  <div className="hero-badge">Open Late Until 2AM</div>
  <h1 className="hero-headline">Your Trusted Pharmacy in Dubai</h1>
  <div className="hero-ctas">
    <a href="wa.me/..." className="hero-cta-primary">Order on WhatsApp</a>
    <a href="tel:..." className="hero-cta-secondary">Call Now</a>
  </div>
</section>
```

### Product Card (ProductGrid.js)
```jsx
<div className="product-card">
  <div className="product-image">
    <img src={product.image} alt={product.name} />
  </div>
  <div className="product-card-content">
    <h3>{product.name}</h3>
    <div className="card-cart-controls">
      <div className="price-and-quantity">
        <span className="product-price">AED {product.price}</span>
        <div className="card-quantity-controls">
          <button onClick={decrement}>−</button>
          <span>{quantity}</span>
          <button onClick={increment}>+</button>
        </div>
      </div>
    </div>
  </div>
</div>
```

### WhatsApp Button (WhatsAppButton.js)
- Fixed position, bottom-right
- 1.5s delayed appearance
- Hover tooltip: "Order on WhatsApp"
- Links to: https://wa.me/971505456388

---

## Features Complete

### E-Commerce
- Product catalog with categories, search, filters
- Shopping cart with optimistic UI updates
- Checkout with address selection
- Order creation with Cash on Delivery
- Stock management (auto-decrease on order)

### User Features
- Registration with email validation
- JWT authentication (login/logout)
- User profile with order history
- Password reset (token-based)
- Address management (add/edit/delete)

### Admin Features
- Dashboard with statistics
- Product management with Cloudinary uploads
- Order management (view, update status)
- Category management

### UI/UX
- Fully responsive (mobile/tablet/desktop)
- Mobile hamburger menu
- Floating WhatsApp button
- Loading states and error handling
- Toast notifications

---

## API Endpoints

### Authentication
- POST `/api/token/` - Login
- POST `/api/token/refresh/` - Refresh token
- POST `/api/register/` - Register
- GET `/api/me/` - Current user

### Products
- GET `/api/products/` - List products
- GET `/api/products/{id}/` - Product details
- GET `/api/categories/` - List categories

### Cart
- GET `/api/cart/` - Get cart
- POST `/api/cart/add_item/` - Add item
- PATCH `/api/cart/update_item/` - Update quantity
- DELETE `/api/cart/remove_item/` - Remove item

### Orders
- GET `/api/orders/` - List orders
- POST `/api/orders/` - Create order
- PATCH `/api/orders/{id}/update_status/` - Update status

### Addresses
- GET `/api/addresses/` - List addresses
- POST `/api/addresses/` - Create address
- PUT/DELETE `/api/addresses/{id}/` - Update/delete

---

## Environment Variables

### Backend (.env)
```bash
DATABASE_URL=postgresql://...
DJANGO_SECRET_KEY=<strong-key>
DEBUG=0
ALLOWED_HOSTS=<railway-domain>
CORS_ALLOWED_ORIGINS=<frontend-url>
CLOUDINARY_URL=cloudinary://...
EMAIL_HOST_USER=...
EMAIL_HOST_PASSWORD=...
```

### Frontend (.env)
```bash
REACT_APP_API_URL=https://al-ameen-pharmacy-production.up.railway.app/api
CI=false
```

---

## For AI/Developer Reference

### Quick Start
1. Read this file to understand the project
2. Frontend code is in `/frontend/src/`
3. Backend code is in `/backend/api/`
4. Styles are in `/frontend/src/styles/`

### Key Files to Know
- `Home.js` + `Home.css` - Homepage layout
- `ProductGrid.js` + `ProductGrid.css` - Product cards
- `Navbar.js` + `Navbar.css` - Header
- `App.css` - Global styles and CSS variables
- `CartContext.js` - Cart state management
- `AuthContext.js` - Auth state management

### Design Principles
- Healthcare teal color scheme (#0D9488)
- Clean, professional, premium look
- Mobile-first responsive design
- WhatsApp as primary CTA
- "Al Ameen Pharmacy" branding (no LLC except in footer)

---

## Contact

- **Developer:** Sihan
- **Database:** Neon PostgreSQL
- **Email:** Gmail SMTP
- **WhatsApp Business:** +971 50 545 6388
