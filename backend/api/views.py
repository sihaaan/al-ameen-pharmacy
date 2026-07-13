import logging

from rest_framework import viewsets, status, permissions, mixins
from rest_framework.decorators import action, api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.contrib.auth.models import User
from django.contrib.auth.tokens import default_token_generator
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.contrib.postgres.search import SearchVector, SearchQuery, SearchRank
from django.db import transaction
from django.db.models import Q, Prefetch
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode

from .models import (
    Brand, Category, Product, ProductImage,
    Supplier, ProductSupplier,
    Cart, CartItem, Address, Order, OrderItem
)
from .serializers import (
    UserRegistrationSerializer, UserSerializer,
    BrandSerializer,
    CategorySerializer, CategoryListSerializer,
    ProductListSerializer, ProductOptionSerializer, ProductDetailSerializer, ProductCreateUpdateSerializer,
    ProductImageSerializer,
    SupplierSerializer, ProductSupplierSerializer,
    CartSerializer, CartItemSerializer,
    AddressSerializer,
    OrderSerializer, OrderCreateSerializer
)
from .emails import (
    send_order_confirmation_email,
    send_staff_order_notification_email,
    send_order_status_update_email,
    send_welcome_email,
    send_password_reset_email
)
from .throttles import (
    PasswordResetConfirmRateThrottle,
    PasswordResetRateThrottle,
    RegistrationRateThrottle,
)
from .upload_validation import validate_image_upload
from quotations.matching import create_or_reuse_product


logger = logging.getLogger(__name__)


def _send_order_confirmation_after_commit(order_id):
    try:
        order = Order.objects.prefetch_related('items').get(pk=order_id)
    except Order.DoesNotExist:
        logger.warning("Order confirmation skipped because order %s no longer exists", order_id)
        return

    try:
        send_order_confirmation_email(order)
    except Exception as email_error:
        logger.warning("Order confirmation email failed for order %s: %s", order_id, email_error)
    try:
        send_staff_order_notification_email(order)
    except Exception as email_error:
        logger.warning("Staff order notification email failed for order %s: %s", order_id, email_error)


def _send_order_status_after_commit(order_id, old_status):
    try:
        order = Order.objects.get(pk=order_id)
        send_order_status_update_email(order, old_status)
    except Exception as email_error:
        logger.warning("Order status email failed for order %s: %s", order_id, email_error)


# ====================
# AUTHENTICATION VIEWS
# ====================

