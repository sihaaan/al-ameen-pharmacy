import re
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.core import mail
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Cart, CartItem, Order, OrderItem, Product
from .throttles import PasswordResetRateThrottle


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
