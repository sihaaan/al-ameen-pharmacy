from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.utils.text import slugify
from decimal import Decimal
import random
import string


# ====================
# CATALOG MODELS
# ====================

class Brand(models.Model):
    """
    Product brands/manufacturers.
    Examples: Pfizer, Johnson & Johnson, Panadol, etc.
    """
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    logo = models.ImageField(upload_to='brands/', blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure unique slug
            original_slug = self.slug
            counter = 1
            while Brand.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class Category(models.Model):
    """
    Hierarchical product categories.
    Supports parent-child relationships for nested categories.
    Examples: Medications > Pain Relief > Headache
    """
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120, unique=True, blank=True, null=True)
    description = models.TextField(blank=True)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='children'
    )
    is_active = models.BooleanField(default=True)
    display_order = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ['display_order', 'name']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['parent']),
            models.Index(fields=['is_active']),
            models.Index(fields=['display_order']),
        ]

    def __str__(self):
        if self.parent:
            return f"{self.parent.name} > {self.name}"
        return self.name

    @property
    def full_path(self):
        """Returns full category path: 'Medications > Pain Relief > Headache'"""
        if self.parent:
            return f"{self.parent.full_path} > {self.name}"
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure unique slug
            original_slug = self.slug
            counter = 1
            while Category.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class Product(models.Model):
    """
    Core product model for pharmacy e-commerce.
    Supports pharmacy-specific fields, SEO, and multi-image via ProductImage.
    """
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('active', 'Active'),
        ('archived', 'Archived'),
    ]

    # Basic info
    name = models.CharField(max_length=200, db_index=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True, null=True)
    brand = models.ForeignKey(
        Brand,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='products'
    )

    # Descriptions
    short_description = models.TextField(
        blank=True,
        default='',
        help_text="Brief description shown in product cards/grid"
    )
    detailed_description = models.TextField(
        blank=True,
        null=True,
        help_text="Full description with usage, dosage, warnings, etc."
    )

    # Pricing & Inventory
    price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))]
    )
    stock_quantity = models.PositiveIntegerField(default=0)
    sku = models.CharField(
        max_length=100,
        blank=True,
        help_text="Stock Keeping Unit - unique product identifier"
    )
    barcode = models.CharField(
        max_length=50,
        blank=True,
        help_text="UPC, EAN, or other barcode"
    )

    # Pharmacy-specific
    requires_prescription = models.BooleanField(
        default=False,
        help_text="Requires prescription to purchase"
    )
    dosage = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g., 500mg, 10ml"
    )
    pack_size = models.CharField(
        max_length=100,
        blank=True,
        help_text="e.g., 30 tablets, 100ml bottle"
    )
    active_ingredient = models.CharField(
        max_length=200,
        blank=True,
        help_text="e.g., Paracetamol, Ibuprofen"
    )

    # Status & Visibility
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='draft',
        db_index=True
    )
    requires_manual_review = models.BooleanField(
        default=True,
        help_text="Requires admin review before publishing"
    )
    is_featured = models.BooleanField(
        default=False,
        help_text="Show on homepage featured section"
    )

    # SEO
    meta_title = models.CharField(
        max_length=70,
        blank=True,
        help_text="SEO title (max 70 chars)"
    )
    meta_description = models.CharField(
        max_length=160,
        blank=True,
        help_text="SEO description (max 160 chars)"
    )

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['status']),
            models.Index(fields=['is_featured']),
            models.Index(fields=['requires_prescription']),
            models.Index(fields=['created_at']),
            models.Index(fields=['brand']),
            models.Index(fields=['category']),
        ]

    def __str__(self):
        return self.name

    @property
    def in_stock(self):
        """Check if product is available"""
        return self.stock_quantity > 0

    @property
    def primary_image(self):
        """Get primary product image"""
        return self.images.filter(is_primary=True).first() or self.images.first()

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            # Ensure unique slug
            original_slug = self.slug
            counter = 1
            while Product.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class ProductImage(models.Model):
    """
    Multiple images per product with ordering and source tracking.
    Supports images from manufacturers, suppliers, or manual uploads.
    """
    SOURCE_TYPE_CHOICES = [
        ('manual_upload', 'Manual Upload'),
        ('manufacturer', 'Manufacturer'),
        ('supplier', 'Supplier'),
        ('placeholder', 'Placeholder'),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='images'
    )
    image = models.ImageField(
        upload_to='products/',
        help_text="Product image (recommended: 800x800px)"
    )
    alt_text = models.CharField(
        max_length=200,
        blank=True,
        help_text="Image alt text for accessibility and SEO"
    )
    is_primary = models.BooleanField(
        default=False,
        help_text="Primary image shown in listings"
    )
    display_order = models.PositiveIntegerField(default=0)
    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
        default='manual_upload'
    )
    source_url = models.URLField(
        blank=True,
        help_text="Original source URL if imported"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['display_order', '-is_primary', 'created_at']
        indexes = [
            models.Index(fields=['product', 'is_primary']),
            models.Index(fields=['display_order']),
        ]

    def __str__(self):
        return f"Image for {self.product.name} ({self.display_order})"

    def save(self, *args, **kwargs):
        # If this is marked as primary, unmark others
        if self.is_primary:
            ProductImage.objects.filter(
                product=self.product,
                is_primary=True
            ).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)


