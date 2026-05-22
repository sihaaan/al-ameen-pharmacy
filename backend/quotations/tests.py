from decimal import Decimal
from io import BytesIO
import tempfile

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse
from openpyxl import Workbook
from pypdf import PdfReader, PdfWriter
from PIL import Image as PILImage
from reportlab.pdfgen import canvas
from rest_framework import status
from rest_framework.test import APITestCase

from .models import Company, CompanyContact, CompanyPriceHistory, Inquiry, InquiryLine, Quotation, QuotationLine, QuotationSettings, QuoteItem


def make_png_bytes(color=(15, 118, 110, 255)):
    buffer = BytesIO()
    PILImage.new("RGBA", (12, 12), color).save(buffer, format="PNG")
    return buffer.getvalue()


def make_png_upload(name="image.png", color=(15, 118, 110, 255)):
    return SimpleUploadedFile(name, make_png_bytes(color), content_type="image/png")


def extract_pdf_text(content):
    reader = PdfReader(BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


class QuotationPermissionTests(APITestCase):
    list_route_names = [
        "quotation-company-list",
        "quotation-contact-list",
        "quotation-item-list",
        "quotation-inquiry-list",
        "quotation-inquiry-line-list",
        "quotation-list",
        "quotation-line-list",
        "quotation-price-history-list",
        "quotation-audit-log-list",
    ]

    def setUp(self):
        self.company = Company.objects.create(name="Blocked Test Company")
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="customer", password="pass")

    def test_anonymous_users_are_blocked_from_all_list_endpoints(self):
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

    def test_non_staff_users_are_blocked_from_all_list_endpoints(self):
        self.client.force_authenticate(self.customer)
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_staff_users_are_allowed_on_all_list_endpoints(self):
        self.client.force_authenticate(self.staff)
        for route_name in self.list_route_names:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                self.assertEqual(response.status_code, status.HTTP_200_OK)


class QuotationWorkflowTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="customer", password="pass")
        self.company = Company.objects.create(name="Workflow Company")
        self.quote_item = QuoteItem.objects.create(name="Bandage Pack", unit="box")
        self.client.force_authenticate(self.staff)

    def create_quote(self):
        return Quotation.objects.create(company=self.company, created_by=self.staff)

    def create_valid_line(self, quotation):
        return QuotationLine.objects.create(
            quotation=quotation,
            quote_item=self.quote_item,
            item_name_snapshot="Bandage Pack",
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )

    def test_cannot_finalize_invalid_quote(self):
        quotation = self.create_quote()
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Unknown Item",
            quantity=Decimal("1.000"),
            unit="box",
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertEqual(CompanyPriceHistory.objects.count(), 0)

    def test_finalized_quote_appends_price_history_once(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)

        response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        self.assertEqual(CompanyPriceHistory.objects.count(), 1)
        history = CompanyPriceHistory.objects.get()
        self.assertEqual(history.company, self.company)
        self.assertEqual(history.quote_item, self.quote_item)
        self.assertEqual(history.unit_price, Decimal("10.00"))

        second_response = self.client.post(reverse("quotation-finalize", args=[quotation.id]))
        self.assertEqual(second_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CompanyPriceHistory.objects.count(), 1)

    def test_finalized_quote_cannot_be_edited(self):
        quotation = self.create_quote()
        line = self.create_valid_line(quotation)
        self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        quote_response = self.client.patch(
            reverse("quotation-detail", args=[quotation.id]),
            {"notes": "Should fail"},
            format="json",
        )
        line_response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {"unit_price": "12.00"},
            format="json",
        )

        self.assertEqual(quote_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(line_response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_revision_creates_new_draft(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)
        self.client.post(reverse("quotation-finalize", args=[quotation.id]))

        response = self.client.post(reverse("quotation-revise", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_REVISED)
        revision = Quotation.objects.get(id=response.data["id"])
        self.assertEqual(revision.status, Quotation.STATUS_DRAFT)
        self.assertEqual(revision.version, 2)
        self.assertEqual(revision.parent, quotation)
        self.assertEqual(revision.lines.count(), 1)

    def test_create_quote_from_inquiry_is_idempotent(self):
        inquiry = Inquiry.objects.create(company=self.company, subject="Repeat inquiry", created_by=self.staff)
        InquiryLine.objects.create(
            inquiry=inquiry,
            raw_name="Bandage Pack",
            matched_quote_item=self.quote_item,
            quantity=Decimal("2.000"),
            unit="box",
            match_status=InquiryLine.MATCH_CONFIRMED,
        )

        first_response = self.client.post(reverse("quotation-inquiry-create-quote", args=[inquiry.id]))
        second_response = self.client.post(reverse("quotation-inquiry-create-quote", args=[inquiry.id]))

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(first_response.data["id"], second_response.data["id"])
        self.assertEqual(Quotation.objects.filter(inquiry=inquiry).count(), 1)

    def test_pdf_endpoint_is_staff_only(self):
        quotation = self.create_quote()
        self.create_valid_line(quotation)

        self.client.force_authenticate(self.customer)
        blocked = self.client.get(reverse("quotation-pdf", args=[quotation.id]))
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        allowed = self.client.get(reverse("quotation-pdf", args=[quotation.id]))
        self.assertEqual(allowed.status_code, status.HTTP_200_OK)
        self.assertEqual(allowed["Content-Type"], "application/pdf")


class InquiryImportTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="import_staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="import_customer", password="pass")
        self.company = Company.objects.create(name="Import Company")
        self.contact = CompanyContact.objects.create(company=self.company, name="Buyer")
        self.client.force_authenticate(self.staff)

    def make_excel_upload(self, name="inquiry.xlsx"):
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "LPO"
        sheet.append(["Item", "Qty", "Unit"])
        sheet.append(["Panadol 500mg", 10, "box"])
        sheet.append(["Gloves medium", 5, "packs"])
        buffer = BytesIO()
        workbook.save(buffer)
        return SimpleUploadedFile(
            name,
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def make_pdf_upload(self, text=True, encrypted=False, name="inquiry.pdf"):
        buffer = BytesIO()
        if text:
            pdf = canvas.Canvas(buffer)
            pdf.drawString(72, 740, "Panadol 500mg - 10 boxes")
            pdf.drawString(72, 720, "Gloves medium 5 packs")
            pdf.save()
            data = buffer.getvalue()
        else:
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=300)
            if encrypted:
                writer.encrypt("secret")
            writer.write(buffer)
            data = buffer.getvalue()
        return SimpleUploadedFile(name, data, content_type="application/pdf")

    def test_import_actions_are_staff_only(self):
        actions = [
            ("post", reverse("quotation-inquiry-parse-text"), {"raw_text": "Panadol 500mg - 10 boxes"}, "json"),
            ("post", reverse("quotation-inquiry-parse-file"), {"file": self.make_excel_upload()}, "multipart"),
            (
                "post",
                reverse("quotation-inquiry-create-imported"),
                {
                    "company": self.company.id,
                    "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
                    "lines": [{"raw_name": "Panadol 500mg", "raw_line": "Panadol 500mg - 10 boxes"}],
                },
                "json",
            ),
        ]

        self.client.force_authenticate(None)
        for method, url, payload, request_format in actions:
            with self.subTest(url=url, user="anonymous"):
                response = getattr(self.client, method)(url, payload, format=request_format)
                self.assertIn(response.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        self.client.force_authenticate(self.customer)
        for method, url, payload, request_format in actions:
            with self.subTest(url=url, user="customer"):
                if url.endswith("parse_file/"):
                    payload = {"file": self.make_excel_upload()}
                response = getattr(self.client, method)(url, payload, format=request_format)
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        staff_actions = [
            ("post", reverse("quotation-inquiry-parse-text"), {"raw_text": "Panadol 500mg - 10 boxes"}, "json", status.HTTP_200_OK),
            ("post", reverse("quotation-inquiry-parse-file"), {"file": self.make_excel_upload()}, "multipart", status.HTTP_200_OK),
            (
                "post",
                reverse("quotation-inquiry-create-imported"),
                {
                    "company": self.company.id,
                    "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
                    "lines": [{"raw_name": "Panadol 500mg", "raw_line": "Panadol 500mg - 10 boxes"}],
                },
                "json",
                status.HTTP_201_CREATED,
            ),
        ]
        for method, url, payload, request_format, expected_status in staff_actions:
            with self.subTest(url=url, user="staff"):
                response = getattr(self.client, method)(url, payload, format=request_format)
                self.assertEqual(response.status_code, expected_status)

    def test_parse_text_examples(self):
        response = self.client.post(
            reverse("quotation-inquiry-parse-text"),
            {
                "raw_text": "\n".join(
                    [
                        "Panadol 500mg - 10 boxes",
                        "Panadol 500mg x 10",
                        "10 boxes Panadol 500mg",
                        "Gloves medium 5 packs",
                        "1. Panadol 500mg - 10 box",
                    ]
                )
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["lines"]), 5)
        self.assertEqual(response.data["lines"][0]["raw_name"], "Panadol 500mg")
        self.assertEqual(response.data["lines"][0]["quantity"], "10")
        self.assertEqual(response.data["lines"][0]["unit"].lower(), "boxes")

    def test_invalid_extension_rejected(self):
        upload = SimpleUploadedFile("inquiry.txt", b"Panadol 500mg - 10 boxes", content_type="text/plain")
        response = self.client.post(reverse("quotation-inquiry-parse-file"), {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Unsupported file type", str(response.data))

    @override_settings(QUOTATION_IMPORT_MAX_UPLOAD_BYTES=12)
    def test_file_size_limit_rejected(self):
        upload = SimpleUploadedFile("big.xlsx", b"PK" + b"x" * 100, content_type="application/octet-stream")
        response = self.client.post(reverse("quotation-inquiry-parse-file"), {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("too large", str(response.data))

    def test_excel_parse_happy_path(self):
        response = self.client.post(
            reverse("quotation-inquiry-parse-file"),
            {"file": self.make_excel_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source_type"], Inquiry.SOURCE_TYPE_EXCEL)
        self.assertEqual(response.data["parse_method"], "openpyxl_v1")
        self.assertEqual(len(response.data["lines"]), 2)
        self.assertEqual(response.data["lines"][0]["source_sheet"], "LPO")
        self.assertEqual(response.data["lines"][0]["raw_name"], "Panadol 500mg")

    def test_pdf_parse_happy_path(self):
        response = self.client.post(
            reverse("quotation-inquiry-parse-file"),
            {"file": self.make_pdf_upload()},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source_type"], Inquiry.SOURCE_TYPE_PDF)
        self.assertEqual(response.data["parse_method"], "pypdf_pdfplumber_v1")
        self.assertGreaterEqual(len(response.data["lines"]), 2)
        self.assertIn("Panadol 500mg", response.data["lines"][0]["raw_name"])

    def test_pdf_no_selectable_text_returns_warning(self):
        response = self.client.post(
            reverse("quotation-inquiry-parse-file"),
            {"file": self.make_pdf_upload(text=False)},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lines"], [])
        self.assertIn("No selectable text detected", response.data["warnings"][0])

    def test_encrypted_pdf_rejected(self):
        response = self.client.post(
            reverse("quotation-inquiry-parse-file"),
            {"file": self.make_pdf_upload(text=False, encrypted=True)},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Encrypted PDF files are not supported", str(response.data))

    def test_create_imported_creates_inquiry_and_lines_atomically(self):
        payload = {
            "company": self.company.id,
            "contact": self.contact.id,
            "subject": "Imported LPO",
            "original_text": "Panadol 500mg - 10 boxes",
            "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
            "source_filename": "",
            "source_mime_type": "text/plain",
            "source_sha256": "a" * 64,
            "parse_method": "deterministic_text_v1",
            "parse_meta": {"warnings": []},
            "lines": [
                {
                    "raw_name": "Panadol 500mg",
                    "raw_line": "Panadol 500mg - 10 boxes",
                    "quantity": "10.000",
                    "unit": "boxes",
                    "parse_status": InquiryLine.PARSE_PARSED,
                    "parse_confidence": 0.9,
                }
            ],
        }

        response = self.client.post(reverse("quotation-inquiry-create-imported"), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        inquiry = Inquiry.objects.get(id=response.data["id"])
        self.assertEqual(inquiry.source, Inquiry.SOURCE_IMPORTED)
        self.assertEqual(inquiry.source_type, Inquiry.SOURCE_TYPE_PASTED_TEXT)
        self.assertEqual(inquiry.lines.count(), 1)


class QuotationSettingsTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="settings_staff", password="pass", is_staff=True)
        self.customer = User.objects.create_user(username="settings_customer", password="pass")
        self.company = Company.objects.create(name="Settings Company")
        self.quote_item = QuoteItem.objects.create(name="Settings Item", unit="box")

    def create_valid_quote(self):
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        QuotationLine.objects.create(
            quotation=quotation,
            quote_item=self.quote_item,
            item_name_snapshot="Settings Item",
            quantity=Decimal("1.000"),
            unit="box",
            unit_price=Decimal("25.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        return quotation

    def test_settings_permissions(self):
        url = reverse("quotation-settings")

        anonymous = self.client.get(url)
        self.assertIn(anonymous.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])

        self.client.force_authenticate(self.customer)
        non_staff = self.client.get(url)
        self.assertEqual(non_staff.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        staff = self.client.get(url)
        self.assertEqual(staff.status_code, status.HTTP_200_OK)

    def test_settings_defaults_returned_if_missing(self):
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("quotation-settings"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["company_name"], "Al Ameen Pharmacy")
        self.assertEqual(QuotationSettings.objects.count(), 1)

    def test_settings_update_works(self):
        self.client.force_authenticate(self.staff)

        response = self.client.patch(
            reverse("quotation-settings"),
            {
                "company_name": "Custom Pharmacy",
                "trn": "123456789",
                "license_number": "LIC-42",
                "validity_days": 30,
                "primary_color": "#123456",
                "logo_layout": QuotationSettings.LOGO_LAYOUT_LOGO_TEXT,
                "show_stamp_area": False,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        settings_obj = QuotationSettings.get_solo()
        self.assertEqual(settings_obj.company_name, "Custom Pharmacy")
        self.assertEqual(settings_obj.validity_days, 30)
        self.assertEqual(settings_obj.logo_layout, QuotationSettings.LOGO_LAYOUT_LOGO_TEXT)
        self.assertEqual(settings_obj.updated_by, self.staff)

    def test_invalid_color_values_are_rejected(self):
        self.client.force_authenticate(self.staff)

        response = self.client.patch(reverse("quotation-settings"), {"primary_color": "teal"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("primary_color", response.data)

    def test_logo_upload_rejects_invalid_file_type(self):
        self.client.force_authenticate(self.staff)
        upload = SimpleUploadedFile("logo.txt", b"not-an-image", content_type="text/plain")

        response = self.client.patch(reverse("quotation-settings"), {"logo": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("valid image", str(response.data))

    def test_signature_and_stamp_uploads_work(self):
        self.client.force_authenticate(self.staff)
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                response = self.client.patch(
                    reverse("quotation-settings"),
                    {
                        "signature_image": make_png_upload("signature.png"),
                        "stamp_image": make_png_upload("stamp.png", color=(212, 160, 65, 255)),
                    },
                    format="multipart",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertTrue(response.data["signature_image_url"])
                self.assertTrue(response.data["stamp_image_url"])
                settings_obj = QuotationSettings.get_solo()
                self.assertTrue(settings_obj.signature_image.name.startswith("quotations/signatures/"))
                self.assertTrue(settings_obj.stamp_image.name.startswith("quotations/stamps/"))

    def test_staff_can_clear_logo_signature_and_stamp(self):
        self.client.force_authenticate(self.staff)
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                self.client.patch(
                    reverse("quotation-settings"),
                    {
                        "logo": make_png_upload("logo.png"),
                        "signature_image": make_png_upload("signature.png"),
                        "stamp_image": make_png_upload("stamp.png"),
                    },
                    format="multipart",
                )
                settings_obj = QuotationSettings.get_solo()
                self.assertTrue(settings_obj.logo)
                self.assertTrue(settings_obj.signature_image)
                self.assertTrue(settings_obj.stamp_image)

                response = self.client.patch(
                    reverse("quotation-settings"),
                    {"clear_logo": True, "clear_signature_image": True, "clear_stamp_image": True},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                settings_obj.refresh_from_db()
                self.assertFalse(settings_obj.logo)
                self.assertFalse(settings_obj.signature_image)
                self.assertFalse(settings_obj.stamp_image)

    def test_non_staff_and_anonymous_cannot_clear_images(self):
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                settings_obj = QuotationSettings.get_solo()
                settings_obj.logo.save("logo.png", ContentFile(make_png_bytes()), save=True)

                anonymous = self.client.patch(reverse("quotation-settings"), {"clear_logo": True}, format="json")
                self.assertIn(anonymous.status_code, [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN])
                settings_obj.refresh_from_db()
                self.assertTrue(settings_obj.logo)

                self.client.force_authenticate(self.customer)
                non_staff = self.client.patch(reverse("quotation-settings"), {"clear_logo": True}, format="json")
                self.assertEqual(non_staff.status_code, status.HTTP_403_FORBIDDEN)
                settings_obj.refresh_from_db()
                self.assertTrue(settings_obj.logo)

    def test_stamp_upload_rejects_invalid_file_type(self):
        self.client.force_authenticate(self.staff)
        upload = SimpleUploadedFile("stamp.txt", b"not-an-image", content_type="text/plain")

        response = self.client.patch(reverse("quotation-settings"), {"stamp_image": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("valid image", str(response.data))

    def test_pdf_generation_uses_settings_and_still_works(self):
        QuotationSettings.objects.create(
            company_name="PDF Settings Pharmacy",
            default_terms="Settings terms apply.",
            payment_terms="Net 15.",
            prepared_by_default="Quotation Team",
            footer_note="Thank you for your business.",
        )
        quotation = self.create_valid_quote()
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("quotation-pdf", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_generation_works_with_uploaded_logo_and_stamp_images(self):
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                settings_obj = QuotationSettings.get_solo()
                settings_obj.company_name = "Image Settings Pharmacy"
                settings_obj.logo.save("logo.png", ContentFile(make_png_bytes()), save=False)
                settings_obj.signature_image.save("signature.png", ContentFile(make_png_bytes()), save=False)
                settings_obj.stamp_image.save("stamp.png", ContentFile(make_png_bytes(color=(212, 160, 65, 255))), save=False)
                settings_obj.save()
                quotation = self.create_valid_quote()
                self.client.force_authenticate(self.staff)

                response = self.client.get(reverse("quotation-pdf", args=[quotation.id]))

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response["Content-Type"], "application/pdf")
                self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_full_logo_only_does_not_repeat_company_name_text(self):
        storage_settings = {
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
        }
        with tempfile.TemporaryDirectory() as media_root:
            with override_settings(MEDIA_ROOT=media_root, STORAGES=storage_settings):
                settings_obj = QuotationSettings.get_solo()
                settings_obj.company_name = "Full Lockup Pharmacy"
                settings_obj.logo_layout = QuotationSettings.LOGO_LAYOUT_FULL
                settings_obj.logo.save("logo.png", ContentFile(make_png_bytes()), save=False)
                settings_obj.save()
                quotation = self.create_valid_quote()
                self.client.force_authenticate(self.staff)

                response = self.client.get(reverse("quotation-pdf", args=[quotation.id]))

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertNotIn("Full Lockup Pharmacy", extract_pdf_text(response.content))

    def test_pdf_no_logo_uses_company_name_text(self):
        settings_obj = QuotationSettings.get_solo()
        settings_obj.company_name = "No Logo Pharmacy"
        settings_obj.logo_layout = QuotationSettings.LOGO_LAYOUT_NONE
        settings_obj.save()
        quotation = self.create_valid_quote()
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("quotation-pdf", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("No Logo Pharmacy", extract_pdf_text(response.content))

    def test_pdf_missing_signature_and_stamp_use_placeholders(self):
        settings_obj = QuotationSettings.get_solo()
        settings_obj.logo_layout = QuotationSettings.LOGO_LAYOUT_NONE
        settings_obj.signature_label = "Signature"
        settings_obj.stamp_label = "Stamp"
        settings_obj.show_signature_area = True
        settings_obj.show_stamp_area = True
        settings_obj.save()
        quotation = self.create_valid_quote()
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("quotation-pdf", args=[quotation.id]))
        text = " ".join(extract_pdf_text(response.content).split())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("Authorized Signature", text)
        self.assertIn("Company Stamp", text)

    def test_manual_inquiry_flow_still_works(self):
        self.client.force_authenticate(self.staff)
        response = self.client.post(
            reverse("quotation-inquiry-list"),
            {
                "company": self.company.id,
                "subject": "Manual inquiry still works",
                "lines": [{"raw_name": "Manual Item", "quantity": "1.000", "unit": "box"}],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        inquiry = Inquiry.objects.get(id=response.data["id"])
        self.assertEqual(inquiry.source, Inquiry.SOURCE_MANUAL)
        self.assertEqual(inquiry.source_type, Inquiry.SOURCE_TYPE_MANUAL)
        self.assertEqual(inquiry.lines.count(), 1)
