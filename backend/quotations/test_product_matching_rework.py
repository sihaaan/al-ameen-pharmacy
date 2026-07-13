from decimal import Decimal
from datetime import date
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .matching import (
    apply_match_to_preview_line,
    create_or_reuse_product,
    create_product_alias,
    suggest_product_for_text,
)
from .models import (
    Company,
    CompanyPriceHistory,
    HistoricalPriceImport,
    HistoricalPriceImportLine,
    ProductAlias,
    Quotation,
    QuotationLine,
)


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
