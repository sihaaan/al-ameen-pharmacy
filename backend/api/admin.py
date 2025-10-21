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
    list_display = ['name', 'category', 'price', 'stock_quantity', 'in_stock', 'requires_prescription']
    list_filter = ['category', 'requires_prescription', 'created_at']
    search_fields = ['name', 'description']
    list_editable = ['price', 'stock_quantity']


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
