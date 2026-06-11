import re
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core.exceptions import ImproperlyConfigured
from django.core import mail
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .emails import send_staff_order_notification_email
from .models import Cart, CartItem, Order, OrderItem, Product, ProductImage
from .throttles import LoginRateThrottle, PasswordResetRateThrottle
from pharmacy_api.settings import normalize_origin, unique_origins


class DeploymentOriginSettingsTests(SimpleTestCase):
    def test_unique_origins_trims_slashes_whitespace_and_duplicates(self):
        origins = unique_origins([
            " https://example.com/ ",
            "https://example.com",
            "http://localhost:3000/",
        ])

        self.assertEqual(origins, ["https://example.com", "http://localhost:3000"])

    def test_unique_origins_accepts_csrf_wildcard_when_allowed(self):
        origins = unique_origins(["https://*.up.railway.app/"], allow_wildcard=True)

        self.assertEqual(origins, ["https://*.up.railway.app"])

    def test_normalize_origin_extracts_origin_from_full_url(self):
        self.assertEqual(
            normalize_origin("https://example.com/admin/login?next=/"),
            "https://example.com",
        )

    def test_normalize_origin_rejects_non_http_schemes(self):
        with self.assertRaises(ImproperlyConfigured):
            normalize_origin("ftp://example.com")


