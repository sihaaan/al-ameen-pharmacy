from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Django REST Framework Router
router = DefaultRouter()

# Catalog endpoints
router.register(r'brands', views.BrandViewSet, basename='brand')
router.register(r'categories', views.CategoryViewSet, basename='category')
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'product-images', views.ProductImageViewSet, basename='product-image')

# Supplier endpoints (admin only)
router.register(r'suppliers', views.SupplierViewSet, basename='supplier')
router.register(r'product-suppliers', views.ProductSupplierViewSet, basename='product-supplier')

# Cart & Order endpoints
router.register(r'cart', views.CartViewSet, basename='cart')
router.register(r'addresses', views.AddressViewSet, basename='address')
router.register(r'orders', views.OrderViewSet, basename='order')

urlpatterns = [
    # Authentication endpoints
    path('register/', views.register_user, name='register'),
    path('me/', views.get_current_user, name='current-user'),

    # Password reset endpoints
    path('password-reset/', views.request_password_reset, name='password-reset-request'),
    path('password-reset/confirm/', views.reset_password_confirm, name='password-reset-confirm'),

    # Include router URLs
    path('', include(router.urls)),
]
