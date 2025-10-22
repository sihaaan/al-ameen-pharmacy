from django.contrib import admin
from .models import Category, Product, Cart, CartItem, Address, Order, OrderItem


# ====================
# ADMIN CONFIGURATIONS
# ====================

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'product_count', 'created_at']
    search_fields = ['name']

    def product_count(self, obj):
        return obj.products.count()
    product_count.short_description = 'Products'


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    # List view configuration
    list_display = ['image_preview', 'name', 'category', 'price', 'stock_quantity', 'in_stock', 'manufacturer', 'requires_prescription']
    list_filter = ['category', 'requires_prescription', 'manufacturer', 'created_at']
    search_fields = ['name', 'description', 'detailed_description', 'manufacturer']
    list_editable = ['price', 'stock_quantity']
    list_per_page = 25

    # Form organization in detail/edit view
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'category', 'manufacturer', 'requires_prescription')
        }),
        ('Product Details', {
            'fields': ('description', 'detailed_description', 'dosage', 'pack_size')
        }),
        ('Pricing & Stock', {
            'fields': ('price', 'stock_quantity'),
            'classes': ('wide',)
        }),
        ('Images', {
            'fields': ('image', 'image_url'),
            'description': 'Upload an image or provide an image URL. Uploaded images are preferred.'
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )

    # Make timestamp fields read-only
    readonly_fields = ['created_at', 'updated_at', 'image_preview']

    # Custom method to display image preview in list view
    def image_preview(self, obj):
        if obj.image:
            return f'<img src="{obj.image.url}" width="50" height="50" style="object-fit: cover; border-radius: 4px;" />'
        elif obj.image_url:
            return f'<img src="{obj.image_url}" width="50" height="50" style="object-fit: cover; border-radius: 4px;" />'
        return '(No image)'
    image_preview.short_description = 'Image'
    image_preview.allow_tags = True


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ['user', 'total_items', 'total_price', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ['cart', 'product', 'quantity', 'subtotal', 'added_at']
    list_filter = ['added_at']


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ['full_name', 'user', 'area', 'city', 'phone_number', 'is_default']
    list_filter = ['city', 'emirate', 'is_default']
    search_fields = ['full_name', 'area', 'phone_number']


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'status', 'total_amount', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['user__username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'product_name', 'quantity', 'price_at_purchase', 'subtotal']
    list_filter = ['order__status']
    search_fields = ['product_name', 'order__user__username']
