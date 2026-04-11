# Al Ameen Pharmacy - Backend Schema Documentation

**Version:** 2.0
**Last Updated:** 2026-04-12
**Stack:** Django 5.x + Django REST Framework + PostgreSQL (Neon)

---

## Quick Reference

### Models Overview

| Model | Purpose | Key Fields |
|-------|---------|------------|
| **Brand** | Product brands/manufacturers | name, slug, logo |
| **Category** | Hierarchical categories | name, slug, parent (self-FK) |
| **Product** | Product catalog | name, slug, brand, category, price, status |
| **ProductImage** | Multiple images per product | product (FK), image, is_primary |
| **Supplier** | Vendor/supplier info | name, contact details |
| **ProductSupplier** | Product-supplier M2M | supplier_sku, is_preferred |
| **Cart** | User shopping cart | user (1:1) |
| **CartItem** | Items in cart | cart, product, quantity |
| **Address** | Delivery addresses | Dubai-specific fields |
| **Order** | Completed orders | denormalized delivery info |
| **OrderItem** | Order line items | price snapshot |

---

## Detailed Schema

### 1. Brand

```python
Brand:
    id              AutoField (PK)
    name            CharField(200), UNIQUE
    slug            SlugField(220), UNIQUE, auto-generated
    logo            ImageField, optional, upload_to='brands/'
    created_at      DateTimeField, auto
    updated_at      DateTimeField, auto
```

**Indexes:** slug, name
**Related:** Product.brand (FK)

---

### 2. Category

```python
Category:
    id              AutoField (PK)
    name            CharField(100)
    slug            SlugField(120), UNIQUE, nullable, auto-generated
    description     TextField, optional
    parent          ForeignKey(self), nullable, CASCADE
    is_active       BooleanField, default=True
    display_order   PositiveIntegerField, default=0
    created_at      DateTimeField, auto
    updated_at      DateTimeField, auto
```

**Indexes:** slug, parent, is_active, display_order
**Related:** Product.category (FK), Category.children (self-relation)
**Property:** `full_path` - returns "Parent > Child > Grandchild"

---

### 3. Product

```python
Product:
    # Basic
    id                      AutoField (PK)
    name                    CharField(200), indexed
    slug                    SlugField(220), UNIQUE, nullable, auto-generated
    brand                   ForeignKey(Brand), nullable, SET_NULL
    category                ForeignKey(Category), nullable, SET_NULL

    # Descriptions
    short_description       TextField, optional (blank default)
    detailed_description    TextField, optional

    # Pricing & Inventory
    price                   DecimalField(10,2), min 0.01
    stock_quantity          PositiveIntegerField, default=0
    sku                     CharField(100), optional
    barcode                 CharField(50), optional

    # Pharmacy-specific
    requires_prescription   BooleanField, default=False
    dosage                  CharField(100), optional
    pack_size               CharField(100), optional
    active_ingredient       CharField(200), optional

    # Status
    status                  CharField(20), choices: draft/active/archived
    requires_manual_review  BooleanField, default=True
    is_featured             BooleanField, default=False

    # SEO
    meta_title              CharField(70), optional
    meta_description        CharField(160), optional

    # Timestamps
    created_at              DateTimeField, auto
    updated_at              DateTimeField, auto
```

**Indexes:** slug, status, is_featured, requires_prescription, created_at, brand, category
**Related:** ProductImage (many), ProductSupplier (many)
**Property:** `in_stock`, `primary_image`

---

### 4. ProductImage

```python
ProductImage:
    id              AutoField (PK)
    product         ForeignKey(Product), CASCADE
    image           ImageField, upload_to='products/'
    alt_text        CharField(200), optional
    is_primary      BooleanField, default=False
    display_order   PositiveIntegerField, default=0
    source_type     CharField(20), choices: manual_upload/manufacturer/supplier/placeholder
    source_url      URLField, optional
    created_at      DateTimeField, auto
    updated_at      DateTimeField, auto
```

**Indexes:** (product, is_primary), display_order
**Behavior:** Setting is_primary=True auto-unsets others for same product

---

### 5. Supplier

