from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

# Django REST Framework Router
# Automatically creates URLs for ViewSets
router = DefaultRouter()
router.register(r'categories', views.CategoryViewSet, basename='category')
router.register(r'products', views.ProductViewSet, basename='product')
router.register(r'cart', views.CartViewSet, basename='cart')
router.register(r'addresses', views.AddressViewSet, basename='address')
router.register(r'orders', views.OrderViewSet, basename='order')

urlpatterns = [
    # Authentication endpoints
    path('register/', views.register_user, name='register'),
    path('me/', views.get_current_user, name='current-user'),

    # Include router URLs
    path('', include(router.urls)),
]