# ====================
# SUPPLIER MODELS
# ====================

class Supplier(models.Model):
    """
    Supplier/vendor information for wholesale and inventory management.
    """
    name = models.CharField(max_length=200, unique=True)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    contact_name = models.CharField(max_length=200, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    email = models.EmailField(blank=True)
    website = models.URLField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['name']),
        ]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
            original_slug = self.slug
            counter = 1
            while Supplier.objects.filter(slug=self.slug).exclude(pk=self.pk).exists():
                self.slug = f"{original_slug}-{counter}"
                counter += 1
        super().save(*args, **kwargs)


class ProductSupplier(models.Model):
    """
    Many-to-many relationship between Products and Suppliers.
    Tracks supplier-specific SKUs and pricing.
    """
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name='suppliers'
    )
    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name='products'
    )
    supplier_sku = models.CharField(
        max_length=100,
        blank=True,
        help_text="Supplier's product code"
    )
    last_purchase_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Last price paid to supplier"
    )
    is_preferred = models.BooleanField(
        default=False,
        help_text="Preferred supplier for this product"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ['product', 'supplier']
        ordering = ['-is_preferred', 'supplier__name']

    def __str__(self):
        return f"{self.product.name} - {self.supplier.name}"

    def save(self, *args, **kwargs):
        # If marking as preferred, unmark others for this product
        if self.is_preferred:
            ProductSupplier.objects.filter(
                product=self.product,
                is_preferred=True
            ).exclude(pk=self.pk).update(is_preferred=False)
        super().save(*args, **kwargs)


# ====================
# CART MODELS
# ====================

class Cart(models.Model):
    """
    Shopping cart for each user.
    One user = one cart. Cart can have many items.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='cart'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Cart for {self.user.username}"

    @property
    def total_price(self):
        """Calculate total price of all items in cart"""
        return sum(item.subtotal for item in self.items.all())

    @property
    def total_items(self):
        """Count total items in cart"""
        return sum(item.quantity for item in self.items.all())


class CartItem(models.Model):
    """
    Individual items inside a cart.
    """
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE
    )
    quantity = models.PositiveIntegerField(
        default=1,
        validators=[MinValueValidator(1)]
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ['cart', 'product']

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    @property
    def subtotal(self):
        """Price for this cart item"""
        return self.product.price * self.quantity


# ====================
# ADDRESS MODELS
# ====================

class Address(models.Model):
    """
    Delivery addresses for users (Dubai-focused).
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='addresses'
    )
    full_name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=20)
    street_address = models.CharField(max_length=255)
    building = models.CharField(max_length=100, blank=True)
    area = models.CharField(max_length=100)
    city = models.CharField(max_length=100, default='Dubai')
    emirate = models.CharField(max_length=50, default='Dubai')
    postal_code = models.CharField(max_length=10, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name} - {self.area}, {self.city}"


# ====================
# ORDER MODELS
# ====================

class Order(models.Model):
    """
    Completed orders with denormalized delivery info.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Payment'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash_on_delivery', 'Cash on Delivery'),
        ('stripe', 'Stripe (Card Payment)'),
    ]

    PAYMENT_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('paid', 'Paid'),
        ('failed', 'Failed'),
        ('refunded', 'Refunded'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    order_number = models.CharField(max_length=50, unique=True, blank=True)

    # Denormalized delivery info
    full_name = models.CharField(max_length=200, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    phone = models.CharField(max_length=20, blank=True, default='')
    address = models.CharField(max_length=255, blank=True, default='')
    city = models.CharField(max_length=100, blank=True, default='')
    emirate = models.CharField(max_length=50, blank=True, default='')
    delivery_notes = models.TextField(blank=True, default='')

    # Status
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )

    # Payment
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash_on_delivery'
    )
    payment_status = models.CharField(
        max_length=20,
        choices=PAYMENT_STATUS_CHOICES,
        default='pending'
    )
    stripe_payment_intent_id = models.CharField(max_length=255, blank=True, null=True)
    stripe_client_secret = models.CharField(max_length=255, blank=True, null=True)

    # Pricing
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['order_number']),
        ]

    def __str__(self):
        return f"Order #{self.order_number} - {self.user.username}"

    def save(self, *args, **kwargs):
        if not self.order_number:
            from django.utils import timezone
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
            random_str = ''.join(random.choices(string.ascii_uppercase + string.digits, k=4))
            self.order_number = f"ORD-{timestamp}-{random_str}"
        super().save(*args, **kwargs)


class OrderItem(models.Model):
    """
    Items in a completed order with price snapshot.
    """
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True
    )
    product_name = models.CharField(max_length=200)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    @property
    def subtotal(self):
        return self.price_at_purchase * self.quantity
