from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError, PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from api.models import Product
from .models import Company, CompanyPriceHistory, Quotation, QuotationLine, QuotationPriceFeedback
from .pricing import recommend_price
from .services import bulk_update_quotation_lines, finalize_quotation
from .catalogue_identity import identity_preview, review_identity, identity_report
from .matching import suggest_product_for_text, create_or_reuse_product


@override_settings(QUOTATION_COMPANY_PRICE_AUTOFILL_ENABLED=True)
class CompanyPricingTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="pricer", is_staff=True)
        self.owner = User.objects.create_user(username="catalogue-owner", is_staff=True, is_superuser=True)
        self.client.force_authenticate(self.staff)
        self.company = Company.objects.create(name="Customer A")
        self.product = Product.objects.create(name="Nitrile gloves medium", price=1, pack_size="box")
        self.quote = Quotation.objects.create(company=self.company, created_by=self.staff)

    def history(self, amount="20", accepted=None, **kwargs):
        quote = Quotation.objects.create(company=kwargs.pop("company", self.company), created_by=self.staff,
                                         currency=kwargs.pop("currency", "AED"), status="finalized")
        product = kwargs.pop("product", self.product)
        unit = kwargs.pop("unit", "box")
        line = QuotationLine.objects.create(quotation=quote, product=product, item_name_snapshot=product.name,
                unit=unit, unit_price=Decimal(amount), match_status="confirmed", **kwargs)
        if accepted:
            line.outcome_status = "accepted"
            line.accepted_unit_price = Decimal(accepted)
            line.save()
            quote.outcome_date = timezone.now().date()
            quote.save()
        return CompanyPriceHistory.objects.create(company=quote.company, product=product, quotation=quote,
            quotation_line=line, unit_price=Decimal(amount), unit=unit, currency=quote.currency)

    def draft(self, **kwargs):
        return QuotationLine.objects.create(quotation=self.quote, product=self.product,
            item_name_snapshot=self.product.name, unit="box", match_status="confirmed", **kwargs)

    def save(self, line, **payload):
        bulk_update_quotation_lines(self.quote, [{"id": line.pk, **payload}], self.staff)
        line.refresh_from_db()
        return line

    def test_accepted_first_then_quoted_fallback(self):
        old = self.history(accepted="18")
        latest = self.history(amount="22")
        recommended = recommend_price(self.quote, self.product, "boxes")
        self.assertEqual(recommended["history_id"], old.pk)
        self.assertEqual(Decimal(recommended["amount"]), 18)
        old.quotation.status = "cancelled"; old.quotation.save()
        self.assertEqual(recommend_price(self.quote, self.product, "box")["history_id"], latest.pk)

    def test_wrong_company_unit_currency_and_variant_cannot_supply_price(self):
        self.history(company=Company.objects.create(name="Customer B"))
        self.history(unit="piece")
        self.history(currency="USD")
        wrong = self.history()
        wrong.quotation_line.item_name_snapshot = "Nitrile gloves large"
        wrong.quotation_line.save()
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])

    def test_source_provenance_survives_save_and_reload(self):
        source = self.history(accepted="18")
        line = self.draft()
        self.save(line, unit_price="18", price_source_history=source.pk)
        response = self.client.get(reverse("quotation-detail", args=[self.quote.pk]))
        self.assertEqual(response.data["lines"][0]["price_provenance"]["basis"], "accepted")
        self.assertEqual(response.data["lines"][0]["price_provenance"]["history_id"], source.pk)

    def test_single_line_update_uses_same_validation(self):
        source = self.history()
        line = self.draft()
        response = self.client.patch(reverse("quotation-line-detail", args=[line.pk]),
            {"unit_price": "20", "price_source_history": source.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["price_provenance"]["kind"], "history")

    def test_forged_source_is_rejected_atomically(self):
        source = self.history(currency="USD")
        line = self.draft()
        with self.assertRaises(ValidationError):
            self.save(line, unit_price="20", price_source_history=source.pk)
        line.refresh_from_db(); self.assertIsNone(line.unit_price)

    def test_manual_edit_is_audit_only_until_finalization(self):
        source = self.history(accepted="18")
        line = self.draft()
        self.save(line, unit_price="18", price_source_history=source.pk)
        self.save(line, unit_price="22", price_feedback="one_off")
        self.assertEqual(line.price_provenance["kind"], "manual")
        self.assertIsNone(line.accepted_unit_price)
        self.assertEqual(CompanyPriceHistory.objects.count(), 1)
        self.assertTrue(QuotationPriceFeedback.objects.filter(line=line, kind="one_off").exists())
        finalize_quotation(self.quote, self.staff)
        self.assertEqual(CompanyPriceHistory.objects.get(quotation_line=line).unit_price, 22)

    def test_outdated_does_not_resurrect_older_accepted_prices(self):
        self.history(accepted="16")
        source = self.history(accepted="18")
        line = self.draft()
        self.save(line, unit_price="18", price_source_history=source.pk)
        self.save(line, unit_price="22", price_feedback="outdated")
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])
        fresh = self.history(amount="23")
        result = recommend_price(self.quote, self.product, "box")
        self.assertEqual(result["history_id"], fresh.pk)
        self.assertEqual(result["basis"], "quoted")

    def test_wrong_product_clears_derived_price_preserves_customer_wording(self):
        source = self.history()
        line = self.draft(quantity=4)
        self.save(line, unit_price="20", price_source_history=source.pk)
        self.save(line, price_feedback="wrong_product")
        self.assertIsNone(line.product_id); self.assertIsNone(line.unit_price)
        self.assertEqual(line.quantity, 4); self.assertEqual(line.item_name_snapshot, self.product.name)

    def test_changed_product_keeps_manual_price_but_blocks_finalization_until_review(self):
        line = self.draft(unit_price=22)
        replacement = Product.objects.create(name="Nitrile gloves large", price=1)
        self.save(line, product=replacement.pk)
        self.assertEqual(line.unit_price, 22); self.assertTrue(line.price_review_required)
        with self.assertRaises(ValidationError): finalize_quotation(self.quote, self.staff)
        self.save(line, price_reviewed=True)
        finalize_quotation(self.quote, self.staff)

    def test_customer_change_invalidates_auto_and_marks_manual_for_review(self):
        source = self.history()
        auto = self.draft(); manual = self.draft(unit_price=27)
        self.save(auto, unit_price="20", price_source_history=source.pk)
        company = Company.objects.create(name="New Customer")
        response = self.client.patch(reverse("quotation-detail", args=[self.quote.pk]), {"company": company.pk})
        self.assertEqual(response.status_code, 200, response.data)
        auto.refresh_from_db(); manual.refresh_from_db()
        self.assertIsNone(auto.unit_price); self.assertTrue(manual.price_review_required)

    def test_no_automatic_price_for_provisional_identity(self):
        self.history(); self.product.identity_review_state = "provisional"; self.product.save()
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])
        self.assertIsNone(suggest_product_for_text(self.product.name, self.company).product)

    def test_reordered_words_find_unique_product_but_variants_do_not(self):
        self.assertEqual(suggest_product_for_text("Gloves medium nitrile").product, self.product)
        for text in ["Gloves large nitrile", "Gloves medium latex", "Nitrile gloves"]:
            with self.subTest(text=text): self.assertIsNone(suggest_product_for_text(text).product)

    def test_box_quantities_strength_and_sterility_are_not_interchangeable(self):
        for stored, requested in [("Swabs box of 100", "Swabs box of 200"),
                ("Paracetamol 500mg tablets", "Paracetamol tablets"),
                ("Sterile gauze", "Non sterile gauze")]:
            Product.objects.create(name=stored, price=1)
            with self.subTest(text=requested): self.assertIsNone(suggest_product_for_text(requested).product)

    def test_duplicate_ids_need_explicit_selection(self):
        Product.objects.create(name="Medium nitrile gloves", price=1, pack_size="box")
        self.assertIsNone(suggest_product_for_text(self.product.name).product)
        resolution = create_or_reuse_product(name=self.product.name, confirm_create=True)
        self.assertFalse(resolution.created); self.assertTrue(resolution.creation_blocked)

    @patch("quotations.catalogue_identity.identity_preview", return_value={"standard_name": "New Syringe", "ai_status": "review_ready"})
    def test_new_items_are_provisional_across_shared_creation_service(self, _preview):
        result = create_or_reuse_product(name="Syringe New")
        self.assertEqual(result.product.identity_review_state, "provisional")
        self.assertEqual(result.product.identity_notes["original_name"], "Syringe New")

    def test_owner_review_merge_keeps_historical_document_links(self):
        duplicate = Product.objects.create(name="Medium nitrile gloves", price=1, pack_size="box", identity_review_state="provisional")
        history = self.history(product=duplicate)
        payload = {"updated_at": duplicate.updated_at.isoformat(), "canonical_product_id": self.product.pk}
        with self.assertRaises(PermissionDenied): review_identity(duplicate.pk, payload, self.staff)
        review_identity(duplicate.pk, payload, self.owner)
        history.refresh_from_db(); history.quotation_line.refresh_from_db()
        self.assertEqual(history.product_id, duplicate.pk)
        self.assertEqual(history.quotation_line.item_name_snapshot, duplicate.name)
        self.assertEqual(recommend_price(self.quote, self.product, "box")["history_id"], history.pk)

    def test_report_contains_provisional_items_and_history_exceptions(self):
        self.product.identity_review_state = "provisional"; self.product.save()
        report = identity_report()
        self.assertEqual(report["results"][0]["id"], self.product.pk)

    @patch("quotations.ai_parsing.get_ai_parse_provider")
    @patch("quotations.ai_parsing.get_ai_parse_availability", return_value={"provider": "openai", "text_model": "configured"})
    @patch("quotations.ai_parsing.settings_ai_status", return_value={"status": "ai_available"})
    def test_ai_cannot_invent_attributes_or_product_ids(self, _status, _availability, provider):
        provider.return_value.clean_rows.return_value = ({"standard_name": "Nitrile gloves sterile large", "candidate_ids": [999999]}, {})
        result = identity_preview("Nitrile gloves medium")
        self.assertEqual(result["standard_name"], "Nitrile gloves medium")
        self.assertEqual(result["ai_status"], "unsupported_change_rejected")
        self.assertNotIn(999999, [row["product_id"] for row in result["candidates"]])

    def test_edit_before_first_save_retains_original_suggestion(self):
        source = self.history(accepted="18")
        line = self.draft()
        self.save(line, unit_price="22", price_original_history=source.pk)
        self.assertEqual(line.price_provenance["previous_source"]["history_id"], source.pk)
        self.assertEqual(line.price_provenance["kind"], "manual")
        self.assertEqual(QuotationPriceFeedback.objects.filter(line=line).last().previous["provenance"]["amount"], "18.000")

    def test_outdated_before_first_save_retires_the_suggestion(self):
        source = self.history(accepted="18")
        line = self.draft()
        self.save(line, unit_price="22", price_original_history=source.pk, price_feedback="outdated")
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])

    def test_new_manual_line_does_not_require_review_of_a_nonexistent_old_price(self):
        response = self.client.post(reverse("quotation-line-list"), {"quotation": self.quote.pk, "product": self.product.pk,
            "item_name_snapshot": self.product.name, "unit": "box", "unit_price": "22", "match_status": "confirmed"})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertFalse(response.data["price_review_required"])

    def test_swapped_combination_strengths_are_not_an_equivalent_identity(self):
        product = Product.objects.create(name="Amlodipine 5mg Valsartan 160mg", price=1)
        self.assertIsNone(suggest_product_for_text("Amlodipine 160mg Valsartan 5mg").product)

    def test_wrong_product_can_be_corrected_and_repriced_in_one_save(self):
        source = self.history()
        other = Product.objects.create(name="Gauze", price=1, pack_size="box")
        other_source = self.history(product=other, amount="13")
        line = self.draft()
        self.save(line, unit_price="20", price_source_history=source.pk)
        self.save(line, product=other.pk, unit_price="13", price_source_history=other_source.pk, price_feedback="wrong_product")
        self.assertEqual(line.unit_price, 13); self.assertFalse(line.price_review_required)
        self.assertEqual(suggest_product_for_text(self.product.name, self.company).product, other)

    def test_cancelled_saved_source_requires_a_review_before_finalization(self):
        source = self.history()
        line = self.draft()
        self.save(line, unit_price="20", price_source_history=source.pk)
        source.quotation.status = "cancelled"; source.quotation.save()
        with self.assertRaises(ValidationError): finalize_quotation(self.quote, self.staff)
        self.save(line, price_reviewed=True)
        self.assertEqual(line.price_provenance["kind"], "manual")
        finalize_quotation(self.quote, self.staff)

    def test_each_and_piece_are_equivalent_but_box_is_not(self):
        self.history(unit="each")
        self.assertTrue(recommend_price(self.quote, self.product, "pcs")["eligible"])
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])

    def test_deleting_a_draft_line_preserves_its_price_audit(self):
        line = self.draft()
        self.save(line, unit_price="22")
        line_id = line.pk
        response = self.client.delete(reverse("quotation-line-detail", args=[line_id]))
        self.assertEqual(response.status_code, 204, response.data)
        feedback = QuotationPriceFeedback.objects.get()
        self.assertIsNone(feedback.line_id)
        self.assertEqual(feedback.previous["line_id"], line_id)

    def test_owner_cannot_consolidate_different_brands(self):
        from api.models import Brand
        self.product.brand = Brand.objects.create(name="Brand A"); self.product.save()
        other = Product.objects.create(name=self.product.name, pack_size="box", price=1, brand=Brand.objects.create(name="Brand B"))
        with self.assertRaises(ValidationError):
            review_identity(other.pk, {"updated_at": other.updated_at.isoformat(), "canonical_product_id": self.product.pk}, self.owner)

    def test_conflicting_historical_brand_is_not_recommended(self):
        from api.models import Brand
        self.product.brand = Brand.objects.create(name="Brand A"); self.product.save()
        self.history(brand_name_snapshot="Brand B")
        self.assertFalse(recommend_price(self.quote, self.product, "box")["eligible"])

    def test_consolidated_identity_cannot_change_brand_through_catalogue_edit(self):
        from api.models import Brand
        from .catalogue_identity import validate_identity_edit
        duplicate = Product.objects.create(name=self.product.name, pack_size="box", price=1, canonical_product=self.product)
        with self.assertRaises(ValidationError):
            validate_identity_edit(duplicate, {"brand": Brand.objects.create(name="Different brand")})

    def test_unsaved_manual_row_keeps_context_review_when_added(self):
        response = self.client.post(reverse("quotation-line-list"), {"quotation": self.quote.pk, "product": self.product.pk,
            "item_name_snapshot": self.product.name, "unit": "box", "unit_price": "22", "match_status": "confirmed",
            "price_context_changed": True})
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data["price_review_required"])
        with self.assertRaises(ValidationError): finalize_quotation(self.quote, self.staff)
        line = QuotationLine.objects.get(pk=response.data["id"])
        self.save(line, price_reviewed=True)
        finalize_quotation(self.quote, self.staff)
