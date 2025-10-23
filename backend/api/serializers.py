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
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_staff', 'date_joined']
        read_only_fields = ['id', 'is_staff', 'date_joined']


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
    Provides image_display that prioritizes uploaded image over URL.
    """
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    image_display = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'price', 'stock_quantity',
            'category', 'category_name', 'image', 'image_url', 'image_display',
            'manufacturer', 'dosage', 'pack_size',
            'requires_prescription', 'in_stock', 'created_at'
        ]
        read_only_fields = ['id', 'in_stock', 'image_display', 'created_at']

    def get_image_display(self, obj):
        """Return uploaded image URL if available, otherwise image_url"""
        request = self.context.get('request')
        if obj.image:
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return obj.image_url


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    For product details (includes full description, etc.)
    Supports image upload and all new fields.
    Provides image_display that prioritizes uploaded image over URL.
    """
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    image_display = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'description', 'detailed_description',
            'price', 'stock_quantity', 'category', 'category_name',
            'image', 'image_url', 'image_display', 'manufacturer', 'dosage', 'pack_size',
            'requires_prescription', 'in_stock',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'in_stock', 'image_display', 'created_at', 'updated_at']

    def get_image_display(self, obj):
        """Return uploaded image URL if available, otherwise image_url"""
        request = self.context.get('request')
        if obj.image:
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return obj.image_url


# ====================
# CART SERIALIZERS
# ====================

class CartItemSerializer(serializers.ModelSerializer):
    """
    Individual items in cart.
    Shows product details and calculates subtotal.
    """
    product = ProductListSerializer(read_only=True)
    product_id = serializers.IntegerField(write_only=True, required=False)
    subtotal = serializers.DecimalField(
        max_digits=10,
        decimal_places=2,
        read_only=True
    )

    class Meta:
        model = CartItem
        fields = [
            'id', 'product', 'product_id', 'quantity', 'subtotal', 'added_at'
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
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'product_name', 'product_image',
            'quantity', 'price_at_purchase', 'subtotal'
        ]
        read_only_fields = ['id', 'subtotal', 'product_image']

    def get_product_image(self, obj):
        """Return product image, prioritizing uploaded image over URL"""
        if not obj.product:
            return None
        request = self.context.get('request')
        if obj.product.image:
            if request:
                return request.build_absolute_uri(obj.product.image.url)
            return obj.product.image.url
        return obj.product.image_url


class OrderSerializer(serializers.ModelSerializer):
    """
    Complete order with items and delivery info.
    """
    items = OrderItemSerializer(many=True, read_only=True)
    user_email = serializers.EmailField(source='user.email', read_only=True)
    username = serializers.CharField(source='user.username', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    payment_method_display = serializers.CharField(source='get_payment_method_display', read_only=True)
    payment_status_display = serializers.CharField(source='get_payment_status_display', read_only=True)

    class Meta:
        model = Order
        fields = [
            'id', 'user', 'username', 'user_email', 'order_number',
            'full_name', 'email', 'phone', 'address', 'city', 'emirate', 'delivery_notes',
            'status', 'status_display',
            'payment_method', 'payment_method_display',
            'payment_status', 'payment_status_display',
            'stripe_payment_intent_id',
            'total_amount', 'items',
            'created_at', 'updated_at', 'delivered_at'
        ]
        read_only_fields = [
            'id', 'user', 'username', 'user_email', 'order_number',
            'status_display', 'payment_method_display', 'payment_status_display',
            'stripe_payment_intent_id', 'created_at', 'updated_at'
        ]


class OrderCreateSerializer(serializers.ModelSerializer):
    """
    For creating new orders from cart.
    Accepts delivery information directly.
    """
    class Meta:
        model = Order
        fields = [
            'full_name', 'email', 'phone', 'address',
            'city', 'emirate', 'delivery_notes', 'payment_method'
        ]

    def validate_phone(self, value):
        """Validate phone number"""
        if not value or len(value.strip()) < 10:
            raise serializers.ValidationError("Please enter a valid phone number")
        return value

    def validate_email(self, value):
        """Validate email format"""
        if not value or '@' not in value:
            raise serializers.ValidationError("Please enter a valid email address")
        return value
