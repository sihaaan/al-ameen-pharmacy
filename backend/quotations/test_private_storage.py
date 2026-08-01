import hashlib
import tempfile
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.files.storage import storages
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .models import (
    Company,
    HistoricalPriceImport,
    ProformaInvoice,
    Quotation,
    QuotationLine,
    QuotationLPO,
    QuotationOutcomePOImport,
)
from .private_storage import (
    PRIVATE_EVIDENCE_REF_PREFIX,
    PRIVATE_EVIDENCE_STORAGE_ALIAS,
    PrivateEvidenceIntegrityError,
    PrivateEvidenceStorageUnavailable,
    get_private_evidence_storage,
    read_private_ref,
    resolve_private_ref,
    store_import_source,
)
from .views import fitz


User = get_user_model()

MEMORY_EVIDENCE_STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
    },
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
    },
    PRIVATE_EVIDENCE_STORAGE_ALIAS: {
        "BACKEND": "django.core.files.storage.memory.InMemoryStorage",
    },
}


@override_settings(STORAGES=MEMORY_EVIDENCE_STORAGES)
class PrivateEvidenceStorageTests(SimpleTestCase):
    def test_new_storage_round_trip_is_versioned_opaque_and_idempotent(self):
        data = b"confidential customer evidence"
        digest = hashlib.sha256(data).hexdigest()

        with patch.object(
            get_private_evidence_storage(),
            "url",
            side_effect=AssertionError("Private evidence must never request a URL."),
        ):
            first_ref = store_import_source(
                data,
                filename="Very Sensitive Customer Name RFQ.PDF",
                sha256=digest,
            )
            second_ref = store_import_source(
                data,
                filename="../../顧客-" + ("x" * 600) + ".PDF",
                sha256=digest,
            )
            restored = read_private_ref(first_ref)

        self.assertEqual(first_ref, second_ref)
        self.assertTrue(first_ref.startswith(PRIVATE_EVIDENCE_REF_PREFIX))
        self.assertIn(digest, first_ref)
        self.assertTrue(first_ref.endswith(".pdf"))
        self.assertNotIn("Sensitive", first_ref)
        self.assertNotIn("Customer", first_ref)
        self.assertEqual(restored, data)

    def test_storage_disabled_performs_no_backend_lookup_or_write(self):
        data = b"disabled evidence"
        with override_settings(QUOTATION_IMPORT_STORE_SOURCE_FILES=False):
            with patch(
                "quotations.private_storage.get_private_evidence_storage"
            ) as get_storage:
                source_ref = store_import_source(
                    data,
                    filename="disabled.pdf",
                    sha256=hashlib.sha256(data).hexdigest(),
                )

        self.assertEqual(source_ref, "")
        get_storage.assert_not_called()

    def test_supplied_digest_must_match_bytes_before_backend_access(self):
        with patch(
            "quotations.private_storage.get_private_evidence_storage"
        ) as get_storage:
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(
                    b"actual bytes",
                    filename="source.pdf",
                    sha256="0" * 64,
                )

        get_storage.assert_not_called()

    def test_backend_write_failure_is_fail_closed(self):
        data = b"write failure"
        failed_storage = Mock()
        failed_storage.exists.return_value = False
        failed_storage.save.side_effect = OSError("provider unavailable")

        with patch(
            "quotations.private_storage.get_private_evidence_storage",
            return_value=failed_storage,
        ):
            with self.assertRaises(PrivateEvidenceStorageUnavailable):
                store_import_source(
                    data,
                    filename="source.pdf",
                    sha256=hashlib.sha256(data).hexdigest(),
                )

    def test_existing_corrupt_versioned_object_fails_closed(self):
        data = b"expected bytes"
        digest = hashlib.sha256(data).hexdigest()
        source_ref = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}.pdf"
        storage = get_private_evidence_storage()
        self.assertEqual(storage.save(source_ref, ContentFile(b"corrupt bytes")), source_ref)

        with patch("quotations.private_storage.timezone.now") as now:
            now.return_value.strftime.return_value = "2026/08/01"
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(data, filename="source.pdf", sha256=digest)
        with self.assertRaises(PrivateEvidenceIntegrityError):
            read_private_ref(source_ref)

    def test_legacy_local_reference_remains_readable_and_wins_over_backend_copy(self):
        legacy_ref = "inquiry_sources/2026/07/31/legacy.pdf"
        backend = get_private_evidence_storage()
        backend.save(legacy_ref, ContentFile(b"copied backend bytes"))

        with tempfile.TemporaryDirectory() as private_root:
            local_path = Path(private_root) / legacy_ref
            local_path.parent.mkdir(parents=True)
            local_path.write_bytes(b"original local bytes")
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                self.assertEqual(read_private_ref(legacy_ref), b"original local bytes")
                self.assertEqual(resolve_private_ref(legacy_ref), local_path.resolve())

    def test_legacy_reference_falls_back_to_copied_backend_object(self):
        legacy_ref = "inquiry_sources/2026/07/31/migrated.pdf"
        get_private_evidence_storage().save(
            legacy_ref,
            ContentFile(b"migrated legacy bytes"),
        )

        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                self.assertEqual(
                    read_private_ref(legacy_ref),
                    b"migrated legacy bytes",
                )

    def test_recorded_digest_protects_legacy_local_and_backend_reads(self):
        legacy_ref = "inquiry_sources/2026/07/31/legacy-integrity.pdf"
        data = b"legacy evidence"
        correct_digest = hashlib.sha256(data).hexdigest()
        wrong_digest = hashlib.sha256(b"different evidence").hexdigest()
        get_private_evidence_storage().save(legacy_ref, ContentFile(data))

        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                with self.assertRaises(PrivateEvidenceIntegrityError):
                    read_private_ref(legacy_ref, expected_sha256=wrong_digest)
                local_path = Path(private_root) / legacy_ref
                local_path.parent.mkdir(parents=True)
                local_path.write_bytes(b"corrupt local copy")
                self.assertEqual(
                    read_private_ref(legacy_ref, expected_sha256=correct_digest),
                    data,
                )
                with self.assertRaises(PrivateEvidenceIntegrityError):
                    read_private_ref(legacy_ref, expected_sha256=wrong_digest)

    def test_versioned_reference_uses_hash_verified_local_cutover_fallback(self):
        digest = hashlib.sha256(b"local-only bytes").hexdigest()
        source_ref = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}.pdf"

        with tempfile.TemporaryDirectory() as private_root:
            local_path = Path(private_root) / source_ref
            local_path.parent.mkdir(parents=True)
            local_path.write_bytes(b"local-only bytes")
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                self.assertEqual(read_private_ref(source_ref), b"local-only bytes")

                local_path.write_bytes(b"corrupt local bytes")
                with self.assertRaises(PrivateEvidenceIntegrityError):
                    read_private_ref(source_ref)

    @override_settings(QUOTATION_PRIVATE_EVIDENCE_MAX_BYTES=4)
    def test_oversized_active_objects_fail_before_parser_use(self):
        data = b"12345"
        digest = hashlib.sha256(data).hexdigest()
        versioned_ref = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}.pdf"
        legacy_ref = "inquiry_sources/2026/07/31/oversized.pdf"
        storage = get_private_evidence_storage()
        storage.save(versioned_ref, ContentFile(data))
        storage.save(legacy_ref, ContentFile(data))

        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                with self.assertRaises(PrivateEvidenceIntegrityError):
                    read_private_ref(versioned_ref)
                with self.assertRaises(PrivateEvidenceIntegrityError):
                    read_private_ref(legacy_ref)

        with patch(
            "quotations.private_storage.get_private_evidence_storage"
        ) as get_storage:
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(data, filename="large.pdf", sha256=digest)
        get_storage.assert_not_called()

    def test_backend_outage_is_not_misreported_as_missing(self):
        digest = hashlib.sha256(b"source").hexdigest()
        source_ref = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}.pdf"
        failed_storage = Mock()
        failed_storage.open.side_effect = OSError("provider unavailable")

        with patch(
            "quotations.private_storage.get_private_evidence_storage",
            return_value=failed_storage,
        ):
            with self.assertRaises(PrivateEvidenceStorageUnavailable):
                read_private_ref(source_ref)

    def test_unsafe_pseudo_and_unknown_version_refs_never_touch_backend(self):
        unsafe_refs = (
            "../secret.pdf",
            "/absolute/secret.pdf",
            "C:\\secret.pdf",
            "https://example.com/secret.pdf",
            "gmail:message-id",
            "inquiry_sources/v2/2026/08/01/" + "a" * 64 + ".pdf",
            "inquiry_sources/v1/2026/08/01/../../secret.pdf",
            "inquiry_sources/secret\x00.pdf",
            "inquiry_sources/legacy\nname.pdf",
            "other_prefix/secret.pdf",
        )

        with patch(
            "quotations.private_storage.get_private_evidence_storage"
        ) as get_storage:
            for source_ref in unsafe_refs:
                with self.subTest(source_ref=source_ref):
                    self.assertIsNone(read_private_ref(source_ref))
                    self.assertIsNone(resolve_private_ref(source_ref))

        get_storage.assert_not_called()

    def test_unsafe_backend_rename_is_never_deleted(self):
        data = b"renamed source"
        backend = Mock()
        backend.exists.return_value = False
        backend.save.return_value = "../unrelated-object"

        with patch(
            "quotations.private_storage.get_private_evidence_storage",
            return_value=backend,
        ):
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(
                    data,
                    filename="source.pdf",
                    sha256=hashlib.sha256(data).hexdigest(),
                )

        backend.delete.assert_not_called()

    def test_safe_backend_rename_fails_closed_without_deleting_provider_object(self):
        data = b"content-identical renamed source"
        digest = hashlib.sha256(data).hexdigest()
        canonical = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}.pdf"
        alternate = f"{PRIVATE_EVIDENCE_REF_PREFIX}2026/08/01/{digest}_1.pdf"
        backend = Mock()
        backend.exists.return_value = False
        backend.save.return_value = alternate

        def open_object(name, _mode):
            if name == alternate:
                return BytesIO(data)
            if name == canonical:
                raise FileNotFoundError(name)
            raise AssertionError(f"Unexpected storage key: {name}")

        backend.open.side_effect = open_object
        with patch(
            "quotations.private_storage.get_private_evidence_storage",
            return_value=backend,
        ), patch("quotations.private_storage.timezone.now") as now:
            now.return_value.strftime.return_value = "2026/08/01"
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(
                    data,
                    filename="source.pdf",
                    sha256=digest,
                )

        backend.delete.assert_not_called()

    def test_normal_save_is_read_back_and_corruption_is_rejected(self):
        data = b"expected persisted source"
        backend = Mock()
        backend.exists.return_value = False
        backend.save.side_effect = lambda name, _content: name
        backend.open.return_value = BytesIO(b"truncated")

        with patch(
            "quotations.private_storage.get_private_evidence_storage",
            return_value=backend,
        ):
            with self.assertRaises(PrivateEvidenceIntegrityError):
                store_import_source(
                    data,
                    filename="source.pdf",
                    sha256=hashlib.sha256(data).hexdigest(),
                )

    def test_default_local_backend_follows_overridden_private_roots(self):
        local_storages = {
            **MEMORY_EVIDENCE_STORAGES,
            PRIVATE_EVIDENCE_STORAGE_ALIAS: {
                "BACKEND": "quotations.private_storage.QuotationEvidenceFileSystemStorage",
            },
        }
        data = b"root-specific source"
        digest = hashlib.sha256(data).hexdigest()

        with override_settings(STORAGES=local_storages):
            with tempfile.TemporaryDirectory() as first_root:
                with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=first_root):
                    source_ref = store_import_source(
                        data,
                        filename="source.xlsx",
                        sha256=digest,
                    )
                    self.assertTrue((Path(first_root) / source_ref).is_file())
            with tempfile.TemporaryDirectory() as second_root:
                with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=second_root):
                    second_ref = store_import_source(
                        data,
                        filename="source.xlsx",
                        sha256=digest,
                    )
                    self.assertEqual(second_ref, source_ref)
                    self.assertTrue((Path(second_root) / second_ref).is_file())


