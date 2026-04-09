from django.contrib import admin
from django.utils.html import format_html
from .models import (
    Brand, Category, Product, ProductImage,
    Supplier, ProductSupplier,
    Cart, CartItem, Address, Order, OrderItem
)


# ====================
# INLINE ADMINS
# ====================

class ProductImageInline(admin.TabularInline):
    """Inline images on Product admin page"""
    model = ProductImage
    extra = 1
    fields = ['image', 'alt_text', 'is_primary', 'display_order', 'source_type']
    readonly_fields = ['created_at']


class ProductSupplierInline(admin.TabularInline):
    """Inline suppliers on Product admin page"""
    model = ProductSupplier
    extra = 0
    fields = ['supplier', 'supplier_sku', 'last_purchase_price', 'is_preferred']
    autocomplete_fields = ['supplier']


class CategoryChildrenInline(admin.TabularInline):
    """Inline child categories"""
    model = Category
    fk_name = 'parent'
    extra = 0
    fields = ['name', 'slug', 'is_active', 'display_order']
    readonly_fields = ['slug']
    verbose_name = "Subcategory"
    verbose_name_plural = "Subcategories"


# ====================
# BRAND ADMIN
# ====================

@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ['logo_preview', 'name', 'slug', 'product_count', 'created_at']
    list_display_links = ['name']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 25

    def logo_preview(self, obj):
        if obj.logo:
            return format_html(
                '<img src="{}" width="40" height="40" style="object-fit: contain; border-radius: 4px; background: #f5f5f5; padding: 2px;" />',
                obj.logo.url
            )
        return '—'
    logo_preview.short_description = 'Logo'

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


# ====================
# CATEGORY ADMIN
# ====================

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'parent', 'is_active', 'display_order', 'product_count', 'created_at']
    list_filter = ['is_active', 'parent']
    search_fields = ['name', 'slug']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']
    list_editable = ['is_active', 'display_order']
    list_per_page = 25
    inlines = [CategoryChildrenInline]

    fieldsets = (
        (None, {
            'fields': ('name', 'slug', 'parent', 'description')
        }),
        ('Display Settings', {
            'fields': ('is_active', 'display_order')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


# ====================
# PRODUCT ADMIN
# ====================

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = [
        'image_preview', 'name', 'brand', 'category', 'price',
        'stock_quantity', 'status', 'is_featured', 'requires_prescription'
    ]
    list_filter = [
        'status', 'is_featured', 'requires_prescription',
        'brand', 'category', 'requires_manual_review', 'created_at'
    ]
    search_fields = ['name', 'slug', 'sku', 'barcode', 'active_ingredient']
    list_editable = ['price', 'stock_quantity', 'status', 'is_featured']
    prepopulated_fields = {'slug': ('name',)}
    autocomplete_fields = ['brand', 'category']
    readonly_fields = ['created_at', 'updated_at', 'image_preview']
    list_per_page = 25
    inlines = [ProductImageInline, ProductSupplierInline]
    save_on_top = True

    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'slug', 'brand', 'category')
        }),
        ('Descriptions', {
            'fields': ('short_description', 'detailed_description')
        }),
        ('Pricing & Inventory', {
            'fields': ('price', 'stock_quantity', 'sku', 'barcode')
        }),
        ('Pharmacy Details', {
            'fields': ('requires_prescription', 'dosage', 'pack_size', 'active_ingredient')
        }),
        ('Status & Visibility', {
            'fields': ('status', 'requires_manual_review', 'is_featured')
        }),
        ('SEO', {
            'fields': ('meta_title', 'meta_description'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def image_preview(self, obj):
        primary = obj.primary_image
        if primary and primary.image:
            return format_html(
                '<img src="{}" width="50" height="50" style="object-fit: cover; border-radius: 4px;" />',
                primary.image.url
            )
        return '(No image)'
    image_preview.short_description = 'Image'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('brand', 'category').prefetch_related('images')


# ====================
# PRODUCT IMAGE ADMIN
# ====================

@admin.register(ProductImage)
class ProductImageAdmin(admin.ModelAdmin):
    list_display = ['image_preview', 'product', 'is_primary', 'display_order', 'source_type', 'created_at']
    list_filter = ['is_primary', 'source_type', 'created_at']
    search_fields = ['product__name', 'alt_text']
    list_editable = ['is_primary', 'display_order']
    autocomplete_fields = ['product']
    readonly_fields = ['created_at', 'updated_at', 'image_preview']
    list_per_page = 25

    def image_preview(self, obj):
        if obj.image:
            return format_html(
                '<img src="{}" width="60" height="60" style="object-fit: cover; border-radius: 4px;" />',
                obj.image.url
            )
        return '(No image)'
    image_preview.short_description = 'Preview'


# ====================
# SUPPLIER ADMIN
# ====================

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = ['name', 'contact_name', 'phone', 'email', 'product_count', 'created_at']
    search_fields = ['name', 'slug', 'contact_name', 'email']
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ['created_at', 'updated_at']
    list_per_page = 25

    fieldsets = (
        (None, {
            'fields': ('name', 'slug')
        }),
        ('Contact Information', {
            'fields': ('contact_name', 'phone', 'email', 'website')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(ProductSupplier)
class ProductSupplierAdmin(admin.ModelAdmin):
    list_display = ['product', 'supplier', 'supplier_sku', 'last_purchase_price', 'is_preferred']
    list_filter = ['is_preferred', 'supplier']
    search_fields = ['product__name', 'supplier__name', 'supplier_sku']
    autocomplete_fields = ['product', 'supplier']
    list_editable = ['is_preferred']
    list_per_page = 25


# ====================
# CART ADMIN
# ====================

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ['user', 'total_items', 'total_price', 'updated_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ['cart', 'product', 'quantity', 'subtotal', 'added_at']
    list_filter = ['added_at']
    autocomplete_fields = ['product']


# ====================
# ADDRESS ADMIN
# ====================

@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user', 'area', 'city', 'phone_number', 'is_default']
    list_filter = ['city', 'emirate', 'is_default']
    search_fields = ['full_name', 'area', 'phone_number', 'user__username']


# ====================
# ORDER ADMIN
# ====================

class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ['product', 'product_name', 'quantity', 'price_at_purchase', 'subtotal']
    can_delete = False


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = [
        'order_number', 'user', 'full_name', 'status',
        'payment_method', 'payment_status', 'total_amount', 'created_at'
    ]
    list_filter = ['status', 'payment_method', 'payment_status', 'created_at']
    search_fields = ['order_number', 'user__username', 'user__email', 'full_name', 'email', 'phone']
    readonly_fields = ['order_number', 'created_at', 'updated_at']
    inlines = [OrderItemInline]
    date_hierarchy = 'created_at'
    list_per_page = 25

    fieldsets = (
        ('Order Info', {
            'fields': ('order_number', 'user', 'status')
        }),
        ('Delivery Information', {
            'fields': ('full_name', 'email', 'phone', 'address', 'city', 'emirate', 'delivery_notes')
        }),
        ('Payment', {
            'fields': ('payment_method', 'payment_status', 'total_amount')
        }),
        ('Stripe (if applicable)', {
            'fields': ('stripe_payment_intent_id', 'stripe_client_secret'),
            'classes': ('collapse',)
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at', 'delivered_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product_name', 'quantity', 'price_at_purchase', 'subtotal']
    list_filter = ['order__status']
    search_fields = ['product_name', 'order__order_number', 'order__user__username']
