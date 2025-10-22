# 🏥 Al Ameen Pharmacy E-Commerce Platform

A full-stack e-commerce platform for pharmacy products built with Django REST Framework and React.

## 🌟 Features

- **Product Catalog** - Browse 18+ pharmacy products across 6 categories
- **Shopping Cart** - Add/remove products, manage quantities
- **User Authentication** - JWT-based secure authentication
- **Order Management** - Complete checkout and order tracking
- **Admin Panel** - Django admin for product/order management
- **UAE-Specific** - Dubai timezone, emirate fields, AED pricing
- **REST API** - Full RESTful API with 16+ endpoints

## 🛠️ Tech Stack

### Backend
- **Django 5.2.6** - Web framework
- **Django REST Framework** - API toolkit
- **PostgreSQL** - Production database (via Neon)
- **JWT Authentication** - Token-based auth
- **Python 3.13** - Programming language

### Frontend
- **React 18** - UI framework
- **Axios** - HTTP client
- **Context API** - State management

## 📋 Prerequisites

- Python 3.11+
- Node.js 16+
- PostgreSQL database (or use Neon free tier)
- Git

## 🚀 Quick Start

### Backend Setup

```bash
# Navigate to backend
cd backend

# Create virtual environment
python -m venv .venv

# Activate virtual environment
# Windows Git Bash:
source .venv/Scripts/activate
# Windows CMD:
.venv\Scripts\activate.bat

# Install dependencies
pip install -r requirements.txt

# Setup environment variables
cp .env.example .env
# Edit .env with your database URL and secret key

# Run migrations
python manage.py migrate

# Populate sample data
python manage.py populate_data

# Create superuser (for admin panel)
python manage.py createsuperuser

# Start development server
python manage.py runserver
```

Backend will run at: http://localhost:8000

### Frontend Setup

```bash
# Navigate to frontend
cd frontend

# Install dependencies
npm install

# Start development server
npm start
```

Frontend will run at: http://localhost:3000

## 📚 API Endpoints

### Authentication
- `POST /api/register/` - Register new user
- `POST /api/token/` - Login (get JWT token)
- `POST /api/token/refresh/` - Refresh token
- `GET /api/me/` - Get current user

### Products & Categories
- `GET /api/products/` - List all products
- `GET /api/products/{id}/` - Product detail
- `GET /api/products/?category={id}` - Filter by category
- `GET /api/categories/` - List categories

### Shopping Cart (requires auth)
- `GET /api/cart/` - Get user's cart
- `POST /api/cart/add_item/` - Add product to cart
- `PATCH /api/cart/update_item/` - Update quantity
- `DELETE /api/cart/remove_item/` - Remove item
- `DELETE /api/cart/clear/` - Clear cart

### Orders (requires auth)
- `GET /api/orders/` - List user's orders
- `POST /api/orders/` - Create order from cart
- `GET /api/orders/{id}/` - Order detail

### Addresses (requires auth)
- `GET /api/addresses/` - List addresses
- `POST /api/addresses/` - Add new address
- `PUT /api/addresses/{id}/` - Update address
- `DELETE /api/addresses/{id}/` - Delete address

## 📁 Project Structure

```
pharmacy-ecommerce/
├── backend/                  # Django backend
│   ├── api/                  # Main API app
│   │   ├── models.py         # Database models
│   │   ├── serializers.py    # DRF serializers
│   │   ├── views.py          # API views
│   │   ├── urls.py           # URL routing
│   │   └── admin.py          # Admin configuration
│   ├── pharmacy_api/         # Django project settings
│   ├── manage.py            # Django CLI
│   └── requirements.txt     # Python dependencies
│
├── frontend/                 # React frontend
│   ├── src/
│   │   ├── api.js           # API client
│   │   ├── App.js           # Main component
│   │   ├── components/      # React components
│   │   └── context/         # React context
│   └── package.json         # Node dependencies
│
├── .gitignore
├── LICENSE
└── README.md
```

## 🗄️ Database Models

- **Category** - Product categories (Pain Relief, Vitamins, etc.)
- **Product** - Pharmacy products with pricing and stock
- **Cart** - User shopping carts
- **CartItem** - Items in carts
- **Address** - Delivery addresses (Dubai-specific fields)
- **Order** - Purchase orders
- **OrderItem** - Products in orders

## 🔐 Environment Variables

Create a `.env` file in the `backend/` directory:

```env
DATABASE_URL=postgresql://user:pass@host/db
DJANGO_SECRET_KEY=your-secret-key
DEBUG=1
ALLOWED_HOSTS=127.0.0.1,localhost
```

## 🎯 Roadmap

- [x] Django backend with REST API
- [x] PostgreSQL database integration
- [x] Product catalog and categories
- [x] Shopping cart functionality
- [x] User authentication (JWT)
- [x] Order management
- [x] User authentication UI (React)
- [x] Cart integration with React
- [x] Checkout flow
- [ ] Payment gateway (Stripe/Telr)
- [ ] Product image uploads
- [ ] Email notifications
- [ ] Order tracking
- [ ] Admin dashboard
- [ ] Search and filters
- [ ] Reviews and ratings

## 👨‍💻 Author

Built for Al Ameen Pharmacy by Sihan

## 📄 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Django & Django REST Framework communities
- React team
- Neon for free PostgreSQL hosting
