from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from django.db.models import Q, F

from .models import Category, Product, Cart, CartItem, Address, Order, OrderItem
from .serializers import (
    UserRegistrationSerializer, UserSerializer,
    CategorySerializer, ProductListSerializer, ProductDetailSerializer,
    CartSerializer, CartItemSerializer, AddressSerializer,
    OrderSerializer, OrderCreateSerializer
)
from .emails import (
    send_order_confirmation_email,
    send_order_status_update_email,
    send_welcome_email,
    send_password_reset_email
)
from .password_reset import PasswordResetToken


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

        # Send welcome email
        try:
            send_welcome_email(user)
        except Exception as email_error:
            # Log email error but don't fail the registration
            print(f"Email error: {email_error}")

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


@api_view(['POST'])
@permission_classes([AllowAny])
def request_password_reset(request):
    """
    Request password reset - sends email with reset link
    Body: {"email": "user@example.com"}
    """
    email = request.data.get('email', '').strip()

    if not email:
        return Response(
            {'error': 'Email is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        # For security, don't reveal if email exists or not
        return Response(
            {'message': 'If an account with that email exists, a password reset link has been sent.'},
            status=status.HTTP_200_OK
        )

    # Create reset token
    reset_token = PasswordResetToken.create_token(user)

    # Build reset URL (frontend URL)
    frontend_url = 'http://localhost:3000'  # Change for production
    reset_url = f"{frontend_url}/reset-password/{reset_token}"

    # Send email
    try:
        send_password_reset_email(user, reset_token, reset_url)
    except Exception as email_error:
        print(f"Email error: {email_error}")
        return Response(
            {'error': 'Failed to send password reset email. Please try again later.'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    return Response(
        {'message': 'If an account with that email exists, a password reset link has been sent.'},
        status=status.HTTP_200_OK
    )


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password_confirm(request):
    """
    Confirm password reset with token
    Body: {
        "token": "reset-token",
        "password": "new-password",
        "password_confirm": "new-password"
    }
    """
    token = request.data.get('token', '').strip()
    password = request.data.get('password', '')
    password_confirm = request.data.get('password_confirm', '')

    if not all([token, password, password_confirm]):
        return Response(
            {'error': 'Token, password, and password confirmation are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if password != password_confirm:
        return Response(
            {'error': 'Passwords do not match'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if len(password) < 8:
        return Response(
            {'error': 'Password must be at least 8 characters long'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Validate token
    user = PasswordResetToken.validate_token(token)

    if not user:
        return Response(
            {'error': 'Invalid or expired reset token'},
            status=status.HTTP_400_BAD_REQUEST
        )

    # Update password
    user.set_password(password)
    user.save()

    # Mark token as used
    PasswordResetToken.mark_used(token)

    return Response(
        {'message': 'Password has been reset successfully. You can now login with your new password.'},
        status=status.HTTP_200_OK
    )


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
        Advanced product search with PostgreSQL full-text search and relevance ranking.

        Features:
        - Full-text search across name, description, and manufacturer
        - Relevance-based ranking (exact matches rank higher)
        - Weighted search (name weighted highest, then description, then manufacturer)
        - Handles pharmaceutical synonyms and partial matches
        - Category filtering
        - Sorted by relevance score

        Examples:
        - /api/products/?search=panadol
        - /api/products/?category=1
        - /api/products/?search=pain&category=1
        """
        queryset = Product.objects.all()
        search_query = self.request.query_params.get('search', None)
        category_id = self.request.query_params.get('category', None)

        # Apply category filter
        if category_id:
            queryset = queryset.filter(category_id=category_id)

        # Apply search with performance optimization
        if search_query and search_query.strip():
            query_term = search_query.strip()

            # For autocomplete (short queries), use fast case-insensitive search
            # For detailed search (3+ chars), use full-text search with ranking
            if len(query_term) < 3:
                # Fast path: Simple ILIKE query (much faster than full-text search)
                queryset = queryset.filter(
                    Q(name__icontains=query_term) |
                    Q(manufacturer__icontains=query_term)
                ).order_by('name')[:10]  # Limit immediately for speed
            else:
                # Full path: PostgreSQL full-text search with relevance ranking
                # Create weighted search vectors for different fields
                search_vector = (
                    SearchVector('name', weight='A') +
                    SearchVector('description', weight='B') +
                    SearchVector('manufacturer', weight='C')
                )

                # Create search query (supports partial matching)
                search_query_obj = SearchQuery(query_term, search_type='websearch')

                # Annotate with search rank and filter
                queryset = queryset.annotate(
                    search=search_vector,
                    rank=SearchRank(search_vector, search_query_obj)
                ).filter(
                    Q(search=search_query_obj) |  # Full-text search
                    Q(name__icontains=query_term) |  # Fallback contains
                    Q(manufacturer__icontains=query_term)
                ).order_by('-rank', 'name')[:20]  # Limit for performance

        else:
            # No search query - return all products ordered by name
            queryset = queryset.order_by('name')

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
    Admins can view and manage all orders.
    """
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        """
        Regular users see only their orders.
        Admins see all orders.
        """
        if self.request.user.is_staff:
            return Order.objects.all()
        return Order.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        """Use different serializer for creating orders"""
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer

    @action(detail=True, methods=['patch'], permission_classes=[permissions.IsAdminUser])
    def update_status(self, request, pk=None):
        """
        PATCH /api/orders/{id}/update_status/
        Admin-only endpoint to update order status.
        Body: {"status": "processing"}
        """
        order = self.get_object()
        old_status = order.status
        new_status = request.data.get('status')

        if not new_status:
            return Response(
                {'error': 'status field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate status choice
        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Valid options: {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # If cancelling an order, restore stock quantities
        if new_status == 'cancelled' and old_status != 'cancelled':
            for item in order.items.all():
                if item.product:  # Check product still exists
                    item.product.stock_quantity += item.quantity
                    item.product.save()

        # If uncancelling an order (changing from cancelled to another status)
        # Decrease stock again
        elif old_status == 'cancelled' and new_status != 'cancelled':
            for item in order.items.all():
                if item.product:
                    # Check if enough stock available
                    if item.product.stock_quantity < item.quantity:
                        return Response(
                            {'error': f'Insufficient stock for {item.product_name}. Only {item.product.stock_quantity} available.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    item.product.stock_quantity -= item.quantity
                    item.product.save()

        order.status = new_status

        # If marking as delivered, set delivered_at timestamp
        if new_status == 'delivered' and not order.delivered_at:
            from django.utils import timezone
            order.delivered_at = timezone.now()

        # If marking as delivered or shipped, update payment status to paid for COD
        if new_status in ['delivered', 'shipped'] and order.payment_method == 'cash_on_delivery':
            order.payment_status = 'paid'

        order.save()

        # Send status update email
        try:
            send_order_status_update_email(order, old_status)
        except Exception as email_error:
            # Log email error but don't fail the status update
            print(f"Email error: {email_error}")

        return Response(OrderSerializer(order).data)

    def create(self, request):
        """
        Create order from current cart.
        Supports Cash on Delivery payment.
        """

        # Get user's cart
        try:
            cart = Cart.objects.get(user=request.user)
        except Cart.DoesNotExist:
            return Response(
                {'error': 'Your cart was not found. Please try adding items to your cart first.'},
                status=status.HTTP_404_NOT_FOUND
            )

        if not cart.items.exists():
            return Response(
                {'error': 'Your cart is empty. Please add items before checking out.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate stock availability before creating order
        out_of_stock_items = []
        for cart_item in cart.items.all():
            if cart_item.product.stock_quantity < cart_item.quantity:
                out_of_stock_items.append({
                    'name': cart_item.product.name,
                    'requested': cart_item.quantity,
                    'available': cart_item.product.stock_quantity
                })

        if out_of_stock_items:
            error_message = 'Some items in your cart are out of stock: '
            for item in out_of_stock_items:
                error_message += f"{item['name']} (requested: {item['requested']}, available: {item['available']}), "
            return Response(
                {'error': error_message.rstrip(', ')},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Extract delivery information from request
        full_name = request.data.get('full_name', '').strip()
        email = request.data.get('email', '').strip()
        phone = request.data.get('phone', '').strip()
        address = request.data.get('address', '').strip()
        city = request.data.get('city', '').strip()
        emirate = request.data.get('emirate', '').strip()
        delivery_notes = request.data.get('notes', '').strip()
        payment_method = request.data.get('payment_method', 'cash_on_delivery')

        # Validate required fields
        missing_fields = []
        if not full_name:
            missing_fields.append('full name')
        if not email:
            missing_fields.append('email')
        if not phone:
            missing_fields.append('phone number')
        if not address:
            missing_fields.append('delivery address')
        if not city:
            missing_fields.append('city')
        if not emirate:
            missing_fields.append('emirate')

        if missing_fields:
            return Response(
                {'error': f'Please provide: {", ".join(missing_fields)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate email format
        if '@' not in email or '.' not in email:
            return Response(
                {'error': 'Please provide a valid email address'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate phone number
        if len(phone.replace(' ', '').replace('-', '')) < 10:
            return Response(
                {'error': 'Please provide a valid phone number (at least 10 digits)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Save address for future use (if it doesn't exist already)
            # Check if similar address exists
            existing_address = Address.objects.filter(
                user=request.user,
                street_address=address,
                city=city,
                emirate=emirate
            ).first()

            if not existing_address:
                # Create new address
                Address.objects.create(
                    user=request.user,
                    full_name=full_name,
                    phone_number=phone,
                    street_address=address,
                    city=city,
                    emirate=emirate,
                    area=city,  # Default area to city if not provided
                    is_default=Address.objects.filter(user=request.user).count() == 0  # First address is default
                )

            # Create order
            order = Order.objects.create(
                user=request.user,
                full_name=full_name,
                email=email,
                phone=phone,
                address=address,
                city=city,
                emirate=emirate,
                delivery_notes=delivery_notes,
                payment_method=payment_method,
                total_amount=cart.total_price,
                status='pending'
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

            # Clear cart after successful order creation
            cart.items.all().delete()

            # Send order confirmation email
            try:
                send_order_confirmation_email(order)
            except Exception as email_error:
                # Log email error but don't fail the order creation
                print(f"Email error: {email_error}")

            # Return order data with context for serializer
            serializer = OrderSerializer(order, context={'request': request})
            return Response(
                serializer.data,
                status=status.HTTP_201_CREATED
            )

        except Exception as e:
            # If order creation fails, ensure we don't leave orphaned data
            if 'order' in locals():
                order.delete()
            return Response(
                {'error': f'Failed to create order: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
