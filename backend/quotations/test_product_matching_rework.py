from decimal import Decimal
from datetime import date
from queue import Queue
from threading import Barrier, Event, Thread
from types import SimpleNamespace
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import close_old_connections, connection
from django.db.backends.postgresql.base import DatabaseWrapper
from django.test import SimpleTestCase, TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from api.models import Product

from .matching import (
    apply_match_to_preview_line,
    create_or_reuse_product,
    create_product_alias,
    learn_confirmed_product_alias,
    suggest_product_for_text,
)
from .models import (
    Company,
    CompanyPriceHistory,
    HistoricalImportAISuggestion,
    HistoricalImportBatch,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    Inquiry,
    InquiryLine,
    ProductAlias,
    Quotation,
    QuotationAuditLog,
    QuotationLine,
)
from .services import (
    _quotation_lines_for_update,
    _quotations_for_update,
    create_imported_inquiry,
    learn_confirmed_quotation_line_alias,
)
from .serializers import InquiryLineSerializer, QuotationLineSerializer
from . import ai_learning
from .views import HistoricalImportAISuggestionViewSet, InquiryLineViewSet, QuotationLineViewSet


class QuotationLineLockScopeTests(SimpleTestCase):
    def postgresql_connection(self):
        connection = DatabaseWrapper(
            {
                "ENGINE": "django.db.backends.postgresql",
                "NAME": "compile_only",
                "USER": "",
                "PASSWORD": "",
                "HOST": "",
                "PORT": "",
                "OPTIONS": {},
                "TIME_ZONE": None,
                "CONN_HEALTH_CHECKS": False,
                "CONN_MAX_AGE": 0,
                "AUTOCOMMIT": False,
                "ATOMIC_REQUESTS": False,
            },
            alias="compile_only",
        )
        connection.get_autocommit = lambda: False
        return connection

    def test_postgresql_locks_only_the_line_when_nullable_relations_are_joined(self):
        connection = self.postgresql_connection()
        queryset = (
            _quotation_lines_for_update()
            .select_related("quotation__company", "inquiry_line", "product")
            .filter(pk=1)
        )

        sql, _ = queryset.query.get_compiler(connection=connection).as_sql()

        self.assertIn('LEFT OUTER JOIN "quotations_inquiryline"', sql)
        self.assertIn('LEFT OUTER JOIN "api_product"', sql)
        self.assertTrue(sql.endswith('FOR UPDATE OF "quotations_quotationline"'))

    def test_postgresql_quotation_lock_does_not_lock_joined_company(self):
        connection = self.postgresql_connection()
        queryset = _quotations_for_update().select_related("company").filter(pk=1)

        sql, _ = queryset.query.get_compiler(connection=connection).as_sql()

        self.assertIn('INNER JOIN "quotations_company"', sql)
        self.assertTrue(sql.endswith('FOR UPDATE OF "quotations_quotation"'))

    def test_postgresql_ai_suggestion_lock_ignores_nullable_joined_targets(self):
        connection = self.postgresql_connection()
        queryset = (
            HistoricalImportAISuggestion.objects.select_for_update(of=("self",))
            .select_related("historical_import__company", "batch", "line", "suggested_company", "suggested_product")
            .filter(pk=1)
        )

        sql, _ = queryset.query.get_compiler(connection=connection).as_sql()

        self.assertIn('LEFT OUTER JOIN "api_product"', sql)
        self.assertTrue(sql.endswith('FOR UPDATE OF "quotations_historicalimportaisuggestion"'))

    def test_postgresql_historical_import_lock_ignores_nullable_company(self):
        connection = self.postgresql_connection()
        queryset = (
            HistoricalPriceImport.objects.select_for_update(of=("self",))
            .select_related("company")
            .filter(pk=1)
        )

        sql, _ = queryset.query.get_compiler(connection=connection).as_sql()

        self.assertIn('LEFT OUTER JOIN "quotations_company"', sql)
        self.assertTrue(sql.endswith('FOR UPDATE OF "quotations_historicalpriceimport"'))

    def test_postgresql_historical_line_lock_ignores_nullable_company(self):
        connection = self.postgresql_connection()
        queryset = (
            HistoricalPriceImportLine.objects.select_for_update(of=("self",))
            .select_related("historical_import__company")
            .filter(pk=1)
        )

        sql, _ = queryset.query.get_compiler(connection=connection).as_sql()

        self.assertIn('LEFT OUTER JOIN "quotations_company"', sql)
        self.assertTrue(sql.endswith('FOR UPDATE OF "quotations_historicalpriceimportline"'))


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row-lock semantics are required.")
class ProductAliasConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_equivalent_alias_writes_are_serialized_without_deadlock(self):
        company = Company.objects.create(name="Concurrent Alias Customer")
        first_product = Product.objects.create(name="Concurrent Product A", price=Decimal("1.00"), status="draft")
        second_product = Product.objects.create(name="Concurrent Product B", price=Decimal("1.00"), status="draft")
        barrier = Barrier(2)
        results = Queue()

        def learn_alias(source_text, product_id):
            close_old_connections()
            try:
                thread_company = Company.objects.get(pk=company.pk)
                thread_product = Product.objects.get(pk=product_id)
                barrier.wait(timeout=5)
                learn_confirmed_product_alias(
                    source_text=source_text,
                    product=thread_product,
                    company=thread_company,
                    explicit_confirmation=True,
                )
            except ValidationError as exc:
                results.put(("validation", str(exc)))
            except Exception as exc:  # pragma: no cover - failure detail for PostgreSQL CI
                results.put(("error", repr(exc)))
            else:
                results.put(("created", source_text))
            finally:
                close_old_connections()

        threads = [
            Thread(target=learn_alias, args=("Customer Special Widget", first_product.id), daemon=True),
            Thread(target=learn_alias, args=("Customer-Special Widget", second_product.id), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads), "Equivalent alias writes deadlocked.")
        outcomes = [results.get_nowait()[0] for _ in range(results.qsize())]
        self.assertCountEqual(outcomes, ["created", "validation"])
        self.assertEqual(ProductAlias.objects.filter(company=company, is_active=True).count(), 1)

    def _assert_concurrent_alias_api_writes_are_serialized(self, company):
        staff = User.objects.create_user(
            username=f"alias-api-staff-{company.pk if company else 'global'}",
            password="pass",
            is_staff=True,
        )
        products = [
            Product.objects.create(name="Alias API Product A", price=Decimal("1.00"), status="draft"),
            Product.objects.create(name="Alias API Product B", price=Decimal("1.00"), status="draft"),
        ]
        barrier = Barrier(2)
        results = Queue()

        def create_alias(source_text, product_id):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(staff)
                barrier.wait(timeout=5)
                response = client.post(
                    reverse("quotation-alias-list"),
                    {
                        "company": company.pk if company else None,
                        "product": product_id,
                        "alias": source_text,
                    },
                    format="json",
                )
                results.put(("response", response.status_code))
            except Exception as exc:  # pragma: no cover - failure detail for PostgreSQL CI
                results.put(("error", repr(exc)))
            finally:
                close_old_connections()

        threads = [
            Thread(target=create_alias, args=("API Customer Widget", products[0].id), daemon=True),
            Thread(target=create_alias, args=("API-Customer Widget", products[1].id), daemon=True),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads), "Alias API writes deadlocked.")
        outcomes = [results.get_nowait() for _ in range(results.qsize())]
        self.assertCountEqual(outcomes, [("response", status.HTTP_201_CREATED), ("response", status.HTTP_400_BAD_REQUEST)])
        self.assertEqual(ProductAlias.objects.filter(company=company).count(), 1)

    def test_company_alias_api_writes_are_serialized(self):
        company = Company.objects.create(name="Concurrent Company Alias API Customer")
        self._assert_concurrent_alias_api_writes_are_serialized(company)

    def test_global_alias_api_writes_are_serialized(self):
        self._assert_concurrent_alias_api_writes_are_serialized(None)

    def test_ai_alias_approvals_for_one_line_are_serialized(self):
        staff = User.objects.create_user(
            username="concurrent-ai-alias-staff",
            password="pass",
            is_staff=True,
        )
        company = Company.objects.create(name="Concurrent AI Alias Customer")
        products = [
            Product.objects.create(name="Concurrent AI Product A", price=Decimal("1.00"), status="draft"),
            Product.objects.create(name="Concurrent AI Product B", price=Decimal("1.00"), status="draft"),
        ]
        batch = HistoricalImportBatch.objects.create(name="Concurrent AI approvals", created_by=staff)
        historical_import = HistoricalPriceImport.objects.create(
            batch=batch,
            company=company,
            source_type=HistoricalPriceImport.SOURCE_TYPE_PDF,
            source_filename="concurrent-ai.pdf",
            source_sha256="a" * 64,
            document_date=date(2026, 7, 1),
            created_by=staff,
        )
        line = HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            item_name="Concurrent Customer Dressing",
            quantity=Decimal("1.000"),
            unit="Pcs",
            unit_price=Decimal("10.00"),
            status=HistoricalPriceImportLine.STATUS_NEEDS_REVIEW,
        )
        suggestions = [
            HistoricalImportAISuggestion.objects.create(
                batch=batch,
                historical_import=historical_import,
                line=line,
                suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
                action=HistoricalImportAISuggestion.ACTION_CREATE_COMPANY_ALIAS,
                suggested_product=product,
                alias_text=line.item_name,
                confidence=0.91,
                reason="Concurrent staff approval.",
                created_by=staff,
            )
            for product in products
        ]
        barrier = Barrier(2)
        results = Queue()

        def approve_suggestion(suggestion_id):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(staff)
                barrier.wait(timeout=5)
                response = client.post(
                    reverse("quotation-historical-import-batch-apply-ai-suggestions", args=[batch.id]),
                    {"suggestion_ids": [suggestion_id]},
                    format="json",
                )
                results.put(("response", response.status_code))
            except Exception as exc:  # pragma: no cover - failure detail for PostgreSQL CI
                results.put(("error", repr(exc)))
            finally:
                close_old_connections()

        threads = [
            Thread(target=approve_suggestion, args=(suggestion.id,), daemon=True)
            for suggestion in suggestions
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(any(thread.is_alive() for thread in threads), "AI alias approvals deadlocked.")
        self.assertCountEqual(
            [results.get_nowait() for _ in range(results.qsize())],
            [("response", status.HTTP_200_OK), ("response", status.HTTP_200_OK)],
        )
        line.refresh_from_db()
        self.assertFalse(
            HistoricalImportAISuggestion.objects.filter(
                id__in=[suggestion.id for suggestion in suggestions],
                status=HistoricalImportAISuggestion.STATUS_PENDING,
            ).exists()
        )
        aliases = ProductAlias.objects.filter(company=company, alias=line.item_name)
        self.assertEqual(aliases.count(), 1)
        self.assertEqual(aliases.get().product_id, line.product_id)

    def test_ai_approval_cannot_be_overwritten_by_concurrent_rejection(self):
        staff = User.objects.create_user(
            username="concurrent-ai-reject-staff",
            password="pass",
            is_staff=True,
        )
        company = Company.objects.create(name="Concurrent AI Reject Customer")
        product = Product.objects.create(name="Concurrent AI Reject Product", price=Decimal("1.00"), status="draft")
        batch = HistoricalImportBatch.objects.create(name="Concurrent AI rejection", created_by=staff)
        historical_import = HistoricalPriceImport.objects.create(
            batch=batch,
            company=company,
            source_type=HistoricalPriceImport.SOURCE_TYPE_PDF,
            source_filename="concurrent-ai-reject.pdf",
            source_sha256="b" * 64,
            document_date=date(2026, 7, 1),
            created_by=staff,
        )
        line = HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            item_name="Concurrent AI Rejection Dressing",
            quantity=Decimal("1.000"),
            unit="Pcs",
            unit_price=Decimal("10.00"),
            status=HistoricalPriceImportLine.STATUS_NEEDS_REVIEW,
        )
        suggestion = HistoricalImportAISuggestion.objects.create(
            batch=batch,
            historical_import=historical_import,
            line=line,
            suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
            action=HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
            suggested_product=product,
            confidence=0.91,
            reason="Concurrent approval/rejection test.",
            created_by=staff,
        )
        approval_entered = Event()
        rejection_read = Event()
        release_approval = Event()
        results = Queue()
        original_apply = ai_learning._apply_one_suggestion
        original_get_object = HistoricalImportAISuggestionViewSet.get_object

        def delayed_apply(*args, **kwargs):
            approval_entered.set()
            if not release_approval.wait(timeout=5):
                raise AssertionError("Timed out waiting to release AI approval.")
            return original_apply(*args, **kwargs)

        def observed_get_object(view):
            value = original_get_object(view)
            rejection_read.set()
            return value

        def approve():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(staff)
                response = client.post(
                    reverse("quotation-historical-import-batch-apply-ai-suggestions", args=[batch.id]),
                    {"suggestion_ids": [suggestion.id]},
                    format="json",
                )
                results.put(("approve", response.status_code))
            except Exception as exc:  # pragma: no cover - failure detail for PostgreSQL CI
                results.put(("approve_error", repr(exc)))
            finally:
                close_old_connections()

        def reject():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(staff)
                response = client.post(
                    reverse("quotation-historical-import-ai-suggestion-reject", args=[suggestion.id]),
                    {"reason": "Concurrent rejection."},
                    format="json",
                )
                results.put(("reject", response.status_code))
            except Exception as exc:  # pragma: no cover - failure detail for PostgreSQL CI
                results.put(("reject_error", repr(exc)))
            finally:
                close_old_connections()

        with patch.object(ai_learning, "_apply_one_suggestion", side_effect=delayed_apply), patch.object(
            HistoricalImportAISuggestionViewSet,
            "get_object",
            observed_get_object,
        ):
            approval_thread = Thread(target=approve, daemon=True)
            rejection_thread = Thread(target=reject, daemon=True)
            approval_thread.start()
            self.assertTrue(approval_entered.wait(timeout=5), "Approval never reached the locked mutation.")
            rejection_thread.start()
            self.assertTrue(rejection_read.wait(timeout=5), "Rejection never read the pending suggestion.")
            release_approval.set()
            approval_thread.join(timeout=10)
            rejection_thread.join(timeout=10)

        self.assertFalse(approval_thread.is_alive(), "AI approval deadlocked.")
        self.assertFalse(rejection_thread.is_alive(), "AI rejection deadlocked.")
        self.assertCountEqual(
            [results.get_nowait() for _ in range(results.qsize())],
            [("approve", status.HTTP_200_OK), ("reject", status.HTTP_400_BAD_REQUEST)],
        )
        suggestion.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(suggestion.status, HistoricalImportAISuggestion.STATUS_APPLIED)
        self.assertEqual(line.product, product)


class ProductMatchingReworkTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="matching-rework-staff",
            password="pass",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Matching Rework Customer")
        self.client.force_authenticate(self.staff)

    def product(self, name, **kwargs):
        return Product.objects.create(
            name=name,
            price=Decimal("1.00"),
            status="draft",
            **kwargs,
        )

    def test_company_preview_falls_through_to_master_catalog(self):
        product = self.product("Pulse Oximeter")
        row = {"raw_name": "pulse-oximeter"}

        apply_match_to_preview_line(row, self.company)

        self.assertEqual(row["matched_product"], product.id)
        self.assertEqual(row["match_status"], "confirmed")
        self.assertEqual(row["match_method"], "canonical_name")
        self.assertEqual(row["match_candidates"][0]["product_id"], product.id)

    def test_company_alias_then_history_precede_global_alias(self):
        company_product = self.product("Company Preferred Gauze")
        historical_product = self.product("Historical Gauze")
        global_product = self.product("Global Gauze")
        ProductAlias.objects.create(company=self.company, product=company_product, alias="gauze special")
        ProductAlias.objects.create(product=global_product, alias="gauze special")

        alias_match = suggest_product_for_text("gauze special", self.company)

        self.assertEqual(alias_match.product, company_product)
        self.assertEqual(alias_match.method, "company_alias")

        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=historical_product,
            item_name_snapshot="Historical Gauze",
            quantity=Decimal("1.000"),
            unit_price=Decimal("3.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        CompanyPriceHistory.objects.create(
            company=self.company,
            product=historical_product,
            quotation=quotation,
            quotation_line=line,
            unit_price=Decimal("3.00"),
            created_by=self.staff,
        )
        ProductAlias.objects.create(product=global_product, alias="Historical Gauze")

        history_match = suggest_product_for_text("Historical Gauze", self.company)

        self.assertEqual(history_match.product, historical_product)
        self.assertEqual(history_match.method, "company_price_history")

    def test_alias_lookup_includes_normalized_unit_token_variants(self):
        product = self.product("Customer Piece Dressing")
        ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias="pcs sterile dressing",
        )

        match = suggest_product_for_text("piece sterile dressing", self.company)

        self.assertEqual(match.product, product)
        self.assertEqual(match.method, "company_alias")

    def test_alias_lookup_does_not_drop_equivalent_rows_after_first_hundred(self):
        filler_product = self.product("Common Alias Filler")
        target_product = self.product("Common Alias Target")
        ProductAlias.objects.bulk_create([
            ProductAlias(
                company=self.company,
                product=filler_product,
                alias=f"common filler wording {index}",
                normalized_alias=f"common filler wording {index}",
            )
            for index in range(101)
        ])
        ProductAlias.objects.create(
            company=self.company,
            product=target_product,
            alias="common-target wording",
        )

        match = suggest_product_for_text("common target wording", self.company)

        self.assertEqual(match.product, target_product)
        self.assertEqual(match.method, "company_alias")

    def test_exact_identifier_precedes_name_candidates(self):
        identifier_product = self.product("Completely Different Product", sku="MED-500", barcode="629000000001")
        self.product("MED 500")

        sku_match = suggest_product_for_text("MED-500")
        barcode_match = suggest_product_for_text("irrelevant", barcode="629000000001")

        self.assertEqual(sku_match.product, identifier_product)
        self.assertEqual(sku_match.method, "exact_sku_or_barcode")
        self.assertEqual(barcode_match.product, identifier_product)

    def test_canonical_identity_normalizes_strength_and_pack(self):
        product = self.product("Panadol", dosage="500mg", pack_size="24 tablets")

        match = suggest_product_for_text("PANADOL 0.5 g 24 tabs")
        resolution = create_or_reuse_product(name="Panadol 0.5g 24 tab")

        self.assertEqual(match.product, product)
        self.assertEqual(match.method, "canonical_name")
        self.assertEqual(resolution.product, product)
        self.assertFalse(resolution.created)
        self.assertEqual(Product.objects.filter(name__icontains="Panadol").count(), 1)

    def test_strength_and_pack_conflicts_are_not_auto_matched(self):
        self.product("Panadol", dosage="500mg", pack_size="24 tablets")

        strength_conflict = suggest_product_for_text("Panadol 650mg 24 tablets")
        pack_conflict = suggest_product_for_text("Panadol 500mg 48 tablets")

        self.assertIsNone(strength_conflict.product)
        self.assertIsNone(pack_conflict.product)
        self.assertNotEqual(strength_conflict.method, "canonical_name")
        self.assertNotEqual(pack_conflict.method, "canonical_name")

    def test_fuzzy_candidates_are_ranked_and_require_explicit_creation_confirmation(self):
        nearest = self.product("Pulse Oximeter")
        self.product("Pulse Monitor")

        first = create_or_reuse_product(name="Pulse Oximtre")

        self.assertTrue(first.requires_confirmation)
        self.assertIsNone(first.product)
        self.assertEqual(first.match.method, "fuzzy_candidates")
        self.assertEqual(first.match.candidates[0].product, nearest)
        self.assertGreater(first.match.candidates[0].score, first.match.candidates[1].score)

        confirmed = create_or_reuse_product(name="Pulse Oximtre", confirm_create=True)

        self.assertTrue(confirmed.created)
        self.assertTrue(confirmed.override_used)
        self.assertEqual(confirmed.product.name, "Pulse Oximtre")

    def test_duplicate_canonical_products_reuse_one_instead_of_creating_another(self):
        first = self.product("Alcohol Detector Mouth-Piece")
        self.product("Alcohol Detector Mouth Piece")
        before = Product.objects.count()

        resolution = create_or_reuse_product(name="ALCOHOL-DETECTOR MOUTH PIECE", confirm_create=True)

        self.assertFalse(resolution.created)
        self.assertEqual(resolution.product, first)
        self.assertEqual(Product.objects.count(), before)

    def test_exact_fingerprint_is_reused_when_same_name_has_other_variants(self):
        base = self.product("Panadol")
        self.product("Panadol", dosage="500mg", pack_size="24 tablets")
        before = Product.objects.count()

        resolution = create_or_reuse_product(name="PANADOL", confirm_create=True)

        self.assertEqual(resolution.product, base)
        self.assertFalse(resolution.created)
        self.assertEqual(Product.objects.count(), before)

    def test_alias_conflict_is_never_silently_remapped(self):
        original = self.product("Original Bandage")
        replacement = self.product("Replacement Bandage")
        alias = ProductAlias.objects.create(company=self.company, product=original, alias="band-aid")

        with self.assertRaises(ValidationError):
            create_product_alias(alias_text="band aid", product=replacement, company=self.company, actor=self.staff)

        alias.refresh_from_db()
        self.assertEqual(alias.product, original)

        api_response = self.client.post(
            reverse("quotation-alias-list"),
            {"company": self.company.id, "product": replacement.id, "alias": "band aid"},
            format="json",
        )
        self.assertEqual(api_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already points", str(api_response.data))

    def test_managed_alias_api_create_and_update_use_the_locked_write_path(self):
        product = self.product("Managed Alias Product")

        created = self.client.post(
            reverse("quotation-alias-list"),
            {
                "company": self.company.id,
                "product": product.id,
                "alias": "Managed Customer Wording",
                "notes": "Created by staff.",
            },
            format="json",
        )

        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        alias = ProductAlias.objects.get(pk=created.data["id"])
        self.assertEqual(alias.created_by, self.staff)
        updated = self.client.patch(
            reverse("quotation-alias-detail", args=[alias.id]),
            {"notes": "Reviewed and retired.", "is_active": False},
            format="json",
        )

        self.assertEqual(updated.status_code, status.HTTP_200_OK)
        alias.refresh_from_db()
        self.assertEqual(alias.notes, "Reviewed and retired.")
        self.assertFalse(alias.is_active)
        self.assertEqual(alias.product, product)
        self.assertEqual(alias.company, self.company)

        global_created = self.client.post(
            reverse("quotation-alias-list"),
            {
                "company": None,
                "product": product.id,
                "alias": "Managed Global Wording",
            },
            format="json",
        )
        self.assertEqual(global_created.status_code, status.HTTP_201_CREATED)
        self.assertIsNone(ProductAlias.objects.get(pk=global_created.data["id"]).company_id)

    def test_managed_alias_notes_update_allows_equivalent_spelling_sibling(self):
        product = self.product("Equivalent Sibling Product")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias="Legacy Gauze Wording",
            notes="Retired spelling.",
            is_active=False,
            created_by=self.staff,
        )
        learned_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias="Legacy-Gauze Wording",
            notes="Exact customer snapshot.",
            created_by=self.staff,
        )

        response = self.client.patch(
            reverse("quotation-alias-detail", args=[retired_alias.id]),
            {"notes": "Catalog team reviewed this retirement.", "is_active": False},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        retired_alias.refresh_from_db()
        learned_alias.refresh_from_db()
        self.assertEqual(retired_alias.notes, "Catalog team reviewed this retirement.")
        self.assertFalse(retired_alias.is_active)
        self.assertTrue(learned_alias.is_active)
        self.assertEqual(learned_alias.notes, "Exact customer snapshot.")

    def test_managed_alias_reactivation_rechecks_equivalent_spelling_sibling(self):
        retired_product = self.product("Retired Alias Product")
        active_product = self.product("Active Alias Product")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="Legacy Gauze Wording",
            is_active=False,
            created_by=self.staff,
        )
        ProductAlias.objects.create(
            company=self.company,
            product=active_product,
            alias="Legacy-Gauze Wording",
            is_active=True,
            created_by=self.staff,
        )

        response = self.client.patch(
            reverse("quotation-alias-detail", args=[retired_alias.id]),
            {"is_active": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already points", str(response.data))
        retired_alias.refresh_from_db()
        self.assertFalse(retired_alias.is_active)

    def test_applied_ai_suggestion_cannot_be_edited(self):
        product = self.product("Applied AI Suggestion Product")
        historical_import = HistoricalPriceImport.objects.create(
            company=self.company,
            source_type=HistoricalPriceImport.SOURCE_TYPE_PDF,
            source_filename="applied-ai-suggestion.pdf",
            source_sha256="c" * 64,
            document_date=date(2026, 7, 1),
            created_by=self.staff,
        )
        line = HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            item_name="Applied AI Suggestion Item",
            quantity=Decimal("1.000"),
            unit="Pcs",
            unit_price=Decimal("10.00"),
            status=HistoricalPriceImportLine.STATUS_READY,
            product=product,
        )
        suggestion = HistoricalImportAISuggestion.objects.create(
            historical_import=historical_import,
            line=line,
            suggestion_type=HistoricalImportAISuggestion.TYPE_LINE,
            action=HistoricalImportAISuggestion.ACTION_MATCH_EXISTING_PRODUCT,
            status=HistoricalImportAISuggestion.STATUS_APPLIED,
            suggested_product=product,
            alias_text="Original applied wording",
            created_by=self.staff,
        )

        response = self.client.patch(
            reverse("quotation-historical-import-ai-suggestion-detail", args=[suggestion.id]),
            {"alias_text": "Changed after approval"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        suggestion.refresh_from_db()
        self.assertEqual(suggestion.alias_text, "Original applied wording")

    def test_direct_inquiry_line_update_refetches_before_saving(self):
        product = self.product("Concurrent Inquiry Match")
        inquiry = Inquiry.objects.create(company=self.company, created_by=self.staff)
        line = InquiryLine.objects.create(
            inquiry=inquiry,
            raw_name="Concurrent inquiry wording",
            quantity=Decimal("1.000"),
            match_status=InquiryLine.MATCH_UNRESOLVED,
        )
        serializer = InquiryLineSerializer(line, data={"quantity": "2.000"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        InquiryLine.objects.filter(pk=line.pk).update(
            matched_product=product,
            match_status=InquiryLine.MATCH_CONFIRMED,
        )
        view = InquiryLineViewSet()
        view.request = SimpleNamespace(user=self.staff)

        view.perform_update(serializer)

        line.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.matched_product, product)
        self.assertEqual(line.match_status, InquiryLine.MATCH_CONFIRMED)

    def test_direct_quotation_line_update_refetches_before_saving(self):
        product = self.product("Concurrent Quotation Match")
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Concurrent quotation wording",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        serializer = QuotationLineSerializer(line, data={"quantity": "2.000"}, partial=True)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        QuotationLine.objects.filter(pk=line.pk).update(
            product=product,
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        view = QuotationLineViewSet()
        view.request = SimpleNamespace(user=self.staff)

        view.perform_update(serializer)

        line.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.product, product)
        self.assertEqual(line.match_status, QuotationLine.MATCH_CONFIRMED)

    def test_direct_quotation_line_update_cannot_move_between_quotations(self):
        source = Quotation.objects.create(company=self.company, created_by=self.staff)
        target = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
            status=Quotation.STATUS_FINALIZED,
        )
        line = QuotationLine.objects.create(
            quotation=source,
            item_name_snapshot="Immovable quotation line",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
        )

        response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {"quotation": target.id},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("cannot be moved", str(response.data))
        line.refresh_from_db()
        self.assertEqual(line.quotation, source)

    def test_quote_line_creation_returns_candidates_then_accepts_clear_override(self):
        existing = self.product("Pulse Oximeter")
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Pulse Oximtre",
            quantity=Decimal("1.000"),
            unit_price=Decimal("20.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        url = reverse("quotation-line-create-product", args=[line.id])

        warning = self.client.post(url, {"product_name": "Pulse Oximtre"}, format="json")

        self.assertEqual(warning.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(warning.data["requires_confirmation"])
        self.assertEqual(warning.data["candidates"][0]["product_id"], existing.id)
        line.refresh_from_db()
        self.assertIsNone(line.product)

        confirmed = self.client.post(
            url,
            {"product_name": "Pulse Oximtre", "confirm_create": True},
            format="json",
        )

        self.assertEqual(confirmed.status_code, status.HTTP_201_CREATED)
        self.assertTrue(confirmed.data["created"])
        line.refresh_from_db()
        self.assertEqual(line.product.name, "Pulse Oximtre")

    def test_historical_bulk_creation_uses_the_same_confirmation_contract(self):
        existing = self.product("Pulse Oximeter")
        historical_import = HistoricalPriceImport.objects.create(
            company=self.company,
            source_filename="history.pdf",
            document_number="QT-HISTORY-1",
            document_date=date(2026, 1, 5),
            created_by=self.staff,
        )
        row = HistoricalPriceImportLine.objects.create(
            historical_import=historical_import,
            item_name="Pulse Oximtre",
            quantity=Decimal("1.000"),
            unit="piece",
            unit_price=Decimal("20.00"),
        )
        url = reverse("quotation-historical-import-bulk-create-quote-items", args=[historical_import.id])

        warning = self.client.post(url, {"row_ids": [row.id]}, format="json")

        self.assertEqual(warning.status_code, status.HTTP_200_OK)
        self.assertEqual(warning.data["summary"]["confirmation_required"], 1)
        candidate = warning.data["summary"]["results"][0]
        self.assertEqual(candidate["status"], "confirmation_required")
        self.assertEqual(candidate["candidates"][0]["product_id"], existing.id)
        row.refresh_from_db()
        self.assertIsNone(row.product)

        confirmed = self.client.post(
            url,
            {"row_ids": [row.id], "confirm_create_row_ids": [row.id]},
            format="json",
        )

        self.assertEqual(confirmed.status_code, status.HTTP_200_OK)
        self.assertEqual(confirmed.data["summary"]["created"], 1)
        row.refresh_from_db()
        self.assertEqual(row.product.name, "Pulse Oximtre")

    def test_quotation_item_post_reuses_exact_canonical_identity(self):
        existing = self.product("Alcohol Detector Mouth-Piece")

        response = self.client.post(
            reverse("quotation-item-list"),
            {"name": "Alcohol Detector Mouth Piece", "price": "1.00"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["created"])
        self.assertEqual(response.data["product_id"], existing.id)
        self.assertEqual(Product.objects.count(), 1)

    @patch("quotations.views.clean_preview_with_ai")
    def test_ai_cleaned_preview_rows_are_rematched(self, clean_preview):
        product = self.product("Sterile Gauze 10cm")
        clean_preview.return_value = {
            "source_type": "pasted_text",
            "lines": [{"raw_name": "Sterile Gauze 10 cm", "match_status": "unresolved"}],
            "warnings": [],
        }

        response = self.client.post(
            reverse("quotation-inquiry-ai-clean-parse"),
            {"company": self.company.id, "preview": {"source_type": "pasted_text", "lines": []}},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["lines"][0]["matched_product"], product.id)
        self.assertEqual(response.data["lines"][0]["match_method"], "canonical_name")

    @patch("quotations.views.maybe_attach_auto_ai_candidate")
    def test_auto_ai_candidate_rows_are_rematched_before_return(self, attach_candidate):
        product = self.product("Digital Thermometer")

        def add_candidate(preview, *args, **kwargs):
            preview["ai_candidate"] = {
                "source_type": "pasted_text",
                "lines": [{"raw_name": "Digital-Thermometer", "match_status": "unresolved"}],
                "warnings": [],
            }
            return preview

        attach_candidate.side_effect = add_candidate

        response = self.client.post(
            reverse("quotation-inquiry-parse-text"),
            {"company": self.company.id, "raw_text": "messy thermometer request"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data["ai_candidate"]["lines"][0]
        self.assertEqual(row["matched_product"], product.id)
        self.assertEqual(row["match_status"], "confirmed")

    def test_imported_confirmed_match_learns_alias_and_quote_keeps_requested_name(self):
        product = self.product("Plastic Adhesive Dressings")
        requested_name = "Plastic Band Aids"

        imported = self.client.post(
            reverse("quotation-inquiry-create-imported"),
            {
                "company": self.company.id,
                "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
                "subject": "Customer RFQ",
                "lines": [
                    {
                        "raw_name": requested_name,
                        "raw_line": requested_name,
                        "quantity": "1000.000",
                        "unit": "pieces",
                        "matched_product": product.id,
                        "match_status": InquiryLine.MATCH_CONFIRMED,
                        "parse_status": InquiryLine.PARSE_PARSED,
                        "parse_confidence": 0.95,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(imported.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias=requested_name,
            ).exists()
        )

        quote_response = self.client.post(
            reverse("quotation-inquiry-create-quote", args=[imported.data["id"]])
        )

        self.assertEqual(quote_response.status_code, status.HTTP_201_CREATED)
        quote_line = QuotationLine.objects.get(quotation_id=quote_response.data["id"])
        self.assertEqual(quote_line.item_name_snapshot, requested_name)
        self.assertEqual(quote_line.product, product)

    def test_staff_corrected_import_can_replace_exact_retired_alias(self):
        retired_product = self.product("Former Imported Mapping")
        replacement = self.product("Reviewed Imported Product")
        requested_name = "Reviewed Imported Exact Item"
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=requested_name,
            is_active=False,
            notes="Retired before import review.",
            created_by=self.staff,
        )

        response = self.client.post(
            reverse("quotation-inquiry-create-imported"),
            {
                "company": self.company.id,
                "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
                "subject": "Staff-corrected imported request",
                "lines": [
                    {
                        "raw_name": requested_name,
                        "quantity": "1.000",
                        "matched_product": replacement.id,
                        "match_status": InquiryLine.MATCH_CONFIRMED,
                        "match_confirmed_by_user": True,
                        "parse_status": InquiryLine.PARSE_PARSED,
                        "parse_confidence": 0.95,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        retired_alias.refresh_from_db()
        self.assertEqual(retired_alias.product, replacement)
        self.assertTrue(retired_alias.is_active)
        self.assertTrue(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
                changes__learning_action="reassigned",
            ).exists()
        )

    def test_manual_nested_inquiry_match_learns_company_alias(self):
        product = self.product("Sterile Oval Eye Pad")
        requested_name = "Oval Eye Pads - Sterile"

        response = self.client.post(
            reverse("quotation-inquiry-list"),
            {
                "company": self.company.id,
                "subject": "Manual customer request",
                "lines": [
                    {
                        "raw_name": requested_name,
                        "quantity": "50.000",
                        "unit": "Pcs",
                        "matched_product": product.id,
                        "match_status": InquiryLine.MATCH_CONFIRMED,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias=requested_name,
            ).exists()
        )

    def test_manual_nested_inquiry_match_can_replace_exact_retired_alias(self):
        retired_product = self.product("Former Manual Inquiry Mapping")
        replacement = self.product("Replacement Manual Inquiry Product")
        requested_name = "Manual Customer Exact Item"
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=requested_name,
            is_active=False,
            notes="Retired before the customer confirmed the replacement.",
            created_by=self.staff,
        )

        response = self.client.post(
            reverse("quotation-inquiry-list"),
            {
                "company": self.company.id,
                "subject": "Reviewed manual customer request",
                "lines": [
                    {
                        "raw_name": requested_name,
                        "quantity": "1.000",
                        "unit": "Pcs",
                        "matched_product": replacement.id,
                        "match_status": InquiryLine.MATCH_CONFIRMED,
                    }
                ],
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        retired_alias.refresh_from_db()
        self.assertEqual(retired_alias.product, replacement)
        self.assertTrue(retired_alias.is_active)
        self.assertTrue(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
                changes__learning_action="reassigned",
            ).exists()
        )

    def test_manual_inquiry_line_update_learns_alias(self):
        product = self.product("Triangular Bandage")
        inquiry = Inquiry.objects.create(company=self.company, subject="Manual match", created_by=self.staff)
        line = InquiryLine.objects.create(
            inquiry=inquiry,
            raw_name="Triangle Bandage Cloth",
            quantity=Decimal("6.000"),
            match_status=InquiryLine.MATCH_UNRESOLVED,
        )

        response = self.client.patch(
            reverse("quotation-inquiry-line-detail", args=[line.id]),
            {
                "matched_product": product.id,
                "match_status": InquiryLine.MATCH_CONFIRMED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias=line.raw_name,
            ).exists()
        )

    def test_direct_inquiry_quantity_edit_does_not_revive_retired_alias(self):
        retired_product = self.product("Former Direct Inquiry Mapping")
        matched_product = self.product("Current Direct Inquiry Product")
        source_wording = "Direct Inquiry Customer Item"
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=source_wording,
            is_active=False,
            notes="Retired direct inquiry mapping.",
            created_by=self.staff,
        )
        inquiry = Inquiry.objects.create(company=self.company, subject="Direct quantity edit", created_by=self.staff)
        line = InquiryLine.objects.create(
            inquiry=inquiry,
            raw_name=source_wording,
            quantity=Decimal("1.000"),
            matched_product=matched_product,
            match_status=InquiryLine.MATCH_CONFIRMED,
        )

        response = self.client.patch(
            reverse("quotation-inquiry-line-detail", args=[line.id]),
            {
                "raw_name": source_wording,
                "matched_product": matched_product.id,
                "match_status": InquiryLine.MATCH_CONFIRMED,
                "quantity": "2.000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(retired_alias.product, retired_product)
        self.assertFalse(retired_alias.is_active)
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
            ).exists()
        )

    def test_direct_quote_match_preserves_snapshot_even_if_client_sends_product_name(self):
        product = self.product("Ammonia Inhalant Bottle")
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Ammonia Inhalant - Bottle",
            quantity=Decimal("7.000"),
            unit="Ampoules",
            unit_price=Decimal("13.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {
                "product": product.id,
                "item_name_snapshot": product.name,
                "match_status": QuotationLine.MATCH_CONFIRMED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.item_name_snapshot, "Ammonia Inhalant - Bottle")
        self.assertEqual(line.product, product)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias="Ammonia Inhalant - Bottle",
            ).exists()
        )

    def test_direct_quote_quantity_edit_does_not_revive_retired_alias(self):
        retired_product = self.product("Former Direct Quote Mapping")
        matched_product = self.product("Current Direct Quote Product")
        source_wording = "Direct Quote Customer Item"
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=source_wording,
            is_active=False,
            notes="Retired direct quote mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=matched_product,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )

        response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {
                "product": matched_product.id,
                "item_name_snapshot": source_wording,
                "match_status": QuotationLine.MATCH_CONFIRMED,
                "quantity": "2.000",
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(retired_alias.product, retired_product)
        self.assertFalse(retired_alias.is_active)
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
            ).exists()
        )

    def test_bulk_candidate_link_preserves_snapshot_and_learns_alias(self):
        product = self.product("Pulse Oximeter")
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Pulse Oximtre",
            quantity=Decimal("1.000"),
            unit="piece",
            unit_price=Decimal("20.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[quotation.id]),
            {
                "lines": [
                    {
                        "id": line.id,
                        "product": product.id,
                        "item_name_snapshot": product.name,
                        "match_status": QuotationLine.MATCH_CONFIRMED,
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.item_name_snapshot, "Pulse Oximtre")
        self.assertEqual(line.product, product)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias="Pulse Oximtre",
            ).exists()
        )

    def test_single_and_bulk_create_or_reuse_preserve_source_snapshots(self):
        single_product = self.product("Alcohol Detector Mouth-Piece")
        bulk_product = self.product("Gauze Bandage 1 Inch Sterile Roll")
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        single_line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="ALCOHOL DETECTOR MOUTH PIECE",
            quantity=Decimal("50.000"),
            unit="Pcs",
            unit_price=Decimal("2.75"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        bulk_line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Gauze Bandage (1 Inch) - Sterile Roll",
            quantity=Decimal("10.000"),
            unit="Pcs",
            unit_price=Decimal("0.80"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        single_response = self.client.post(
            reverse("quotation-line-create-product", args=[single_line.id]),
            format="json",
        )
        bulk_response = self.client.post(
            reverse("quotation-bulk-create-products-for-lines", args=[quotation.id]),
            {"line_ids": [bulk_line.id]},
            format="json",
        )

        self.assertEqual(single_response.status_code, status.HTTP_200_OK)
        self.assertEqual(bulk_response.status_code, status.HTTP_200_OK)
        single_line.refresh_from_db()
        bulk_line.refresh_from_db()
        self.assertEqual(single_line.item_name_snapshot, "ALCOHOL DETECTOR MOUTH PIECE")
        self.assertEqual(single_line.product, single_product)
        self.assertEqual(bulk_line.item_name_snapshot, "Gauze Bandage (1 Inch) - Sterile Roll")
        self.assertEqual(bulk_line.product, bulk_product)
        self.assertEqual(
            set(ProductAlias.objects.filter(company=self.company).values_list("alias", flat=True)),
            {
                "ALCOHOL DETECTOR MOUTH PIECE",
                "Gauze Bandage (1 Inch) - Sterile Roll",
            },
        )

    def test_inquiry_wording_repairs_older_overwritten_quote_snapshot(self):
        product = self.product("Canonical First Aid Plasters")
        inquiry = Inquiry.objects.create(company=self.company, subject="Older quote", created_by=self.staff)
        inquiry_line = InquiryLine.objects.create(
            inquiry=inquiry,
            raw_name="Customer First Aid Band Aids",
            quantity=Decimal("10.000"),
        )
        quotation = Quotation.objects.create(company=self.company, inquiry=inquiry, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            inquiry_line=inquiry_line,
            item_name_snapshot=product.name,
            quantity=Decimal("10.000"),
            unit="Pcs",
            unit_price=Decimal("1.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-line-create-product", args=[line.id]),
            {"product_name": product.name},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.item_name_snapshot, inquiry_line.raw_name)
        self.assertEqual(line.product, product)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias=inquiry_line.raw_name,
            ).exists()
        )

    def test_alias_conflict_rolls_back_bulk_product_match(self):
        original = self.product("Original Customer Product")
        replacement = self.product("Replacement Customer Product")
        source_wording = "Customer Special Item"
        ProductAlias.objects.create(
            company=self.company,
            product=original,
            alias=source_wording,
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[quotation.id]),
            {"lines": [{"id": line.id, "product": replacement.id}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        line.refresh_from_db()
        self.assertIsNone(line.product)
        self.assertEqual(line.match_status, QuotationLine.MATCH_UNRESOLVED)
        self.assertEqual(line.item_name_snapshot, source_wording)
        self.assertEqual(
            ProductAlias.objects.get(company=self.company, alias=source_wording).product,
            original,
        )

    def test_quantity_only_bulk_save_does_not_revive_retired_alias(self):
        retired_product = self.product("Former Quantity Save Mapping")
        matched_product = self.product("Current Quantity Save Product")
        source_wording = "Quantity Save Customer Item"
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=source_wording,
            is_active=False,
            notes="Retired catalog decision.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=matched_product,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit="Pcs",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[quotation.id]),
            {
                "lines": [
                    {
                        "id": line.id,
                        "product": matched_product.id,
                        "item_name_snapshot": source_wording,
                        "match_status": QuotationLine.MATCH_CONFIRMED,
                        "quantity": "2.000",
                        "unit": "Pcs",
                        "unit_price": "10.00",
                    }
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.quantity, Decimal("2.000"))
        self.assertEqual(line.product, matched_product)
        self.assertEqual(retired_alias.product, retired_product)
        self.assertFalse(retired_alias.is_active)
        self.assertEqual(retired_alias.notes, "Retired catalog decision.")
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
            ).exists()
        )

    def test_inactive_alias_to_another_product_does_not_roll_back_single_creation(self):
        retired_product = self.product("Former Customer Mapping")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="Customer Special Widget",
            is_active=False,
            notes="Retired mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Customer-Special Widget",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-line-create-product", args=[line.id]),
            {"product_name": "New Canonical Widget"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["created"])
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.product.name, "New Canonical Widget")
        self.assertEqual(line.item_name_snapshot, "Customer-Special Widget")
        self.assertFalse(retired_alias.is_active)
        self.assertEqual(retired_alias.product, retired_product)
        self.assertEqual(retired_alias.alias, "Customer Special Widget")
        self.assertEqual(retired_alias.notes, "Retired mapping.")
        self.assertEqual(retired_alias.created_by, self.staff)
        learned_alias = ProductAlias.objects.get(
            company=self.company,
            product=line.product,
            alias="Customer-Special Widget",
        )
        self.assertTrue(learned_alias.is_active)

    def test_inactive_alias_to_another_product_does_not_roll_back_bulk_creation(self):
        retired_product = self.product("Former Bulk Mapping")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="Bulk Customer Special Widget",
            is_active=False,
            notes="Retired bulk mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Bulk Customer-Special Widget",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-bulk-create-products-for-lines", args=[quotation.id]),
            {
                "line_ids": [line.id],
                "names": {str(line.id): "New Bulk Canonical Widget"},
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["created_products"], 1)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.product.name, "New Bulk Canonical Widget")
        self.assertEqual(line.item_name_snapshot, "Bulk Customer-Special Widget")
        self.assertFalse(retired_alias.is_active)
        self.assertEqual(retired_alias.product, retired_product)
        self.assertEqual(retired_alias.alias, "Bulk Customer Special Widget")
        self.assertEqual(retired_alias.notes, "Retired bulk mapping.")
        self.assertEqual(retired_alias.created_by, self.staff)
        learned_alias = ProductAlias.objects.get(
            company=self.company,
            product=line.product,
            alias="Bulk Customer-Special Widget",
        )
        self.assertTrue(learned_alias.is_active)

    def test_exact_retired_alias_is_reassigned_and_audited_after_staff_confirmation(self):
        retired_product = self.product("Former Exact Mapping")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="customer exact widget",
            is_active=False,
            notes="Retired exact mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Customer Exact Widget",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-line-create-product", args=[line.id]),
            {"product_name": "Replacement Exact Widget"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(retired_alias.product, line.product)
        self.assertEqual(retired_alias.alias, "Customer Exact Widget")
        self.assertTrue(retired_alias.is_active)
        self.assertIn("Retired exact mapping.", retired_alias.notes)
        self.assertIn(f"Learned from confirmed quotation line {line.pk}.", retired_alias.notes)
        self.assertEqual(retired_alias.created_by, self.staff)
        audit = QuotationAuditLog.objects.get(
            target_type="ProductAlias",
            target_id=retired_alias.id,
            changes__learning_action="reassigned",
        )
        self.assertEqual(audit.changes["previous"]["product_id"], retired_product.id)
        self.assertEqual(audit.changes["product_id"], line.product_id)

    def test_exact_retired_alias_is_reassigned_when_snapshot_becomes_product_name(self):
        retired_product = self.product("Former Same Name Mapping")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="Customer Same Name Widget",
            is_active=False,
            notes="Retired same-name mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Customer Same Name Widget",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-line-create-product", args=[line.id]),
            {"product_name": line.item_name_snapshot},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.product.name, line.item_name_snapshot)
        self.assertEqual(retired_alias.product, line.product)
        self.assertTrue(retired_alias.is_active)
        audit = QuotationAuditLog.objects.get(
            target_type="ProductAlias",
            target_id=retired_alias.id,
            changes__learning_action="reassigned",
        )
        self.assertEqual(audit.changes["previous"]["product_id"], retired_product.id)
        self.assertEqual(audit.changes["product_id"], line.product_id)

    def test_auto_confirmed_import_does_not_reactivate_retired_exact_alias(self):
        retired_product = self.product("Former Automatic Import Mapping")
        source_wording = "Automatic Import Widget"
        automatic_match = self.product(source_wording)
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias=source_wording,
            is_active=False,
            notes="Retired by catalog review.",
            created_by=self.staff,
        )

        inquiry = create_imported_inquiry(
            {
                "company": self.company,
                "subject": "Automatic match import",
                "source_type": Inquiry.SOURCE_TYPE_PASTED_TEXT,
                "lines": [
                    {
                        "raw_name": source_wording,
                        "quantity": Decimal("1.000"),
                        "matched_product": automatic_match,
                        "match_status": InquiryLine.MATCH_CONFIRMED,
                        "match_reason": "Automatically matched canonical Product name.",
                        "parse_status": InquiryLine.PARSE_PARSED,
                        "parse_confidence": 0.99,
                    }
                ],
            },
            self.staff,
        )

        retired_alias.refresh_from_db()
        imported_line = inquiry.lines.get()
        self.assertEqual(imported_line.matched_product, automatic_match)
        self.assertEqual(imported_line.match_status, InquiryLine.MATCH_CONFIRMED)
        self.assertEqual(retired_alias.product, retired_product)
        self.assertFalse(retired_alias.is_active)
        self.assertEqual(retired_alias.notes, "Retired by catalog review.")
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=retired_alias.id,
            ).exists()
        )

    def test_concurrent_inactive_same_product_alias_is_reactivated_after_explicit_confirmation(self):
        product = self.product("Concurrent Recovery Product")
        source_wording = "Concurrent Recovery Customer Item"
        inactive_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias=source_wording,
            is_active=False,
            notes="Retired before concurrent confirmation.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=product,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        original_locking_queryset = ProductAlias.objects.select_for_update()

        with (
            patch("quotations.matching._aliases_for_text", return_value=[]),
            patch.object(
                ProductAlias.objects,
                "select_for_update",
                side_effect=[ProductAlias.objects.none(), original_locking_queryset],
            ),
        ):
            learned_alias, created = learn_confirmed_quotation_line_alias(
                line,
                self.staff,
                explicit_confirmation=True,
            )

        inactive_alias.refresh_from_db()
        self.assertEqual(learned_alias, inactive_alias)
        self.assertFalse(created)
        self.assertTrue(inactive_alias.is_active)
        self.assertTrue(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=inactive_alias.id,
                changes__learning_action="reactivated",
            ).exists()
        )

    def test_concurrent_inactive_alias_stays_retired_for_automatic_match(self):
        product = self.product("Automatic Concurrent Recovery Product")
        source_wording = "Automatic Concurrent Recovery Item"
        inactive_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias=source_wording,
            is_active=False,
            notes="Retired automatic recovery mapping.",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=product,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        original_locking_queryset = ProductAlias.objects.select_for_update()

        with (
            patch("quotations.matching._aliases_for_text", return_value=[]),
            patch.object(
                ProductAlias.objects,
                "select_for_update",
                side_effect=[ProductAlias.objects.none(), original_locking_queryset],
            ),
        ):
            learned_alias, created = learn_confirmed_quotation_line_alias(line, self.staff)

        inactive_alias.refresh_from_db()
        self.assertIsNone(learned_alias)
        self.assertFalse(created)
        self.assertFalse(inactive_alias.is_active)
        self.assertEqual(inactive_alias.notes, "Retired automatic recovery mapping.")
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                target_type="ProductAlias",
                target_id=inactive_alias.id,
            ).exists()
        )

    def test_same_name_product_link_rolls_back_when_active_alias_points_elsewhere(self):
        original = self.product("Original Same Name Alias Product")
        source_wording = "Customer Canonical Widget"
        replacement = self.product(source_wording)
        active_alias = ProductAlias.objects.create(
            company=self.company,
            product=original,
            alias=source_wording,
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot=source_wording,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[quotation.id]),
            {"lines": [{"id": line.id, "product": replacement.id}]},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        line.refresh_from_db()
        active_alias.refresh_from_db()
        self.assertIsNone(line.product)
        self.assertEqual(line.match_status, QuotationLine.MATCH_UNRESOLVED)
        self.assertEqual(active_alias.product, original)
        self.assertTrue(active_alias.is_active)

    def test_active_alias_still_rolls_back_single_product_creation(self):
        original = self.product("Active Alias Product")
        active_alias = ProductAlias.objects.create(
            company=self.company,
            product=original,
            alias="Active Customer Widget",
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot=active_alias.alias,
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        product_count = Product.objects.count()

        response = self.client.post(
            reverse("quotation-line-create-product", args=[line.id]),
            {"product_name": "Different Canonical Widget"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Product.objects.count(), product_count)
        line.refresh_from_db()
        active_alias.refresh_from_db()
        self.assertIsNone(line.product)
        self.assertEqual(active_alias.product, original)
        self.assertTrue(active_alias.is_active)

    def test_fuzzy_override_creates_and_learns_alias_beside_retired_variant(self):
        self.product("Pulse Oximeter")
        retired_product = self.product("Former Pulse Request Mapping")
        retired_alias = ProductAlias.objects.create(
            company=self.company,
            product=retired_product,
            alias="Retired-Pulse Request",
            is_active=False,
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Retired Pulse Request",
            quantity=Decimal("1.000"),
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        url = reverse("quotation-line-create-product", args=[line.id])

        warning = self.client.post(url, {"product_name": "Pulse Oximtre"}, format="json")
        confirmed = self.client.post(
            url,
            {"product_name": "Pulse Oximtre", "confirm_create": True},
            format="json",
        )

        self.assertEqual(warning.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(confirmed.status_code, status.HTTP_201_CREATED)
        line.refresh_from_db()
        retired_alias.refresh_from_db()
        self.assertEqual(line.product.name, "Pulse Oximtre")
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=line.product,
                alias="Retired Pulse Request",
                is_active=True,
            ).exists()
        )
        self.assertEqual(retired_alias.product, retired_product)
        self.assertFalse(retired_alias.is_active)

    def test_changing_quote_inquiry_source_uses_the_new_inquiry_wording(self):
        product = self.product("Canonical Wound Dressing")
        old_inquiry = Inquiry.objects.create(company=self.company, subject="Old RFQ", created_by=self.staff)
        old_source = InquiryLine.objects.create(
            inquiry=old_inquiry,
            raw_name="Old Customer Dressing Name",
            quantity=Decimal("1.000"),
        )
        new_inquiry = Inquiry.objects.create(company=self.company, subject="Correct RFQ", created_by=self.staff)
        new_source = InquiryLine.objects.create(
            inquiry=new_inquiry,
            raw_name="Customer Sterile Dressing 10 x 10",
            quantity=Decimal("1.000"),
        )
        quotation = Quotation.objects.create(company=self.company, inquiry=old_inquiry, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            inquiry_line=old_source,
            item_name_snapshot=old_source.raw_name,
            quantity=Decimal("1.000"),
            unit_price=Decimal("5.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.patch(
            reverse("quotation-line-detail", args=[line.id]),
            {
                "inquiry_line": new_source.id,
                "product": product.id,
                "item_name_snapshot": product.name,
                "match_status": QuotationLine.MATCH_CONFIRMED,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        line.refresh_from_db()
        self.assertEqual(line.inquiry_line, new_source)
        self.assertEqual(line.item_name_snapshot, new_source.raw_name)
        self.assertTrue(
            ProductAlias.objects.filter(
                company=self.company,
                product=product,
                alias=new_source.raw_name,
            ).exists()
        )
        self.assertFalse(ProductAlias.objects.filter(company=self.company, alias=old_source.raw_name).exists())

    def test_automatic_alias_learning_preserves_curated_and_inactive_aliases(self):
        product = self.product("Canonical Gauze Product")
        active_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias="Customer Gauze",
            notes="Curated purchasing-team note.",
            created_by=self.staff,
        )
        inactive_alias = ProductAlias.objects.create(
            company=self.company,
            product=product,
            alias="Legacy Gauze Wording",
            notes="Deliberately disabled after catalog review.",
            is_active=False,
            created_by=self.staff,
        )
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        active_line = QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot=active_alias.alias,
            quantity=Decimal("1.000"),
            unit_price=Decimal("5.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )
        inactive_line = QuotationLine.objects.create(
            quotation=quotation,
            # Punctuation differs, but the pharmacy-aware normalizer treats
            # this as the same wording as the deliberately disabled alias.
            item_name_snapshot="Legacy-Gauze Wording",
            quantity=Decimal("1.000"),
            unit_price=Decimal("5.00"),
            match_status=QuotationLine.MATCH_UNRESOLVED,
        )

        response = self.client.post(
            reverse("quotation-bulk-update-lines", args=[quotation.id]),
            {
                "lines": [
                    {"id": active_line.id, "product": product.id},
                    {"id": inactive_line.id, "product": product.id},
                ]
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        active_alias.refresh_from_db()
        inactive_alias.refresh_from_db()
        self.assertEqual(active_alias.alias, "Customer Gauze")
        self.assertEqual(active_alias.notes, "Curated purchasing-team note.")
        self.assertTrue(active_alias.is_active)
        self.assertEqual(inactive_alias.alias, "Legacy Gauze Wording")
        self.assertEqual(inactive_alias.notes, "Deliberately disabled after catalog review.")
        self.assertFalse(inactive_alias.is_active)
        self.assertEqual(ProductAlias.objects.filter(company=self.company, product=product).count(), 3)
        learned_alias = ProductAlias.objects.get(
            company=self.company,
            product=product,
            alias="Legacy-Gauze Wording",
        )
        self.assertTrue(learned_alias.is_active)
        inactive_line.refresh_from_db()
        self.assertEqual(inactive_line.item_name_snapshot, "Legacy-Gauze Wording")