class AuthSafetyTests(APITestCase):
    def setUp(self):
        cache.clear()

    def test_registration_rejects_duplicate_email_case_insensitively(self):
        User.objects.create_user(username="existing", email="buyer@example.com", password="pass")

        response = self.client.post(
            reverse("register"),
            {
                "username": "new",
                "email": "BUYER@example.com",
                "password": "StrongPass123!",
                "password_confirm": "StrongPass123!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("email", response.data)

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        FRONTEND_URL="https://shop.example.com",
    )
    def test_password_reset_uses_signed_token_and_frontend_url(self):
        user = User.objects.create_user(username="buyer", email="buyer@example.com", password="OldPass123!")

        request_response = self.client.post(reverse("password-reset-request"), {"email": "buyer@example.com"})

        self.assertEqual(request_response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("https://shop.example.com/reset-password/", mail.outbox[0].body)
        token = re.search(r"https://shop\.example\.com/reset-password/([A-Za-z0-9_.:-]+)", mail.outbox[0].body).group(1)

        confirm_response = self.client.post(
            reverse("password-reset-confirm"),
            {"token": token, "password": "NewPass123!", "password_confirm": "NewPass123!"},
            format="json",
        )

        self.assertEqual(confirm_response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.check_password("NewPass123!"))
        reused_response = self.client.post(
            reverse("password-reset-confirm"),
            {"token": token, "password": "AnotherPass123!", "password_confirm": "AnotherPass123!"},
            format="json",
        )
        self.assertEqual(reused_response.status_code, status.HTTP_400_BAD_REQUEST)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_password_reset_request_is_throttled(self):
        with patch.object(PasswordResetRateThrottle, "THROTTLE_RATES", {"password_reset": "1/hour"}):
            first_response = self.client.post(reverse("password-reset-request"), {"email": "nobody@example.com"})
            second_response = self.client.post(reverse("password-reset-request"), {"email": "nobody@example.com"})

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_jwt_login_is_throttled(self):
        User.objects.create_user(username="buyer", email="buyer@example.com", password="StrongPass123!")

        with patch.object(LoginRateThrottle, "THROTTLE_RATES", {"login": "1/hour"}):
            first_response = self.client.post(
                reverse("token_obtain_pair"),
                {"username": "buyer", "password": "StrongPass123!"},
                format="json",
            )
            second_response = self.client.post(
                reverse("token_obtain_pair"),
                {"username": "buyer", "password": "StrongPass123!"},
                format="json",
            )

        self.assertEqual(first_response.status_code, status.HTTP_200_OK)
        self.assertEqual(second_response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_jwt_refresh_rotates_and_blacklists_old_refresh_token(self):
        User.objects.create_user(username="buyer", email="buyer@example.com", password="StrongPass123!")
        token_response = self.client.post(
            reverse("token_obtain_pair"),
            {"username": "buyer", "password": "StrongPass123!"},
            format="json",
        )
        original_refresh = token_response.data["refresh"]

        refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": original_refresh},
            format="json",
        )
        reused_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": original_refresh},
            format="json",
        )
        new_refresh_response = self.client.post(
            reverse("token_refresh"),
            {"refresh": refresh_response.data["refresh"]},
            format="json",
        )

        self.assertEqual(token_response.status_code, status.HTTP_200_OK)
        self.assertEqual(refresh_response.status_code, status.HTTP_200_OK)
        self.assertIn("access", refresh_response.data)
        self.assertIn("refresh", refresh_response.data)
        self.assertNotEqual(refresh_response.data["refresh"], original_refresh)
        self.assertEqual(reused_response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.assertEqual(new_refresh_response.status_code, status.HTTP_200_OK)


class CartOrderSafetyTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="buyer", email="buyer@example.com", password="pass")
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.product = Product.objects.create(
            name="Checkout Bandage",
            price=Decimal("12.50"),
            stock_quantity=5,
            status="active",
            show_price=True,
        )
        self.client.force_authenticate(self.user)

    def test_cart_rejects_non_numeric_quantity_cleanly(self):
        response = self.client.post(reverse("cart-add-item"), {"product_id": self.product.id, "quantity": "many"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"], "Quantity must be a whole number")

    def test_cart_rejects_prescription_products(self):
        self.product.requires_prescription = True
        self.product.save(update_fields=["requires_prescription"])

        response = self.client.post(reverse("cart-add-item"), {"product_id": self.product.id, "quantity": 1}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("prescription", response.data["error"].lower())
        self.assertFalse(CartItem.objects.exists())

    def test_cart_rejects_inquiry_only_products(self):
        self.product.show_price = False
        self.product.save(update_fields=["show_price"])

        response = self.client.post(reverse("cart-add-item"), {"product_id": self.product.id, "quantity": 1}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("inquiry-only", response.data["error"])
        self.assertFalse(CartItem.objects.exists())

    def test_cart_update_rejects_stale_prescription_item(self):
        cart, _ = Cart.objects.get_or_create(user=self.user)
        cart_item = CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        self.product.requires_prescription = True
        self.product.save(update_fields=["requires_prescription"])

        response = self.client.patch(reverse("cart-update-item"), {"cart_item_id": cart_item.id, "quantity": 2}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("prescription", response.data["error"].lower())
        cart_item.refresh_from_db()
        self.assertEqual(cart_item.quantity, 1)

    def test_checkout_rejects_stale_inquiry_only_item(self):
        cart, _ = Cart.objects.get_or_create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        self.product.show_price = False
        self.product.save(update_fields=["show_price"])

        response = self.client.post(
            reverse("order-list"),
            {
                "full_name": "Buyer",
                "email": "buyer@example.com",
                "phone": "0501234567",
                "address": "Dubai",
                "city": "Dubai",
                "emirate": "Dubai",
                "payment_method": "cash_on_delivery",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.product.name, response.data["error"])
        self.assertEqual(Order.objects.count(), 0)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 5)

    @override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
    def test_checkout_creates_order_atomically_and_decrements_stock(self):
        cart, _ = Cart.objects.get_or_create(user=self.user)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)

        response = self.client.post(
            reverse("order-list"),
            {
                "full_name": "Buyer",
                "email": "buyer@example.com",
                "phone": "0501234567",
                "address": "Dubai",
                "city": "Dubai",
                "emirate": "Dubai",
                "payment_method": "cash_on_delivery",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.product.refresh_from_db()
        self.assertEqual(self.product.stock_quantity, 3)
        self.assertEqual(Order.objects.count(), 1)
        self.assertEqual(OrderItem.objects.count(), 1)
        self.assertFalse(CartItem.objects.filter(cart=cart).exists())

    def test_customer_cannot_update_or_delete_order(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            email="buyer@example.com",
            phone="0501234567",
            address="Dubai",
            city="Dubai",
            emirate="Dubai",
            total_amount=Decimal("12.50"),
        )

        patch_response = self.client.patch(reverse("order-detail", args=[order.id]), {"status": "delivered"}, format="json")
        delete_response = self.client.delete(reverse("order-detail", args=[order.id]))

        self.assertEqual(patch_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(delete_response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        order.refresh_from_db()
        self.assertEqual(order.status, "pending")

    def test_admin_status_update_rejects_invalid_transition(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            email="buyer@example.com",
            phone="0501234567",
            address="Dubai",
            city="Dubai",
            emirate="Dubai",
            total_amount=Decimal("12.50"),
        )
        self.client.force_authenticate(self.staff)

        response = self.client.patch(reverse("order-update-status", args=[order.id]), {"status": "delivered"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Cannot change order", response.data["error"])

    def test_order_survives_user_deletion_and_stays_staff_visible(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            email="buyer@example.com",
            phone="0501234567",
            address="Dubai",
            city="Dubai",
            emirate="Dubai",
            total_amount=Decimal("12.50"),
        )

        self.user.delete()
        order.refresh_from_db()

        self.assertIsNone(order.user)

        self.client.force_authenticate(self.staff)
        response = self.client.get(reverse("order-detail", args=[order.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["user_email"], "buyer@example.com")
        self.assertEqual(response.data["username"], "")

    @override_settings(
        EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
        FRONTEND_URL="https://shop.example.com",
        ORDER_NOTIFICATION_EMAILS=["orders@example.com"],
    )
    def test_staff_order_notification_includes_admin_and_whatsapp_links(self):
        order = Order.objects.create(
            user=self.user,
            full_name="Buyer",
            email="buyer@example.com",
            phone="0501234567",
            address="Dubai",
            city="Dubai",
            emirate="Dubai",
            total_amount=Decimal("12.50"),
        )
        OrderItem.objects.create(
            order=order,
            product=self.product,
            product_name=self.product.name,
            quantity=1,
            price_at_purchase=Decimal("12.50"),
        )

        sent_count = send_staff_order_notification_email(order)

        self.assertEqual(sent_count, 1)
        self.assertEqual(mail.outbox[-1].to, ["orders@example.com"])
        self.assertIn(order.order_number, mail.outbox[-1].subject)
        self.assertIn("https://shop.example.com/admin", mail.outbox[-1].body)
        self.assertIn("https://wa.me/971501234567", mail.outbox[-1].body)


class ProductImageVisibilityTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff_images", password="pass", is_staff=True)
        self.active_product = Product.objects.create(
            name="Public Image Product",
            price=Decimal("10.00"),
            stock_quantity=5,
            status="active",
            show_price=True,
        )
        self.draft_product = Product.objects.create(
            name="Draft Image Product",
            price=Decimal("10.00"),
            stock_quantity=5,
            status="draft",
            show_price=False,
        )
        self.active_image = ProductImage.objects.create(
            product=self.active_product,
            image="products/public.jpg",
            alt_text="Public image",
        )
        self.draft_image = ProductImage.objects.create(
            product=self.draft_product,
            image="products/draft.jpg",
            alt_text="Draft image",
        )

    def test_public_product_image_list_hides_draft_product_images(self):
        response = self.client.get(reverse("product-image-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = {item["id"] for item in response.data}
        self.assertIn(self.active_image.id, image_ids)
        self.assertNotIn(self.draft_image.id, image_ids)

    def test_staff_product_image_list_can_include_draft_product_images(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("product-image-list"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        image_ids = {item["id"] for item in response.data}
        self.assertIn(self.active_image.id, image_ids)
        self.assertIn(self.draft_image.id, image_ids)
