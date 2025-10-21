from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from decimal import Decimal

# ====================
# PHARMACY E-COMMERCE MODELS
# ====================

class Category(models.Model):
    """
    Product categories like 'Pain Relief', 'Vitamins', 'First Aid', etc.
    Think of this like folders organizing your medicines.
    """
    name = models.CharField(max_length=100, unique=True)
    description = models.TextField(blank=True)  # blank=True means optional
    created_at = models.DateTimeField(auto_now_add=True)  # Auto-set on creation

    class Meta:
        verbose_name_plural = "Categories"  # Proper plural in admin panel
        ordering = ['name']  # Order alphabetically

    def __str__(self):
        # This makes it display nicely in admin panel
        return self.name


class Product(models.Model):
    """
    Individual medicines/health products your pharmacy sells.
    Each product has name, price, stock, description, etc.
    """
    name = models.CharField(max_length=200)
    description = models.TextField()
    price = models.DecimalField(
        max_digits=10,  # Total digits (including decimals)
        decimal_places=2,  # Digits after decimal: 12.50
        validators=[MinValueValidator(Decimal('0.01'))]  # Must be positive
    )
    stock_quantity = models.IntegerField(
        default=0,
        validators=[MinValueValidator(0)]  # Can't be negative
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,  # If category deleted, product stays but category=NULL
        null=True,
        blank=True,
        related_name='products'  # Access category.products.all()
    )
    image_url = models.URLField(blank=True, null=True)  # Product image
    requires_prescription = models.BooleanField(default=False)  # Some medicines need Rx

    # Automatic timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)  # Auto-update on save

    class Meta:
        ordering = ['-created_at']  # Newest first (- means descending)

    def __str__(self):
        return self.name

    @property
    def in_stock(self):
        """Helper property to check if product is available"""
        return self.stock_quantity > 0


class Cart(models.Model):
    """
    Shopping cart for each user.
    One user = one cart. Cart can have many items.
    """
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,  # If user deleted, delete their cart too
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
    Example: 3x Paracetamol, 1x Vitamin D
    """
    cart = models.ForeignKey(
        Cart,
        on_delete=models.CASCADE,
        related_name='items'  # Access cart.items.all()
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE
    )
    quantity = models.IntegerField(
        default=1,
        validators=[MinValueValidator(1)]
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # A product can only appear once in a cart (just increase quantity)
        unique_together = ['cart', 'product']

    def __str__(self):
        return f"{self.quantity}x {self.product.name}"

    @property
    def subtotal(self):
        """Price for this cart item (quantity * unit price)"""
        return self.product.price * self.quantity


class Address(models.Model):
    """
    Delivery addresses for users (important for pharmacy delivery in Dubai!)
    Users can have multiple addresses (home, work, etc.)
    """
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='addresses'
    )
    full_name = models.CharField(max_length=200)
    phone_number = models.CharField(max_length=20)
    street_address = models.CharField(max_length=255)
    building = models.CharField(max_length=100, blank=True)  # Villa/Building number
    area = models.CharField(max_length=100)  # Dubai Marina, JBR, etc.
    city = models.CharField(max_length=100, default='Dubai')
    emirate = models.CharField(max_length=50, default='Dubai')
    postal_code = models.CharField(max_length=10, blank=True)
    is_default = models.BooleanField(default=False)

    class Meta:
        verbose_name_plural = "Addresses"

    def __str__(self):
        return f"{self.full_name} - {self.area}, {self.city}"


class Order(models.Model):
    """
    When customer completes checkout, Cart becomes an Order.
    Orders are permanent records of purchases.
    """
    STATUS_CHOICES = [
        ('pending', 'Pending Payment'),
        ('processing', 'Processing'),
        ('shipped', 'Shipped'),
        ('delivered', 'Delivered'),
        ('cancelled', 'Cancelled'),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='orders'
    )
    delivery_address = models.ForeignKey(
        Address,
        on_delete=models.SET_NULL,
        null=True
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending'
    )
    total_amount = models.DecimalField(max_digits=10, decimal_places=2)

    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    delivered_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Order #{self.id} - {self.user.username} - {self.status}"


class OrderItem(models.Model):
    """
    Individual products in an order.
    We store price at time of purchase (in case product price changes later).
    """
    order = models.ForeignKey(
        Order,
        on_delete=models.CASCADE,
        related_name='items'
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,  # Keep order even if product deleted
        null=True
    )
    product_name = models.CharField(max_length=200)  # Store name in case product deleted
    quantity = models.IntegerField(validators=[MinValueValidator(1)])
    price_at_purchase = models.DecimalField(max_digits=10, decimal_places=2)

    def __str__(self):
        return f"{self.quantity}x {self.product_name}"

    @property
    def subtotal(self):
        return self.price_at_purchase * self.quantity
