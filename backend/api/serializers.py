from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    Brand, Category, Product, ProductImage,
    Supplier, ProductSupplier,
    Cart, CartItem, Address, Order, OrderItem
)


# ====================
# USER SERIALIZERS
# ====================

class UserRegistrationSerializer(serializers.ModelSerializer):
    """For creating new user accounts with password hashing."""
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'password', 'password_confirm', 'first_name', 'last_name']
        extra_kwargs = {
            'email': {'required': True},
        }

    def validate(self, data):
        if data['password'] != data['password_confirm']:
            raise serializers.ValidationError("Passwords don't match!")
        return data

    def create(self, validated_data):
        validated_data.pop('password_confirm')
        user = User.objects.create_user(
            username=validated_data['username'],
            email=validated_data['email'],
            password=validated_data['password'],
            first_name=validated_data.get('first_name', ''),
            last_name=validated_data.get('last_name', '')
        )
        return user


class UserSerializer(serializers.ModelSerializer):
    """For displaying user information (without password)."""
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'is_staff', 'date_joined']
        read_only_fields = ['id', 'is_staff', 'date_joined']


# ====================
# BRAND SERIALIZERS
# ====================

class BrandSerializer(serializers.ModelSerializer):
    """Brand serializer with product count."""
    product_count = serializers.SerializerMethodField()
    logo_url = serializers.SerializerMethodField()

    class Meta:
        model = Brand
        fields = ['id', 'name', 'slug', 'logo', 'logo_url', 'product_count', 'created_at']
        read_only_fields = ['id', 'slug', 'created_at']

    def get_product_count(self, obj):
        return obj.products.filter(status='active').count()

    def get_logo_url(self, obj):
        if obj.logo:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.logo.url)
            return obj.logo.url
        return None


# ====================
# CATEGORY SERIALIZERS
# ====================

class CategorySerializer(serializers.ModelSerializer):
    """Category serializer with hierarchy support."""
    product_count = serializers.SerializerMethodField()
    parent_name = serializers.CharField(source='parent.name', read_only=True, allow_null=True)
    children = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'slug', 'description', 'parent', 'parent_name',
            'is_active', 'display_order', 'product_count', 'children', 'created_at'
        ]
        read_only_fields = ['id', 'slug', 'created_at']

    def get_product_count(self, obj):
        return obj.products.filter(status='active').count()

    def get_children(self, obj):
        children = obj.children.filter(is_active=True)
        if children.exists():
            return CategorySerializer(children, many=True, context=self.context).data
        return []


class CategoryListSerializer(serializers.ModelSerializer):
    """Simplified category serializer for dropdowns/lists."""
    full_path = serializers.CharField(read_only=True)

    class Meta:
        model = Category
        fields = ['id', 'name', 'slug', 'full_path', 'parent', 'is_active']


# ====================
# PRODUCT IMAGE SERIALIZERS
# ====================

class ProductImageSerializer(serializers.ModelSerializer):
    """Product image serializer."""
    image_url = serializers.SerializerMethodField()

    class Meta:
        model = ProductImage
        fields = [
            'id', 'product', 'image', 'image_url', 'alt_text', 'is_primary',
            'display_order', 'source_type', 'source_url', 'created_at'
        ]
        read_only_fields = ['id', 'created_at']

    def get_image_url(self, obj):
        if obj.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.image.url)
            return obj.image.url
        return None


# ====================
# PRODUCT SERIALIZERS
# ====================

class ProductListSerializer(serializers.ModelSerializer):
    """
    Lightweight product serializer for listings.
    Includes primary image and basic info.
    """
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True, allow_null=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True, allow_null=True)
    brand_slug = serializers.CharField(source='brand.slug', read_only=True, allow_null=True)
    primary_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'short_description', 'price', 'stock_quantity',
            'category', 'category_name', 'category_slug',
            'brand', 'brand_name', 'brand_slug',
            'primary_image_url', 'dosage', 'pack_size',
            'requires_prescription', 'is_featured', 'show_price', 'status', 'in_stock', 'created_at'
        ]
        read_only_fields = ['id', 'slug', 'in_stock', 'created_at']

    def get_primary_image_url(self, obj):
        primary = obj.primary_image
        if primary and primary.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(primary.image.url)
            return primary.image.url
        return None


class ProductDetailSerializer(serializers.ModelSerializer):
    """
    Full product serializer with all details, images, and related data.
    """
    category_name = serializers.CharField(source='category.name', read_only=True, allow_null=True)
    category_slug = serializers.CharField(source='category.slug', read_only=True, allow_null=True)
    brand_name = serializers.CharField(source='brand.name', read_only=True, allow_null=True)
    brand_slug = serializers.CharField(source='brand.slug', read_only=True, allow_null=True)
    images = ProductImageSerializer(many=True, read_only=True)
    primary_image_url = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            'id', 'name', 'slug', 'brand', 'brand_name', 'brand_slug',
            'category', 'category_name', 'category_slug',
            'short_description', 'detailed_description',
            'price', 'stock_quantity', 'sku', 'barcode',
            'requires_prescription', 'dosage', 'pack_size', 'active_ingredient',
            'status', 'is_featured', 'show_price',
            'meta_title', 'meta_description',
            'images', 'primary_image_url',
            'in_stock', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'slug', 'in_stock', 'created_at', 'updated_at']

    def get_primary_image_url(self, obj):
        primary = obj.primary_image
        if primary and primary.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(primary.image.url)
            return primary.image.url
        return None


