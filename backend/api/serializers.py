from rest_framework import serializers
from django.contrib.auth.models import User
from .models import Category, Product, Cart, CartItem, Address, Order, OrderItem


# ====================
# USER SERIALIZERS
# ====================

class UserRegistrationSerializer(serializers.ModelSerializer):
    """
    For creating new user accounts.
    Handles password hashing automatically.
    """
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'password_confirm', 'first_name', 'last_name']
        extra_kwargs = {
            'email': {'required': True},
        }

    def validate(self, data):
        """Check that passwords match"""
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError("Passwords don't match!")
        return data

    def create(self, validated_data):
        """Create user with hashed password"""
        validated_data.pop('password_confirm')  # Remove confirmation field
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],  # Django hashes this automatically
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', '')
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    """
    For displaying user information (without password!)
    """
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'date_joined']
        read_only_fields = ['id', 'date_joined']


# ====================
# CATEGORY & PRODUCT SERIALIZERS
# ====================

class CategorySerializer(serializers.ModelSerializer):
    """
    Simple category serializer.
    Later we can add product count, etc.
    """
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = ['id', 'name', 'description', 'product_count', 'created_at']
        read_only_fields = ['id', 'created_at']

    def get_product_count(self, obj):
        """Count how many products in this category"""
        return obj.products.count()


class ProductListSerializer(serializers.ModelSerializer):
    """
    For listing products (simpler, faster).
    Shows category name instead of just ID.
    """
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'price', 'stock_quantity',
            'category', 'category_name', 'image_url',
            'requires_prescription', 'in_stock'
        ]
        read_only_fields = ['id', 'in_stock']


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    For product details (includes full description, etc.)
    """
    category_name = serializers.CharField(source='category.name', read_only=True)

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price', 'stock_quantity',
            'category', 'category_name', 'image_url',
            'requires_prescription', 'in_stock',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'in_stock', 'created_at', 'updated_at']


# ====================
# CART SERIALIZERS
# ====================

class CartItemSerializer(serializers.ModelSerializer):
    """
    Individual items in cart.
    Shows product details and calculates subtotal.
    """
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_price = serializers.DecimalField(
        source='product.price',
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    product_image = serializers.URLField(source='product.image_url', read_only=True)
    subtotal = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True
    )

    class Meta:
        model = CartItem
        fields = [
            'id', 'product', 'product_name', 'product_price',
            'product_image', 'quantity', 'subtotal', 'added_at'
        ]
        read_only_fields = ['id', 'subtotal', 'added_at']

    def validate_quantity(self, value):
        """Check stock availability"""
        if value < 1:
            raise serializers.ValidationError("Quantity must be at least 1")
        return value


class CartSerializer(serializers.ModelSerializer):
    """
    Complete cart with all items.
    Shows totals and item details.
    """
    items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True
    )
    total_items = serializers.IntegerField(read_only=True)

    class Meta:
        model = Cart
        fields = ['id', 'user', 'items', 'total_price', 'total_items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


# ====================
# ADDRESS SERIALIZERS
# ====================

class AddressSerializer(serializers.ModelSerializer):
    """
    Delivery address for orders.
    Dubai-specific fields included.
    """
    class Meta:
        model = Address
        fields = [
            'id', 'full_name', 'phone_number', 'street_address',
            'building', 'area', 'city', 'emirate',
            'postal_code', 'is_default'
        ]
        read_only_fields = ['id']

    def validate_phone_number(self, value):
        """Basic phone validation for UAE numbers"""
        # Remove spaces and dashes
        cleaned = value.replace(' ', '').replace('-', '')
        if not cleaned.startswith('+971') and not cleaned.startswith('971') and not cleaned.startswith('0'):
            raise serializers.ValidationError("Please enter a valid UAE phone number")
        return value


# ====================
# ORDER SERIALIZERS
# ====================

class OrderItemSerializer(serializers.ModelSerializer):
    """
    Items in a completed order.
    Price is frozen at time of purchase.
    """
    product_image = serializers.URLField(source='product.image_url', read_only=True, allow_null=True)

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'product_name', 'product_image',
            'quantity', 'price_at_purchase', 'subtotal'
        ]
        read_only_fields = ['id', 'subtotal']


class OrderSerializer(serializers.ModelSerializer):
    """
    Complete order with items and delivery info.
    """
    items = OrderItemSerializer(many=True, read_only=True)
    delivery_address_details = AddressSerializer(source='delivery_address', read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'user', 'user_email', 'status', 'total_amount',
            'delivery_address', 'delivery_address_details', 'items',
            'created_at', 'updated_at', 'delivered_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrderCreateSerializer(serializers.ModelSerializer):
    """
    For creating new orders from cart.
    Simpler - just need delivery address.
    """
    class Meta:
        model = Order
        fields = ['delivery_address']

    def create(self, validated_data):
        """
        Create order from user's cart.
        This will be implemented in the view.
        """
        return super().create(validated_data)