```python
Supplier:
    id              AutoField (PK)
    name            CharField(200), UNIQUE
    slug            SlugField(220), UNIQUE, auto-generated
    contact_name    CharField(200), optional
    phone           CharField(50), optional
    email           EmailField, optional
    website         URLField, optional
    created_at      DateTimeField, auto
    updated_at      DateTimeField, auto
```

**Indexes:** slug, name
**Related:** ProductSupplier (many)

---

### 6. ProductSupplier

```python
ProductSupplier:
    id                  AutoField (PK)
    product             ForeignKey(Product), CASCADE
    supplier            ForeignKey(Supplier), CASCADE
    supplier_sku        CharField(100), optional
    last_purchase_price DecimalField(10,2), nullable
    is_preferred        BooleanField, default=False
    created_at          DateTimeField, auto
    updated_at          DateTimeField, auto
```

**Constraints:** unique_together(product, supplier)
**Behavior:** Setting is_preferred=True auto-unsets others for same product

---

### 7. Cart

```python
Cart:
    id              AutoField (PK)
    user            OneToOneField(User), CASCADE
    created_at      DateTimeField, auto
    updated_at      DateTimeField, auto
```

**Related:** CartItem (many via 'items')
**Properties:** `total_price`, `total_items`

---

### 8. CartItem

```python
CartItem:
    id          AutoField (PK)
    cart        ForeignKey(Cart), CASCADE
    product     ForeignKey(Product), CASCADE
    quantity    PositiveIntegerField, default=1, min=1
    added_at    DateTimeField, auto
```

**Constraints:** unique_together(cart, product)
**Property:** `subtotal`

---

### 9. Address

```python
Address:
    id              AutoField (PK)
    user            ForeignKey(User), CASCADE
    full_name       CharField(200)
    phone_number    CharField(20)
    street_address  CharField(255)
    building        CharField(100), optional
    area            CharField(100)
    city            CharField(100), default='Dubai'
    emirate         CharField(50), default='Dubai'
    postal_code     CharField(10), optional
    is_default      BooleanField, default=False
```

---

### 10. Order

```python
Order:
    id                      AutoField (PK)
    user                    ForeignKey(User), CASCADE
    order_number            CharField(50), UNIQUE, auto-generated

    # Denormalized delivery (snapshot, all optional with defaults)
    full_name               CharField(200), default=''
    email                   EmailField, default=''
    phone                   CharField(20), default=''
    address                 CharField(255), default=''
    city                    CharField(100), default=''
    emirate                 CharField(50), default=''
    delivery_notes          TextField, default=''

    # Status
    status                  CharField(20), choices: pending/processing/shipped/delivered/cancelled
    payment_method          CharField(20), choices: cash_on_delivery/stripe
    payment_status          CharField(20), choices: pending/paid/failed/refunded

    # Stripe
    stripe_payment_intent_id    CharField(255), optional
    stripe_client_secret        CharField(255), optional

    # Totals
    total_amount            DecimalField(10,2)

    # Timestamps
    created_at              DateTimeField, auto
    updated_at              DateTimeField, auto
    delivered_at            DateTimeField, nullable
```

**Indexes:** status, created_at, order_number
**Order Number Format:** `ORD-YYYYMMDDHHMMSS-XXXX`

---

### 11. OrderItem

```python
OrderItem:
    id                  AutoField (PK)
    order               ForeignKey(Order), CASCADE
    product             ForeignKey(Product), SET_NULL, nullable
    product_name        CharField(200)  # snapshot
    quantity            PositiveIntegerField, min=1
    price_at_purchase   DecimalField(10,2)  # snapshot
```

**Property:** `subtotal`

---

## API Endpoints

### Catalog (Public)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/brands/` | List all brands |
| GET | `/api/brands/{slug}/` | Get brand by slug |
| GET | `/api/categories/` | List categories (hierarchical) |
| GET | `/api/categories/?root=true` | Root categories only |
| GET | `/api/categories/?flat=true` | Flat list for dropdowns |
| GET | `/api/categories/{slug}/` | Get category by slug |
| GET | `/api/products/` | List active products |
| GET | `/api/products/?search=query` | Full-text search |
| GET | `/api/products/?category=slug` | Filter by category |
| GET | `/api/products/?brand=slug` | Filter by brand |
| GET | `/api/products/?featured=true` | Featured products |
| GET | `/api/products/?in_stock=true` | In-stock only |
| GET | `/api/products/{slug}/` | Product detail |
| GET | `/api/product-images/?product=id` | Images for product |

