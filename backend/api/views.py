from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404

from .models import Category, Product, Cart, CartItem, Address, Order, OrderItem
from .serializers import (
    UserRegistrationSerializer, UserSerializer,
    CategorySerializer, ProductListSerializer, ProductDetailSerializer,
    CartSerializer, CartItemSerializer, AddressSerializer,
    OrderSerializer, OrderCreateSerializer
)


# ====================
# AUTHENTICATION VIEWS
# ====================

@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    """
    Register a new user account.
    Anyone can access (no login required).
    """
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        # Automatically create a cart for the new user
        Cart.objects.create(user=user)
        return Response({
            'message': 'User registered successfully!',
            'user': UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    """
    Get details of currently logged-in user.
    Requires authentication token.
    """
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


# ====================
# CATEGORY VIEWS
# ====================

class CategoryViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Categories.
    Provides: list, retrieve, create, update, delete
    """
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]  # Anyone can view categories

    def get_permissions(self):
        """
        Only allow admins to create/update/delete categories.
        Everyone can view (list/retrieve).
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]


# ====================
# PRODUCT VIEWS
# ====================

class ProductViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Products.
    Uses different serializers for list vs detail views.
    """
    queryset = Product.objects.all()
    permission_classes = [AllowAny]

    def get_serializer_class(self):
        """Use detailed serializer for single product and create/update, simple for lists"""
        if self.action in ['retrieve', 'create', 'update', 'partial_update']:
            return ProductDetailSerializer
        return ProductListSerializer

    def get_permissions(self):
        """Only admins can modify products"""
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]

    def get_queryset(self):
        """
        Filter products by category if requested.
        Example: /api/products/?category=1
        """
        queryset = Product.objects.all()
        category_id = self.request.query_params.get('category', None)
        if category_id:
            queryset = queryset.filter(category_id=category_id)
        return queryset


# ====================
# CART VIEWS
# ====================

class CartViewSet(viewsets.ViewSet):
    """
    Custom ViewSet for Cart operations.
    Each user has one cart.
    """
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """
        GET /api/cart/
        Get current user's cart with all items.
        """
        cart, created = Cart.objects.get_or_create(user=request.user)
        serializer = CartSerializer(cart)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def add_item(self, request):
        """
        POST /api/cart/add_item/
        Add product to cart or increase quantity.
        Body: {"product_id": 1, "quantity": 2}
        """
        product_id = request.data.get('product_id')
        quantity = request.data.get('quantity', 1)

        if not product_id:
            return Response(
                {'error': 'product_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get or create cart
        cart, _ = Cart.objects.get_or_create(user=request.user)

        # Get product
        try:
            product = Product.objects.get(id=product_id)
        except Product.DoesNotExist:
            return Response(
                {'error': 'Product not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check stock
        if product.stock_quantity < quantity:
            return Response(
                {'error': f'Only {product.stock_quantity} items in stock'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Add to cart or update quantity
        cart_item, created = CartItem.objects.get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity}
        )

        if not created:
            # Item already in cart, increase quantity
            cart_item.quantity += quantity
            if cart_item.quantity > product.stock_quantity:
                return Response(
                    {'error': f'Only {product.stock_quantity} items available'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.save()

        return Response(
            CartItemSerializer(cart_item).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=False, methods=['patch'])
    def update_item(self, request):
        """
        PATCH /api/cart/update_item/
        Update quantity of cart item.
        Body: {"cart_item_id": 1, "quantity": 3}
        """
        cart_item_id = request.data.get('cart_item_id')
        quantity = request.data.get('quantity')

        if not cart_item_id or quantity is None:
            return Response(
                {'error': 'cart_item_id and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = CartItem.objects.get(
                id=cart_item_id,
                cart__user=request.user
            )
        except CartItem.DoesNotExist:
            return Response(
                {'error': 'Cart item not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Check stock
        if quantity > cart_item.product.stock_quantity:
            return Response(
                {'error': f'Only {cart_item.product.stock_quantity} items in stock'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if quantity <= 0:
            cart_item.delete()
            return Response({'message': 'Item removed from cart'})

        cart_item.quantity = quantity
        cart_item.save()

        return Response(CartItemSerializer(cart_item).data)

    @action(detail=False, methods=['delete'])
    def remove_item(self, request):
        """
        DELETE /api/cart/remove_item/
        Remove item from cart.
        Body: {"cart_item_id": 1}
        """
        cart_item_id = request.data.get('cart_item_id')

        if not cart_item_id:
            return Response(
                {'error': 'cart_item_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = CartItem.objects.get(
                id=cart_item_id,
                cart__user=request.user
            )
            cart_item.delete()
            return Response({'message': 'Item removed from cart'})
        except CartItem.DoesNotExist:
            return Response(
                {'error': 'Cart item not found'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=False, methods=['delete'])
    def clear(self, request):
        """
        DELETE /api/cart/clear/
        Remove all items from cart.
        """
        cart = get_object_or_404(Cart, user=request.user)
        cart.items.all().delete()
        return Response({'message': 'Cart cleared'})


# ====================
# ADDRESS VIEWS
# ====================

class AddressViewSet(viewsets.ModelViewSet):
    """
    Manage user delivery addresses.
    Users can only see/edit their own addresses.
    """
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Users can only see their own addresses"""
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        """Automatically set user when creating address"""
        serializer.save(user=self.request.user)


# ====================
# ORDER VIEWS
# ====================

class OrderViewSet(viewsets.ModelViewSet):
    """
    Manage orders.
    Users can view their orders and create new ones.
    """
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """Users can only see their own orders"""
        return Order.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        """Use different serializer for creating orders"""
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer

    def create(self, request):
        """
        Create order from current cart.
        Requires delivery_address_id in request.
        """
        # Get user's cart
        try:
            cart = Cart.objects.get(user=request.user)
        except Cart.DoesNotExist:
            return Response(
                {'error': 'Cart not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        if not cart.items.exists():
            return Response(
                {'error': 'Cart is empty'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Get delivery address
        address_id = request.data.get('delivery_address')
        if not address_id:
            return Response(
                {'error': 'delivery_address is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            address = Address.objects.get(id=address_id, user=request.user)
        except Address.DoesNotExist:
            return Response(
                {'error': 'Address not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # Create order
        order = Order.objects.create(
            user=request.user,
            delivery_address=address,
            total_amount=cart.total_price
        )

        # Create order items from cart items
        for cart_item in cart.items.all():
            OrderItem.objects.create(
                order=order,
                product=cart_item.product,
                product_name=cart_item.product.name,
                quantity=cart_item.quantity,
                price_at_purchase=cart_item.product.price
            )

            # Decrease stock
            product = cart_item.product
            product.stock_quantity -= cart_item.quantity
            product.save()

        # Clear cart
        cart.items.all().delete()

        return Response(
            OrderSerializer(order).data,
            status=status.HTTP_201_CREATED
        )