class ProductCreateUpdateSerializer(serializers.ModelSerializer):
    """Serializer for creating/updating products (admin use)."""

    class Meta:
        model = Product
        fields = [
            'name', 'brand', 'category',
            'short_description', 'detailed_description',
            'price', 'stock_quantity', 'sku', 'barcode',
            'requires_prescription', 'dosage', 'pack_size', 'active_ingredient',
            'status', 'requires_manual_review', 'is_featured', 'show_price',
            'meta_title', 'meta_description'
        ]


# ====================
# SUPPLIER SERIALIZERS
# ====================

class SupplierSerializer(serializers.ModelSerializer):
    """Supplier serializer."""
    product_count = serializers.SerializerMethodField()

    class Meta:
        model = Supplier
        fields = [
            'id', 'name', 'slug', 'contact_name', 'phone', 'email',
            'website', 'product_count', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'slug', 'created_at', 'updated_at']

    def get_product_count(self, obj):
        return obj.products.count()


class ProductSupplierSerializer(serializers.ModelSerializer):
    """Product-Supplier relationship serializer."""
    supplier_name = serializers.CharField(source='supplier.name', read_only=True)
    product_name = serializers.CharField(source='product.name', read_only=True)

    class Meta:
        model = ProductSupplier
        fields = [
            'id', 'product', 'product_name', 'supplier', 'supplier_name',
            'supplier_sku', 'last_purchase_price', 'is_preferred',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


# ====================
# CART SERIALIZERS
# ====================

class CartItemSerializer(serializers.ModelSerializer):
    """Cart item with product details."""
    product = ProductListSerializer(read_only=True)
    product_id = serializers.IntegerField(write_only=True, required=False)
    subtotal = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)

    class Meta:
        model = CartItem
        fields = ['id', 'product', 'product_id', 'quantity', 'subtotal', 'added_at']
        read_only_fields = ['id', 'subtotal', 'added_at']

    def validate_quantity(self, value):
        if value < 1:
            raise serializers.ValidationError("Quantity must be at least 1")
        return value


class CartSerializer(serializers.ModelSerializer):
    """Complete cart with items and totals."""
    items = CartItemSerializer(many=True, read_only=True)
    total_price = serializers.DecimalField(max_digits=10, decimal_places=2, read_only=True)
    total_items = serializers.IntegerField(read_only=True)

    class Meta:
        model = Cart
        fields = ['id', 'user', 'items', 'total_price', 'total_items', 'created_at', 'updated_at']
        read_only_fields = ['id', 'user', 'created_at', 'updated_at']


# ====================
# ADDRESS SERIALIZERS
# ====================

class AddressSerializer(serializers.ModelSerializer):
    """Delivery address serializer."""
    class Meta:
        model = Address
        fields = [
            'id', 'full_name', 'phone_number', 'street_address',
            'building', 'area', 'city', 'emirate', 'postal_code', 'is_default'
        ]
        read_only_fields = ['id']

    def validate_phone_number(self, value):
        cleaned = value.replace(' ', '').replace('-', '').replace('(', '').replace(')', '')
        if not cleaned or len(cleaned) < 7:
            raise serializers.ValidationError("Please enter a valid phone number")
        digits_only = cleaned.lstrip('+')
        if not digits_only.isdigit():
            raise serializers.ValidationError("Phone number should contain only digits and optional + prefix")
        return value


# ====================
# ORDER SERIALIZERS
# ====================

class OrderItemSerializer(serializers.ModelSerializer):
    """Order item with product image."""
    product_image = serializers.SerializerMethodField()

    class Meta:
        model = OrderItem
        fields = [
            'id', 'product', 'product_name', 'product_image',
            'quantity', 'price_at_purchase', 'subtotal'
        ]
        read_only_fields = ['id', 'subtotal', 'product_image']

    def get_product_image(self, obj):
        if not obj.product:
            return None
        primary = obj.product.primary_image
        if primary and primary.image:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(primary.image.url)
            return primary.image.url
        return None


class OrderSerializer(serializers.ModelSerializer):
    """Complete order serializer."""
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
    """For creating new orders from cart."""
    class Meta:
        model = Order
        fields = [
            'full_name', 'email', 'phone', 'address',
            'city', 'emirate', 'delivery_notes', 'payment_method'
        ]

    def validate_phone(self, value):
        if not value or len(value.strip()) < 10:
            raise serializers.ValidationError("Please enter a valid phone number")
        return value

    def validate_email(self, value):
        if not value or '@' not in value:
            raise serializers.ValidationError("Please enter a valid email address")
        return value