### Admin Only

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST/PUT/DELETE | `/api/brands/` | Manage brands |
| POST/PUT/DELETE | `/api/categories/` | Manage categories |
| POST/PUT/DELETE | `/api/products/` | Manage products |
| POST/PUT/DELETE | `/api/product-images/` | Manage images |
| ALL | `/api/suppliers/` | Manage suppliers |
| ALL | `/api/product-suppliers/` | Manage product-supplier links |

### Cart (Authenticated)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/cart/` | Get user's cart |
| POST | `/api/cart/add_item/` | Add to cart |
| PATCH | `/api/cart/update_item/` | Update quantity |
| DELETE | `/api/cart/remove_item/` | Remove item |
| DELETE | `/api/cart/clear/` | Clear cart |

### Orders (Authenticated)

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/orders/` | List user's orders |
| POST | `/api/orders/` | Create order from cart |
| PATCH | `/api/orders/{id}/update_status/` | Admin: update status |

### Auth

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/api/register/` | Register user |
| GET | `/api/me/` | Current user info |
| POST | `/api/token/` | JWT login |
| POST | `/api/token/refresh/` | Refresh JWT |
| POST | `/api/password-reset/` | Request reset |
| POST | `/api/password-reset/confirm/` | Confirm reset |

---

## Key Design Decisions

### 1. Slug-based URLs
All public resources (brands, categories, products) use slug as lookup field for SEO-friendly URLs.

### 2. Product Status Workflow
- `draft` - Not visible to customers
- `active` - Live and purchasable
- `archived` - Soft-deleted, not visible

### 3. Image Source Tracking
ProductImage.source_type tracks where images came from (upload, manufacturer, supplier) for future automation.

### 4. Hierarchical Categories
Self-referencing FK enables unlimited nesting. Use `?root=true` for top-level only.

### 5. Order Denormalization
Delivery info is copied to Order at checkout time, preserving history even if Address changes.

### 6. Stock Management
- Decreased on order creation
- Restored on order cancellation
- Checked before checkout

---

## Migration Strategy

Since this is v2.0 with breaking changes, migrations were reset:
1. Delete old migrations (0001, 0002, 0003)
2. Create fresh `0001_initial_schema_v2.py`
3. Run `migrate` with fresh database

For production with data, would need:
1. Data migration script to map old → new
2. Temporary compatibility layer

---

## Files Reference

| File | Purpose |
|------|---------|
| `api/models.py` | All Django models |
| `api/serializers.py` | DRF serializers |
| `api/views.py` | API ViewSets |
| `api/admin.py` | Django admin config |
| `api/urls.py` | URL routing |
| `api/emails.py` | Email sending functions |
| `api/password_reset.py` | Password reset tokens |

---

## For AI/Developer Reference

### Quick Queries
```python
# Active products with images
Product.objects.filter(status='active').prefetch_related('images')

# Products by category (including children)
category = Category.objects.get(slug='pain-relief')
Product.objects.filter(category__in=[category] + list(category.children.all()))

# Featured products
Product.objects.filter(is_featured=True, status='active')

# Product with all related data
Product.objects.select_related('brand', 'category').prefetch_related('images', 'suppliers')
```

### Adding New Product
```python
product = Product.objects.create(
    name="Panadol Extra",
    short_description="Fast pain relief",
    price=Decimal('15.00'),
    brand=Brand.objects.get(slug='panadol'),
    category=Category.objects.get(slug='pain-relief'),
    status='draft',  # Review before publishing
    requires_prescription=False
)

ProductImage.objects.create(
    product=product,
    image=uploaded_file,
    is_primary=True,
    source_type='manual_upload'
)

product.status = 'active'
product.save()
```
