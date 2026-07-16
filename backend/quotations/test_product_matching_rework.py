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
    Inquiry,
    InquiryLine,
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
        self.assertEqual(ProductAlias.objects.filter(company=self.company, product=product).count(), 2)
        inactive_line.refresh_from_db()
        self.assertEqual(inactive_line.item_name_snapshot, "Legacy-Gauze Wording")
