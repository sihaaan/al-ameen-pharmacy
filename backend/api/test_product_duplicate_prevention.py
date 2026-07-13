import tempfile
from io import BytesIO

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Brand, Category, Product, ProductImage


def make_png_upload(name="product.png", color=(18, 120, 95, 255)):
    buffer = BytesIO()
    PILImage.new("RGBA", (12, 12), color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class StaffProductDuplicatePreventionTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="product-create-staff",
            password="pass",
            is_staff=True,
        )
        self.client.force_authenticate(self.staff)
        self.url = reverse("product-list")

    def test_similar_product_warns_then_explicit_confirmation_creates(self):
        existing = Product.objects.create(name="Digital Thermometer", price="12.00", status="active")
        payload = {"name": "Digital Thermometre", "price": "13.50", "status": "draft"}

        warning = self.client.post(self.url, payload, format="json")

        self.assertEqual(warning.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(warning.data["requires_confirmation"])
        self.assertFalse(warning.data["creation_blocked"])
        self.assertEqual(warning.data["match_method"], "fuzzy_candidates")
        self.assertEqual(warning.data["candidates"][0]["product_id"], existing.id)
        self.assertFalse(Product.objects.filter(name="Digital Thermometre").exists())

        confirmed = self.client.post(
            self.url,
            {**payload, "confirm_create": True},
            format="json",
        )

        self.assertEqual(confirmed.status_code, status.HTTP_201_CREATED)
        self.assertTrue(confirmed.data["created"])
        self.assertTrue(confirmed.data["override_used"])
        self.assertTrue(Product.objects.filter(name="Digital Thermometre").exists())

    def test_exact_canonical_identity_is_reused_without_overwriting_fields(self):
        existing = Product.objects.create(
            name="Alcohol Detector Mouth-Piece",
            price="4.00",
            stock_quantity=7,
            status="active",
            show_price=True,
        )

        response = self.client.post(
            self.url,
            {
                "name": "Alcohol Detector Mouth Piece",
                "price": "99.00",
                "stock_quantity": 100,
                "status": "draft",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["created"])
        self.assertTrue(response.data["reused"])
        self.assertEqual(response.data["product_id"], existing.id)
        self.assertEqual(Product.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(str(existing.price), "4.00")
        self.assertEqual(existing.stock_quantity, 7)
        self.assertEqual(existing.status, "active")

    def test_conflicting_identifier_is_blocked_even_with_confirmation(self):
        Product.objects.create(name="First SKU Owner", sku="DUP-SKU", price="1.00", status="draft")
        Product.objects.create(name="Second SKU Owner", sku="DUP-SKU", price="1.00", status="draft")
        before = Product.objects.count()

        response = self.client.post(
            self.url,
            {
                "name": "Attempted Third SKU Owner",
                "price": "2.00",
                "sku": "DUP-SKU",
                "confirm_create": True,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(response.data["requires_confirmation"])
        self.assertTrue(response.data["creation_blocked"])
        self.assertEqual(response.data["match_method"], "identifier_conflict")
        self.assertEqual(len(response.data["candidates"]), 2)
        self.assertEqual(Product.objects.count(), before)

    def test_confirmed_multipart_create_preserves_image_and_all_catalog_fields(self):
        Product.objects.create(name="Advanced Wound Dressing", price="1.00", status="draft")
        brand = Brand.objects.create(name="Clinical Brand")
        category = Category.objects.create(name="Clinical Supplies")
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        payload = {
            "name": "Advanced Wound Dresing",
            "brand": brand.id,
            "category": category.id,
            "short_description": "Short clinical description",
            "detailed_description": "Detailed clinical description and usage.",
            "price": "25.75",
            "stock_quantity": 42,
            "sku": "AWD-NEW-01",
            "barcode": "6291234567890",
            "requires_prescription": True,
            "dosage": "10 cm x 10 cm",
            "pack_size": "box of 20 pieces",
            "active_ingredient": "Hydrofiber",
            "status": "active",
            "requires_manual_review": False,
            "is_featured": True,
            "show_price": True,
            "meta_title": "Advanced wound dressing",
            "meta_description": "Advanced wound dressing for clinical care.",
            "confirm_create": True,
            "image": make_png_upload(),
        }

        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                response = self.client.post(self.url, payload, format="multipart")

                self.assertEqual(response.status_code, status.HTTP_201_CREATED)
                product = Product.objects.get(name="Advanced Wound Dresing")
                image = ProductImage.objects.get(product=product)
                self.assertTrue(image.is_primary)
                self.assertTrue(image.image.name.endswith("product.png"))
                self.assertEqual(len(response.data["images"]), 1)
                self.assertTrue(response.data["primary_image_url"])

        product.refresh_from_db()
        self.assertEqual(product.brand, brand)
        self.assertEqual(product.category, category)
        self.assertEqual(product.short_description, payload["short_description"])
        self.assertEqual(product.detailed_description, payload["detailed_description"])
        self.assertEqual(str(product.price), "25.75")
        self.assertEqual(product.stock_quantity, 42)
        self.assertEqual(product.sku, "AWD-NEW-01")
        self.assertEqual(product.barcode, "6291234567890")
        self.assertTrue(product.requires_prescription)
        self.assertEqual(product.dosage, "10 cm x 10 cm")
        self.assertEqual(product.pack_size, "box of 20 pieces")
        self.assertEqual(product.active_ingredient, "Hydrofiber")
        self.assertEqual(product.status, "active")
        self.assertFalse(product.requires_manual_review)
        self.assertTrue(product.is_featured)
        self.assertTrue(product.show_price)
        self.assertEqual(product.meta_title, payload["meta_title"])
        self.assertEqual(product.meta_description, payload["meta_description"])
        self.assertFalse(response.data["requires_manual_review"])