@override_settings(STORAGES=MEMORY_EVIDENCE_STORAGES)
class PrivateEvidencePreviewTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="evidence_staff",
            password="pass",
            is_staff=True,
        )
        self.customer = User.objects.create_user(
            username="evidence_customer",
            password="pass",
        )

    def make_pdf(self):
        if fitz is None:
            self.skipTest("PyMuPDF is not installed.")
        document = fitz.open()
        page = document.new_page()
        page.insert_text((72, 72), "Private quotation evidence")
        data = document.tobytes()
        document.close()
        return data

    def create_historical_import(self):
        data = self.make_pdf()
        source_ref = store_import_source(
            data,
            filename="historical.pdf",
            sha256=hashlib.sha256(data).hexdigest(),
        )
        return HistoricalPriceImport.objects.create(
            source_type=HistoricalPriceImport.SOURCE_TYPE_PDF,
            source_filename="historical.pdf",
            source_mime_type="application/pdf",
            source_sha256=hashlib.sha256(data).hexdigest(),
            source_file_ref=source_ref,
            source_file_size=len(data),
            created_by=self.staff,
        )

    def test_historical_preview_is_staff_only_and_not_browser_cacheable(self):
        historical_import = self.create_historical_import()
        url = reverse(
            "quotation-historical-import-preview-page",
            args=[historical_import.id],
        )

        self.client.force_authenticate(self.customer)
        blocked = self.client.get(url)
        self.assertEqual(blocked.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(self.staff)
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        self.assertEqual(response["Pragma"], "no-cache")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")

    def test_historical_preview_distinguishes_storage_outage_from_missing(self):
        historical_import = self.create_historical_import()
        url = reverse(
            "quotation-historical-import-preview-page",
            args=[historical_import.id],
        )
        self.client.force_authenticate(self.staff)

        with patch(
            "quotations.views.read_private_ref",
            side_effect=PrivateEvidenceStorageUnavailable("provider unavailable"),
        ):
            unavailable = self.client.get(url)
        self.assertEqual(unavailable.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

        historical_import.source_file_ref = "inquiry_sources/legacy-missing.pdf"
        historical_import.save(update_fields=["source_file_ref"])
        missing = self.client.get(url)
        self.assertEqual(missing.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(STORAGES=MEMORY_EVIDENCE_STORAGES)
class PrivateEvidencePersistenceRouteTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="evidence_route_staff",
            password="pass",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Evidence Route Company")
        self.product = Product.objects.create(
            name="Bandage Pack",
            price=Decimal("1.00"),
            pack_size="box",
            status="draft",
        )
        self.client.force_authenticate(self.staff)

    def workbook_bytes(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["Item Description", "Qty", "Unit", "Unit Price"])
        sheet.append(["Bandage Pack", 2, "box", 10])
        buffer = BytesIO()
        workbook.save(buffer)
        return buffer.getvalue()

    def upload(self, data):
        return SimpleUploadedFile(
            "customer-lpo.xlsx",
            data,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def assert_persisted(self, instance, data):
        self.assertTrue(instance.source_file_ref.startswith(PRIVATE_EVIDENCE_REF_PREFIX))
        self.assertEqual(
            read_private_ref(
                instance.source_file_ref,
                expected_sha256=instance.source_sha256,
            ),
            data,
        )

    def test_file_outcome_lpo_and_proforma_routes_retain_readable_private_evidence(self):
        data = self.workbook_bytes()

        outcome_quote = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
            status=Quotation.STATUS_FINALIZED,
        )
        QuotationLine.objects.create(
            quotation=outcome_quote,
            product=self.product,
            item_name_snapshot=self.product.name,
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        outcome_response = self.client.post(
            reverse("quotation-parse-outcome-po", args=[outcome_quote.id]),
            {"file": self.upload(data), "use_ai": "false"},
            format="multipart",
        )
        self.assertEqual(outcome_response.status_code, status.HTTP_201_CREATED)
        self.assert_persisted(
            QuotationOutcomePOImport.objects.get(quotation=outcome_quote),
            data,
        )

        lpo_quote = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
            status=Quotation.STATUS_APPROVED,
        )
        QuotationLine.objects.create(
            quotation=lpo_quote,
            product=self.product,
            item_name_snapshot=self.product.name,
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        lpo_response = self.client.post(
            reverse("quotation-upload-lpo", args=[lpo_quote.id]),
            {"file": self.upload(data), "use_ai": "false"},
            format="multipart",
        )
        self.assertEqual(lpo_response.status_code, status.HTTP_201_CREATED)
        self.assert_persisted(QuotationLPO.objects.get(quotation=lpo_quote), data)

        proforma = ProformaInvoice.objects.create(
            company=self.company,
            created_by=self.staff,
        )
        proforma_response = self.client.post(
            reverse("quotation-standalone-proforma-upload-lpo", args=[proforma.id]),
            {"file": self.upload(data), "use_ai": "false"},
            format="multipart",
        )
        self.assertEqual(proforma_response.status_code, status.HTTP_201_CREATED)
        proforma.refresh_from_db()
        self.assert_persisted(proforma, data)