@api_view(['POST'])
@permission_classes([AllowAny])
@throttle_classes([RegistrationRateThrottle])
def register_user(request):
    """Register a new user account."""
    serializer = UserRegistrationSerializer(data=request.data)
    if serializer.is_valid():
        user = serializer.save()
        Cart.objects.create(user=user)
        try:
            send_welcome_email(user)
        except Exception as email_error:
            logger.warning("Welcome email failed for user %s: %s", user.pk, email_error)
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
@throttle_classes([PasswordResetRateThrottle])
def request_password_reset(request):
    """Request password reset - sends email with reset link."""
    email = request.data.get('email', '').strip()
    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    user = User.objects.filter(email__iexact=email).order_by('id').first()
    if not user:
        return Response(
            {'message': 'If an account with that email exists, a password reset link has been sent.'},
            status=status.HTTP_200_OK
        )

    uidb64 = urlsafe_base64_encode(force_bytes(user.pk))
    reset_token = default_token_generator.make_token(user)
    packed_token = f"{uidb64}.{reset_token}"
    frontend_url = getattr(settings, "FRONTEND_URL", "http://localhost:3000").rstrip("/")
    reset_url = f"{frontend_url}/reset-password/{packed_token}"

    try:
        send_password_reset_email(user, packed_token, reset_url)
    except Exception as email_error:
        logger.warning("Password reset email failed for user %s: %s", user.pk, email_error)
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
@throttle_classes([PasswordResetConfirmRateThrottle])
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

    uidb64, separator, raw_token = token.partition(".")
    if not separator:
        return Response({'error': 'Invalid or expired reset token'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        return Response({'error': 'Invalid or expired reset token'}, status=status.HTTP_400_BAD_REQUEST)

    if not default_token_generator.check_token(user, raw_token):
        return Response({'error': 'Invalid or expired reset token'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        return Response({'error': list(exc.messages)}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(password)
    user.save()

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
        if self.action == 'list' and self.request.query_params.get('compact') == 'true':
            return ProductOptionSerializer
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
        validate_image_upload(image_file, label="Product image")

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
        """Create or safely reuse a product, with optional image upload."""
        # Extract image from request before serializer validation
        image_file = request.FILES.get('image')
        if image_file:
            validate_image_upload(image_file, label="Product image")

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        values = dict(serializer.validated_data)
        name = values.pop('name')
        resolution = create_or_reuse_product(
            name=name,
            sku=values.get('sku') or '',
            barcode=values.get('barcode') or '',
            dosage=values.get('dosage') or '',
            pack_size=values.get('pack_size') or '',
            defaults=values,
            confirm_create=str(request.data.get('confirm_create') or '').lower() in {'1', 'true', 'yes', 'on'},
        )
        if resolution.requires_confirmation:
            return Response(
                {'detail': resolution.warning, **resolution.as_dict()},
                status=status.HTTP_409_CONFLICT,
            )
        product = resolution.product

        # A create request that safely reuses an existing identity must not
        # silently replace that Product's image or other catalog fields.
        if resolution.created:
            self._handle_product_image(product, image_file)

        # Return full product details
        payload = dict(ProductDetailSerializer(product, context={'request': request}).data)
        payload.update(resolution.as_dict())
        return Response(payload, status=status.HTTP_201_CREATED if resolution.created else status.HTTP_200_OK)

    def update(self, request, *args, **kwargs):
        """Update product with optional image upload."""
        partial = kwargs.pop('partial', False)
        instance = self.get_object()

        # Extract image from request before serializer validation
        image_file = request.FILES.get('image')
        if image_file:
            validate_image_upload(image_file, label="Product image")

        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        serializer.is_valid(raise_exception=True)
        product = serializer.save()

        # Handle image upload (only if new image provided)
        self._handle_product_image(product, image_file)

        # Return full product details
        return Response(
            ProductDetailSerializer(product, context={'request': request}).data
        )

    def destroy(self, request, *args, **kwargs):
        """
        Archive products that have business history instead of hard deleting.

        Quotation workflows now use Product as the master item identity, so old
        quotations and price history must remain readable even if an item should
        no longer be visible on the public catalog.
        """
        product = self.get_object()
        quotation_related_managers = [
            'quotation_lines',
            'quotation_inquiry_lines',
            'historical_import_lines',
            'company_price_history',
            'quotation_aliases',
        ]
        has_quotation_refs = any(
            hasattr(product, manager_name) and getattr(product, manager_name).exists()
            for manager_name in quotation_related_managers
        )
        has_commerce_refs = (
            CartItem.objects.filter(product=product).exists()
            or OrderItem.objects.filter(product=product).exists()
        )
        if has_quotation_refs or has_commerce_refs:
            product.status = 'archived'
            product.save(update_fields=['status', 'updated_at'])
            return Response(
                ProductDetailSerializer(product, context={'request': request}).data,
                status=status.HTTP_200_OK,
            )
        return super().destroy(request, *args, **kwargs)

    def get_queryset(self):
        """
        Advanced product filtering and search.
        Supports: search, category, brand, featured, status
        """
        compact = self.request.query_params.get('compact') == 'true'
        if compact:
            queryset = Product.objects.all()
        else:
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
            if len(search_query) < 3:
                # Fast autocomplete search
                queryset = queryset.filter(
                    Q(name__icontains=search_query) |
                    Q(brand__name__icontains=search_query) |
                    Q(sku__icontains=search_query)
                ).order_by('name')[:10]
            else:
                # Full-text search with ranking
                search_vector = (
                    SearchVector('name', weight='A') +
                    SearchVector('short_description', weight='B') +
                    SearchVector('brand__name', weight='C') +
                    SearchVector('active_ingredient', weight='C')
                )
                search_query_obj = SearchQuery(search_query, search_type='websearch')

                queryset = queryset.annotate(
                    search=search_vector,
                    rank=SearchRank(search_vector, search_query_obj)
                ).filter(
                    Q(search=search_query_obj) |
                    Q(name__icontains=search_query) |
                    Q(brand__name__icontains=search_query) |
                    Q(active_ingredient__icontains=search_query)
                ).order_by('-rank', 'name')[:20]
        else:
            queryset = queryset.order_by('name' if compact else '-is_featured', 'name')

        if compact and not search_query:
            try:
                limit = int(self.request.query_params.get('limit', 200))
            except (TypeError, ValueError):
                limit = 200
            queryset = queryset[:max(1, min(limit, 500))]

        return queryset

    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Lightweight product summary for admin dashboard stats."""
        return Response({'count': self.get_queryset().count()})


# ====================
# PRODUCT IMAGE VIEWS
# ====================

class ProductImageViewSet(viewsets.ModelViewSet):
    """ViewSet for Product Images (admin only for modifications)."""
    queryset = ProductImage.objects.all()
    serializer_class = ProductImageSerializer
    permission_classes = [AllowAny]

    def get_queryset(self):
        queryset = ProductImage.objects.select_related('product')
        if not self.request.user.is_staff:
            queryset = queryset.filter(product__status='active')
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

    def _cart_product_block_reason(self, product):
        if not product or product.status != 'active':
            return 'This product is not currently available for online checkout.'
        if product.requires_prescription:
            return 'This product requires prescription review. Please send an inquiry instead of adding it to cart.'
        if not product.show_price:
            return 'This product is inquiry-only and cannot be added to cart directly.'
        return ''

    def _parse_quantity(self, value, *, allow_zero=False):
        try:
            quantity = int(value)
        except (TypeError, ValueError):
            return None, Response(
                {'error': 'Quantity must be a whole number'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        minimum = 0 if allow_zero else 1
        if quantity < minimum:
            return None, Response(
                {'error': f'Quantity must be at least {minimum}'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return quantity, None

    def list(self, request):
        """GET /api/cart/ - Get current user's cart."""
        cart, created = Cart.objects.get_or_create(user=request.user)
        serializer = CartSerializer(cart, context={'request': request})
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def add_item(self, request):
        """POST /api/cart/add_item/ - Add product to cart."""
        product_id = request.data.get('product_id')
        quantity, error_response = self._parse_quantity(request.data.get('quantity', 1))
        if error_response:
            return error_response

        if not product_id:
            return Response({'error': 'product_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with transaction.atomic():
                cart, _ = Cart.objects.select_for_update().get_or_create(user=request.user)
                product = Product.objects.select_for_update().get(id=product_id, status='active')
                block_reason = self._cart_product_block_reason(product)
                if block_reason:
                    return Response({'error': block_reason}, status=status.HTTP_400_BAD_REQUEST)
                if product.stock_quantity < quantity:
                    return Response(
                        {'error': f'Only {product.stock_quantity} items in stock'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                cart_item, created = CartItem.objects.select_for_update().get_or_create(
                    cart=cart, product=product, defaults={'quantity': quantity}
                )

                if not created:
                    cart_item.quantity += quantity
                    if cart_item.quantity > product.stock_quantity:
                        return Response(
                            {'error': f'Only {product.stock_quantity} items available'},
                            status=status.HTTP_400_BAD_REQUEST
                        )
                    cart_item.save(update_fields=['quantity'])
        except (Product.DoesNotExist, ValueError):
            return Response({'error': 'Product not found'}, status=status.HTTP_404_NOT_FOUND)

        return Response(
            CartItemSerializer(cart_item, context={'request': request}).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK
        )

    @action(detail=False, methods=['patch'])
    def update_item(self, request):
        """PATCH /api/cart/update_item/ - Update cart item quantity."""
        cart_item_id = request.data.get('cart_item_id')
        quantity, error_response = self._parse_quantity(request.data.get('quantity'), allow_zero=True)
        if error_response:
            return error_response

        if not cart_item_id or quantity is None:
            return Response(
                {'error': 'cart_item_id and quantity are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            cart_item = CartItem.objects.select_related('product').get(id=cart_item_id, cart__user=request.user)
        except CartItem.DoesNotExist:
            return Response({'error': 'Cart item not found'}, status=status.HTTP_404_NOT_FOUND)

        if quantity <= 0:
            cart_item.delete()
            return Response({'message': 'Item removed from cart'})

        block_reason = self._cart_product_block_reason(cart_item.product)
        if block_reason:
            return Response({'error': block_reason}, status=status.HTTP_400_BAD_REQUEST)

        if quantity > cart_item.product.stock_quantity:
            return Response(
                {'error': f'Only {cart_item.product.stock_quantity} items in stock'},
                status=status.HTTP_400_BAD_REQUEST
            )

        cart_item.quantity = quantity
        cart_item.save(update_fields=['quantity'])
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

class OrderViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
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

    def _validate_delivery_payload(self, request):
        delivery = {
            'full_name': request.data.get('full_name', '').strip(),
            'email': request.data.get('email', '').strip(),
            'phone': request.data.get('phone', '').strip(),
            'address': request.data.get('address', '').strip(),
            'city': request.data.get('city', '').strip(),
            'emirate': request.data.get('emirate', '').strip(),
            'delivery_notes': request.data.get('notes', '').strip(),
            'payment_method': request.data.get('payment_method', 'cash_on_delivery'),
        }

        missing_fields = []
        if not delivery['full_name']: missing_fields.append('full name')
        if not delivery['email']: missing_fields.append('email')
        if not delivery['phone']: missing_fields.append('phone number')
        if not delivery['address']: missing_fields.append('delivery address')
        if not delivery['city']: missing_fields.append('city')
        if not delivery['emirate']: missing_fields.append('emirate')

        if missing_fields:
            return None, Response(
                {'error': f'Please provide: {", ".join(missing_fields)}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if '@' not in delivery['email'] or '.' not in delivery['email']:
            return None, Response({'error': 'Please provide a valid email address'}, status=status.HTTP_400_BAD_REQUEST)

        if len(delivery['phone'].replace(' ', '').replace('-', '')) < 10:
            return None, Response(
                {'error': 'Please provide a valid phone number (at least 10 digits)'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if delivery['payment_method'] != 'cash_on_delivery':
            return None, Response(
                {'error': 'Card payment is not available yet. Please use cash on delivery.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return delivery, None

    @action(detail=True, methods=['patch'], permission_classes=[permissions.IsAdminUser])
    def update_status(self, request, pk=None):
        """PATCH /api/orders/{id}/update_status/ - Admin update order status."""
        new_status = request.data.get('status')

        if not new_status:
            return Response({'error': 'status field is required'}, status=status.HTTP_400_BAD_REQUEST)

        valid_statuses = [choice[0] for choice in Order.STATUS_CHOICES]
        if new_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Valid options: {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        order_id = self.get_object().pk
        allowed_transitions = {
            'pending': {'processing', 'cancelled'},
            'processing': {'shipped', 'cancelled'},
            'shipped': {'delivered'},
            'delivered': set(),
            'cancelled': {'pending', 'processing'},
        }

        with transaction.atomic():
            order = Order.objects.select_for_update().get(pk=order_id)
            old_status = order.status
            if old_status == new_status:
                return Response(OrderSerializer(order, context={'request': request}).data)

            if new_status not in allowed_transitions.get(old_status, set()):
                return Response(
                    {'error': f'Cannot change order from {old_status} to {new_status}.'},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            items = list(order.items.select_related('product'))
            product_ids = [item.product_id for item in items if item.product_id]
            products = Product.objects.select_for_update().in_bulk(product_ids)

            if new_status == 'cancelled' and old_status != 'cancelled':
                for item in items:
                    product = products.get(item.product_id)
                    if product:
                        product.stock_quantity += item.quantity
                        product.save(update_fields=['stock_quantity'])

            elif old_status == 'cancelled' and new_status != 'cancelled':
                insufficient = []
                for item in items:
                    product = products.get(item.product_id)
                    if product and product.stock_quantity < item.quantity:
                        insufficient.append(f"{item.product_name} (available: {product.stock_quantity})")
                if insufficient:
                    return Response(
                        {'error': f'Insufficient stock for: {", ".join(insufficient)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                for item in items:
                    product = products.get(item.product_id)
                    if product:
                        product.stock_quantity -= item.quantity
                        product.save(update_fields=['stock_quantity'])

            order.status = new_status

            if new_status == 'delivered' and not order.delivered_at:
                order.delivered_at = timezone.now()
            if new_status == 'delivered' and order.payment_method == 'cash_on_delivery':
                order.payment_status = 'paid'
            order.save(update_fields=['status', 'delivered_at', 'payment_status', 'updated_at'])

        transaction.on_commit(lambda: _send_order_status_after_commit(order.pk, old_status))

        return Response(OrderSerializer(order, context={'request': request}).data)

    def create(self, request):
        """Create order from current cart."""
        delivery, error_response = self._validate_delivery_payload(request)
        if error_response:
            return error_response

        try:
            with transaction.atomic():
                try:
                    cart = Cart.objects.select_for_update().get(user=request.user)
                except Cart.DoesNotExist:
                    return Response(
                        {'error': 'Your cart was not found. Please try adding items to your cart first.'},
                        status=status.HTTP_404_NOT_FOUND
                    )

                cart_items = list(cart.items.select_related('product').select_for_update())
                if not cart_items:
                    return Response(
                        {'error': 'Your cart is empty. Please add items before checking out.'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                product_ids = [cart_item.product_id for cart_item in cart_items]
                products = Product.objects.select_for_update().in_bulk(product_ids)
                blocked_items = []
                out_of_stock_items = []
                for cart_item in cart_items:
                    product = products.get(cart_item.product_id)
                    if not product or product.status != 'active' or product.requires_prescription or not product.show_price:
                        blocked_items.append(cart_item.product.name if cart_item.product else f"Product {cart_item.product_id}")
                        continue
                    if product.stock_quantity < cart_item.quantity:
                        out_of_stock_items.append({
                            'name': product.name,
                            'requested': cart_item.quantity,
                            'available': product.stock_quantity
                        })

                if blocked_items:
                    return Response(
                        {'error': f'These items cannot be checked out directly: {", ".join(blocked_items)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                if out_of_stock_items:
                    error_message = 'Some items in your cart are out of stock: '
                    for item in out_of_stock_items:
                        error_message += f"{item['name']} (requested: {item['requested']}, available: {item['available']}), "
                    return Response({'error': error_message.rstrip(', ')}, status=status.HTTP_400_BAD_REQUEST)

                existing_address = Address.objects.filter(
                    user=request.user,
                    street_address=delivery['address'],
                    city=delivery['city'],
                    emirate=delivery['emirate']
                ).first()

                if not existing_address:
                    Address.objects.create(
                        user=request.user,
                        full_name=delivery['full_name'],
                        phone_number=delivery['phone'],
                        street_address=delivery['address'],
                        city=delivery['city'],
                        emirate=delivery['emirate'],
                        area=delivery['city'],
                        is_default=Address.objects.filter(user=request.user).count() == 0
                    )

                total_amount = sum(products[item.product_id].price * item.quantity for item in cart_items)
                order = Order.objects.create(
                    user=request.user,
                    full_name=delivery['full_name'],
                    email=delivery['email'],
                    phone=delivery['phone'],
                    address=delivery['address'],
                    city=delivery['city'],
                    emirate=delivery['emirate'],
                    delivery_notes=delivery['delivery_notes'],
                    payment_method=delivery['payment_method'],
                    total_amount=total_amount,
                    status='pending'
                )

                for cart_item in cart_items:
                    product = products[cart_item.product_id]
                    OrderItem.objects.create(
                        order=order,
                        product=product,
                        product_name=product.name,
                        quantity=cart_item.quantity,
                        price_at_purchase=product.price
                    )
                    product.stock_quantity -= cart_item.quantity
                    product.save(update_fields=['stock_quantity'])

                cart.items.all().delete()
                transaction.on_commit(lambda: _send_order_confirmation_after_commit(order.pk))

            return Response(
                OrderSerializer(order, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )

        except Exception as e:
            logger.exception("Order creation failed for user %s", request.user.pk)
            return Response(
                {'error': 'Failed to create order. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
