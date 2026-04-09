from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.shortcuts import get_object_or_404
from django.db.models import Q, Prefetch

from .models import (
    Brand, Category, Product, ProductImage,
    Supplier, ProductSupplier,
    Cart, CartItem, Address, Order, OrderItem
)
from .serializers import (
    UserRegistrationSerializer, UserSerializer,
    BrandSerializer,
    CategorySerializer, CategoryListSerializer,
    ProductListSerializer, ProductDetailSerializer, ProductCreateUpdateSerializer,
    ProductImageSerializer,
    SupplierSerializer, ProductSupplierSerializer,
    CartSerializer, CartItemSerializer,
    AddressSerializer,
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
    """Register a new user account."""
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        Cart.objects.create(user=user)
        try:
            send_welcome_email(user)
        except Exception as email_error:
            print(f"Email error: {email_error}")
        return Response({
            'message': 'User registered successfully!',
            'user': UserSerializer(user).data
        }, status=status.HTTP_201_CREATED)
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_current_user(request):
    """Get details of currently logged-in user."""
    serializer = UserSerializer(request.user)
    return Response(serializer.data)


@api_view(['POST'])
@permission_classes([AllowAny])
def request_password_reset(request):
    """Request password reset - sends email with reset link."""
    email = request.data.get('email', '').strip()
    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {'message': 'If an account with that email exists, a password reset link has been sent.'},
            status=status.HTTP_200_OK
        )

    reset_token = PasswordResetToken.create_token(user)
    frontend_url = 'http://localhost:3000'
    reset_url = f"{frontend_url}/reset-password/{reset_token}"

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
    """Confirm password reset with token."""
    token = request.data.get('token', '').strip()
    password = request.data.get('password', '')
    password_confirm = request.data.get('password_confirm', '')

    if not all([token, password, password_confirm]):
        return Response(
            {'error': 'Token, password, and password confirmation are required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    if password != password_confirm:
        return Response({'error': 'Passwords do not match'}, status=status.HTTP_400_BAD_REQUEST)

    if len(password) < 8:
        return Response(
            {'error': 'Password must be at least 8 characters long'},
            status=status.HTTP_400_BAD_REQUEST
        )

    user = PasswordResetToken.validate_token(token)
    if not user:
        return Response({'error': 'Invalid or expired reset token'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(password)
    user.save()
    PasswordResetToken.mark_used(token)

    return Response(
        {'message': 'Password has been reset successfully. You can now login with your new password.'},
        status=status.HTTP_200_OK
    )


# ====================
# BRAND VIEWS
# ====================

class BrandViewSet(viewsets.ModelViewSet):
    """ViewSet for Brands."""
    queryset = Brand.objects.all()
    serializer_class = BrandSerializer
    permission_classes = [AllowAny]
    lookup_field = 'slug'

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]


# ====================
# CATEGORY VIEWS
# ====================

class CategoryViewSet(viewsets.ModelViewSet):
    """ViewSet for Categories with hierarchy support."""
    queryset = Category.objects.all()
    permission_classes = [AllowAny]
    lookup_field = 'slug'

    def get_serializer_class(self):
        if self.action == 'list':
            # Check if flat list requested
            if self.request.query_params.get('flat') == 'true':
                return CategoryListSerializer
        return CategorySerializer

    def get_queryset(self):
        queryset = Category.objects.all()

        # Filter active only for non-admin users
        if not self.request.user.is_staff:
            queryset = queryset.filter(is_active=True)

        # Option to get only root categories (no parent)
        if self.request.query_params.get('root') == 'true':
            queryset = queryset.filter(parent__isnull=True)

        return queryset.order_by('display_order', 'name')

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]


# ====================
# PRODUCT VIEWS
# ====================

class ProductViewSet(viewsets.ModelViewSet):
    """ViewSet for Products with advanced search and filtering."""
    queryset = Product.objects.all()
    permission_classes = [AllowAny]
    lookup_field = 'slug'

    def get_serializer_class(self):
        if self.action in ['retrieve']:
            return ProductDetailSerializer
        if self.action in ['create', 'update', 'partial_update']:
            return ProductCreateUpdateSerializer
        return ProductListSerializer

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]

    def _handle_product_image(self, product, image_file):
        """
        Handle image upload for a product.
        Creates a new ProductImage and sets it as primary.
        """
        if not image_file:
            return

        # Unset any existing primary images for this product
        ProductImage.objects.filter(product=product, is_primary=True).update(is_primary=False)

        # Create new primary image
        ProductImage.objects.create(
            product=product,
            image=image_file,
            is_primary=True,
            source_type='manual_upload',
            alt_text=product.name
        )

    def create(self, request, *args, **kwargs):
        """Create product with optional image upload."""
        # Extract image from request before serializer validation
        image_file = request.FILES.get('image')

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()

        # Handle image upload
        self._handle_product_image(product, image_file)

        # Return full product details
        return Response(
            ProductDetailSerializer(product, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

    def update(self, request, *args, **kwargs):
        """Update product with optional image upload."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        # Extract image from request before serializer validation
        image_file = request.FILES.get('image')

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()

        # Handle image upload (only if new image provided)
        self._handle_product_image(product, image_file)

        # Return full product details
        return Response(
            ProductDetailSerializer(product, context={'request': request}).data
        )

    def get_queryset(self):
        """
        Advanced product filtering and search.
        Supports: search, category, brand, featured, status
        """
        queryset = Product.objects.select_related('brand', 'category').prefetch_related(
            Prefetch('images', queryset=ProductImage.objects.order_by('-is_primary', 'display_order'))
        )

        # Non-admin users only see active products
        if not self.request.user.is_staff:
            queryset = queryset.filter(status='active')

        # Filter by category (including children)
        category_slug = self.request.query_params.get('category')
        category_id = self.request.query_params.get('category_id')
        if category_slug:
            try:
                category = Category.objects.get(slug=category_slug)
                # Include child categories
                category_ids = [category.id] + list(category.children.values_list('id', flat=True))
                queryset = queryset.filter(category_id__in=category_ids)
            except Category.DoesNotExist:
                pass
        elif category_id:
            queryset = queryset.filter(category_id=category_id)

        # Filter by brand
        brand_slug = self.request.query_params.get('brand')
        brand_id = self.request.query_params.get('brand_id')
        if brand_slug:
            queryset = queryset.filter(brand__slug=brand_slug)
        elif brand_id:
            queryset = queryset.filter(brand_id=brand_id)

        # Filter by featured
        if self.request.query_params.get('featured') == 'true':
            queryset = queryset.filter(is_featured=True)

        # Filter by prescription requirement
        prescription = self.request.query_params.get('prescription')
        if prescription == 'true':
            queryset = queryset.filter(requires_prescription=True)
        elif prescription == 'false':
            queryset = queryset.filter(requires_prescription=False)

        # Filter by in_stock
        if self.request.query_params.get('in_stock') == 'true':
            queryset = queryset.filter(stock_quantity__gt=0)

        # Search
        search_query = self.request.query_params.get('search', '').strip()
        if search_query:
            queryset = queryset.filter(
                Q(name__icontains=search_query) |
                Q(brand__name__icontains=search_query) |
                Q(active_ingredient__icontains=search_query) |
                Q(short_description__icontains=search_query)
            ).order_by('name')[:20]
        else:
            queryset = queryset.order_by('-is_featured', 'name')

        return queryset


# ====================
# PRODUCT IMAGE VIEWS
# ====================

class ProductImageViewSet(viewsets.ModelViewSet):
    """ViewSet for Product Images (admin only for modifications)."""
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = ProductImage.objects.all()
        product_id = self.request.query_params.get('product')
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        return queryset.order_by('-is_primary', 'display_order')

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            return [permissions.IsAdminUser()]
        return [AllowAny()]


# ====================
# SUPPLIER VIEWS
# ====================

class SupplierViewSet(viewsets.ModelViewSet):
    """ViewSet for Suppliers (admin only)."""
    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAdminUser]
    lookup_field = 'slug'


class ProductSupplierViewSet(viewsets.ModelViewSet):
    """ViewSet for Product-Supplier relationships (admin only)."""
    queryset = ProductSupplier.objects.all()
    serializer_class = ProductSupplierSerializer
    permission_classes = [permissions.IsAdminUser]

    def get_queryset(self):
        queryset = ProductSupplier.objects.select_related('product', 'supplier')
        product_id = self.request.query_params.get('product')
        supplier_id = self.request.query_params.get('supplier')
        if product_id:
            queryset = queryset.filter(product_id=product_id)
        if supplier_id:
            queryset = queryset.filter(supplier_id=supplier_id)
        return queryset


# ====================
# CART VIEWS
# ====================

class CartViewSet(viewsets.ViewSet):
    """Custom ViewSet for Cart operations."""
    permission_classes = [IsAuthenticated]

    def list(self, request):
        """GET /api/cart/ - Get current user's cart."""
        cart, created = Cart.objects.get_or_create(user=request.user)
        serializer = CartSerializer(cart, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def add_item(self, request):
        """POST /api/cart/add_item/ - Add product to cart."""
        product_id = request.data.get('product_id')
        quantity = request.data.get('quantity', 1)

        if not product_id:
            return Response({'error': 'product_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        cart, _ = Cart.objects.get_or_create(user=request.user)

        try:
            product = Product.objects.get(id=product_id, status='active')
        except Product.DoesNotExist:
            return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

        if product.stock_quantity < quantity:
            return Response(
                {'error': f'Only {product.stock_quantity} items in stock'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart_item, created = CartItem.objects.get_or_create(
            cart=cart, product=product, defaults={'quantity': quantity}
        )

        if not created:
            cart_item.quantity += quantity
            if cart_item.quantity > product.stock_quantity:
                return Response(
                    {'error': f'Only {product.stock_quantity} items available'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            cart_item.save()

        return Response(
            CartItemSerializer(cart_item, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=False, methods=['patch'])
    def update_item(self, request):
        """PATCH /api/cart/update_item/ - Update cart item quantity."""
        cart_item_id = request.data.get('cart_item_id')
        quantity = request.data.get('quantity')

        if not cart_item_id or quantity is None:
            return Response(
                {'error': 'cart_item_id and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = CartItem.objects.get(id=cart_item_id, cart__user=request.user)
        except CartItem.DoesNotExist:
            return Response({'error': 'Cart item not found'}, status=status.HTTP_404_NOT_FOUND)

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
        return Response(CartItemSerializer(cart_item, context={'request': request}).data)

    @action(detail=False, methods=['delete'])
    def remove_item(self, request):
        """DELETE /api/cart/remove_item/ - Remove item from cart."""
        cart_item_id = request.data.get('cart_item_id')

        if not cart_item_id:
            return Response({'error': 'cart_item_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            cart_item = CartItem.objects.get(id=cart_item_id, cart__user=request.user)
            cart_item.delete()
            return Response({'message': 'Item removed from cart'})
        except CartItem.DoesNotExist:
            return Response({'error': 'Cart item not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['delete'])
    def clear(self, request):
        """DELETE /api/cart/clear/ - Clear entire cart."""
        cart = get_object_or_404(Cart, user=request.user)
        cart.items.all().delete()
        return Response({'message': 'Cart cleared'})


# ====================
# ADDRESS VIEWS
# ====================

class AddressViewSet(viewsets.ModelViewSet):
    """Manage user delivery addresses."""
    serializer_class = AddressSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Address.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


# ====================
# ORDER VIEWS
# ====================

class OrderViewSet(viewsets.ModelViewSet):
    """Manage orders."""
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if self.request.user.is_staff:
            return Order.objects.all().prefetch_related('items', 'items__product')
        return Order.objects.filter(user=self.request.user).prefetch_related('items', 'items__product')

    def get_serializer_class(self):
        if self.action == 'create':
            return OrderCreateSerializer
        return OrderSerializer

    @action(detail=True, methods=['patch'], permission_classes=[permissions.IsAdminUser])
    def update_status(self, request, pk=None):
        """PATCH /api/orders/{id}/update_status/ - Admin update order status."""
        order = self.get_object()
        old_status = order.status
        new_status = request.data.get('status')

        if not new_status:
            return Response({'error': 'status field is required'}, status=status.HTTP_400_BAD_REQUEST)

        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Valid options: {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Handle stock restoration for cancellation
        if new_status == 'cancelled' and old_status != 'cancelled':
            for item in order.items.all():
                if item.product:
                    item.product.stock_quantity += item.quantity
                    item.product.save()

        # Handle stock deduction for uncancellation
        elif old_status == 'cancelled' and new_status != 'cancelled':
            for item in order.items.all():
                if item.product:
                    if item.product.stock_quantity < item.quantity:
                        return Response(
                            {'error': f'Insufficient stock for {item.product_name}. Only {item.product.stock_quantity} available.'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    item.product.stock_quantity -= item.quantity
                    item.product.save()

        order.status = new_status

        if new_status == 'delivered' and not order.delivered_at:
            from django.utils import timezone
            order.delivered_at = timezone.now()

        if new_status in ['delivered', 'shipped'] and order.payment_method == 'cash_on_delivery':
            order.payment_status = 'paid'

        order.save()

        try:
            send_order_status_update_email(order, old_status)
        except Exception as email_error:
            print(f"Email error: {email_error}")

        return Response(OrderSerializer(order, context={'request': request}).data)

    def create(self, request):
        """Create order from current cart."""
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

        # Validate stock
        out_of_stock_items = []
        for cart_item in cart.items.select_related('product'):
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
            return Response({'error': error_message.rstrip(', ')}, status=status.HTTP_400_BAD_REQUEST)

        # Extract delivery info
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
        if not full_name: missing_fields.append('full name')
        if not email: missing_fields.append('email')
        if not phone: missing_fields.append('phone number')
        if not address: missing_fields.append('delivery address')
        if not city: missing_fields.append('city')
        if not emirate: missing_fields.append('emirate')

        if missing_fields:
            return Response(
                {'error': f'Please provide: {", ".join(missing_fields)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if '@' not in email or '.' not in email:
            return Response({'error': 'Please provide a valid email address'}, status=status.HTTP_400_BAD_REQUEST)

        if len(phone.replace(' ', '').replace('-', '')) < 10:
            return Response(
                {'error': 'Please provide a valid phone number (at least 10 digits)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # Save address for future use
            existing_address = Address.objects.filter(
                user=request.user, street_address=address, city=city, emirate=emirate
            ).first()

            if not existing_address:
                Address.objects.create(
                    user=request.user,
                    full_name=full_name,
                    phone_number=phone,
                    street_address=address,
                    city=city,
                    emirate=emirate,
                    area=city,
                    is_default=Address.objects.filter(user=request.user).count() == 0
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

            # Create order items and update stock
            for cart_item in cart.items.select_related('product'):
                OrderItem.objects.create(
                    order=order,
                    product=cart_item.product,
                    product_name=cart_item.product.name,
                    quantity=cart_item.quantity,
                    price_at_purchase=cart_item.product.price
                )
                cart_item.product.stock_quantity -= cart_item.quantity
                cart_item.product.save()

            # Clear cart
            cart.items.all().delete()

            # Send confirmation email
            try:
                send_order_confirmation_email(order)
            except Exception as email_error:
                print(f"Email error: {email_error}")

            return Response(
                OrderSerializer(order, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )

        except Exception as e:
            if 'order' in locals():
                order.delete()
            return Response(
                {'error': f'Failed to create order: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
