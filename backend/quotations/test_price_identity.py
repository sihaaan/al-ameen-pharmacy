from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import Product
from .models import (Company, CompanyPriceHistory, CompanyProductIdentityMatch,
                     PriceRecommendationRetirement, Quotation, QuotationLine, QuotationPriceFeedback)
from .price_identity import prepare_price_identity_matches, safe_variant
from .pricing import pricing_unit, recommend_price, recommendation_context
from .services import bulk_update_quotation_lines, finalize_quotation


@override_settings(QUOTATION_COMPANY_PRICE_AUTOFILL_ENABLED=True)
class CompanyPriceIdentityTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="matcher", is_staff=True)
        self.company = Company.objects.create(name="Contracting company")
        self.quote = Quotation.objects.create(company=self.company, created_by=self.staff)
        self.product = Product.objects.create(name="WHEELCHAIR", price=1, pack_size="NOS")
        self.old_product = Product.objects.create(name="Wheel Chair", price=1, pack_size="NO.S")

    def history(self, product=None, unit="NO.S", status="sent", company=None):
        product = product or self.old_product
        quote = Quotation.objects.create(company=company or self.company, created_by=self.staff, status=status)
        line = QuotationLine.objects.create(quotation=quote, product=product, item_name_snapshot=product.name,
            unit=unit, unit_price=320, match_status="confirmed")
        return CompanyPriceHistory.objects.create(company=quote.company, product=product, quotation=quote,
            quotation_line=line, unit_price=320, unit=unit, currency="AED")

    def review(self, **overrides):
        proposal = {"product_id": self.product.pk, "historical_product_id": self.old_product.pk,
                    "same_product": True, "confidence": 0.99, "reason": "Same product, different spelling.", **overrides}
        with patch("quotations.ai_parsing.settings_ai_status", return_value={"status": "ai_available"}), \
             patch("quotations.ai_parsing.get_ai_parse_availability", return_value={"provider": "openai", "text_model": "test"}), \
             patch("quotations.ai_parsing.get_ai_parse_provider") as provider:
            provider.return_value.clean_rows.return_value = ({"rows": [proposal]}, {})
            prepare_price_identity_matches(self.quote, [self.product])
            return provider.return_value.clean_rows.call_count

    def typo_products(self):
        self.product.name = "Digital Thermometre"
        self.old_product.name = "Digital Thermometer"
        self.product.save()
        self.old_product.save()
        return self.history()

    def test_unit_spellings_are_equivalent_without_pack_conversion(self):
        for unit in ("NOS", "NO.S", "No. S.", "piece", "PCS", "each", "units"):
            self.assertEqual(pricing_unit(unit), "piece")
        for unit in ("box", "box 100", "100 pcs", "pack 10"):
            self.assertNotEqual(pricing_unit(unit), "piece")

    def test_existing_draft_finds_sent_duplicate_despite_revised_direct_history(self):
        source = self.history()
        self.history(product=self.product, unit="NOS", status="revised")
        result = recommend_price(self.quote, self.product, "NOS", source_wording="Wheelchair")
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["history_id"], source.pk)
        self.assertEqual(result["source_product_id"], self.old_product.pk)
        self.assertEqual(Decimal(result["amount"]), 320)
        self.assertTrue(result["matched_duplicate"])
        line = QuotationLine.objects.create(quotation=self.quote, product=self.product,
            item_name_snapshot="Wheelchair", unit="NOS", match_status="confirmed")
        bulk_update_quotation_lines(self.quote, [{"id": line.pk, "unit_price": "320", "price_source_history": source.pk}], self.staff)
        finalize_quotation(self.quote, self.staff)
        line.refresh_from_db()
        self.assertEqual(line.price_provenance["history_id"], source.pk)
        self.assertEqual(line.product_id, self.product.pk)

    def test_repeated_import_name_and_word_order_match(self):
        self.old_product.name = "WHEEL CHAIR Wheelchair"
        self.old_product.save()
        self.history()
        self.assertTrue(recommend_price(self.quote, self.product, "piece")["eligible"])
        self.product.name = "Cotton absorbent roll"
        self.old_product.name = "Roll absorbent cotton"
        self.product.save(); self.old_product.save()
        self.history()
        self.assertTrue(recommend_price(self.quote, self.product, "NOS")["eligible"])

    def test_specific_status_unit_currency_and_retirement_reasons(self):
        source = self.history(status="revised")
        self.assertIn("revised", recommend_price(self.quote, self.product, "NOS")["reason"])
        source.quotation.status = "sent"; source.quotation.save()
        self.assertIn("historical unit is NO.S", recommend_price(self.quote, self.product, "box")["reason"])
        self.quote.currency = "USD"
        self.assertIn("historical currency is AED", recommend_price(self.quote, self.product, "NOS")["reason"])
        self.quote.currency = "AED"
        PriceRecommendationRetirement.objects.create(company=self.company, product=self.old_product,
            unit="NO.S", currency="AED", retired_before=timezone.now())
        self.assertIn("outdated", recommend_price(self.quote, self.product, "NOS")["reason"])

    def test_other_company_and_wrong_product_feedback_cannot_be_bypassed(self):
        self.history(company=Company.objects.create(name="Other company"))
        self.assertFalse(recommend_price(self.quote, self.product, "NOS")["eligible"])
        self.history()
        QuotationPriceFeedback.objects.create(company=self.company, product=self.old_product,
            kind="wrong_product", source_wording="Wheelchair")
        self.assertFalse(recommend_price(self.quote, self.product, "NOS", source_wording="Wheelchair")["eligible"])

    def test_semantic_ai_match_persists_without_another_call_and_invalidates_on_edit(self):
        source = self.typo_products()
        self.assertFalse(recommend_price(self.quote, self.product, "NOS")["eligible"])
        self.assertEqual(self.review(), 1)
        self.assertEqual(self.review(), 0)
        self.assertEqual(recommend_price(self.quote, self.product, "NOS")["history_id"], source.pk)
        self.old_product.name = "Infrared Digital Thermometer"
        self.old_product.save()
        self.assertFalse(recommend_price(self.quote, self.product, "NOS")["eligible"])

    def test_low_confidence_invented_ids_and_uncertainty_do_not_fill(self):
        self.typo_products()
        for overrides in ({"confidence": 0.9}, {"historical_product_id": 999999}, {"same_product": False}):
            CompanyProductIdentityMatch.objects.all().delete()
            self.review(**overrides)
            self.assertFalse(recommend_price(self.quote, self.product, "NOS")["eligible"])

    def test_ai_links_work_in_reverse_and_respect_wrong_product_corrections(self):
        self.typo_products()
        self.review()
        source = self.history(product=self.product)
        self.assertEqual(recommend_price(self.quote, self.old_product, "NOS")["history_id"], source.pk)
        QuotationPriceFeedback.objects.create(company=self.company, product=self.product,
            kind="wrong_product", source_wording=self.product.name)
        self.assertFalse(recommend_price(self.quote, self.old_product, "NOS", source_wording=self.product.name)["eligible"])

    def test_batch_context_does_not_add_queries_per_product(self):
        self.history()
        ids = [Product.objects.create(name=f"Test item {i}", price=1).pk for i in range(20)]
        with CaptureQueriesContext(connection) as one:
            recommendation_context(self.quote, [self.product.pk])
        with CaptureQueriesContext(connection) as many:
            recommendation_context(self.quote, [self.product.pk, *ids])
        self.assertLessEqual(len(many), len(one) + 1)

    def test_variant_mismatch_cannot_be_overridden_by_ai(self):
        for left, right in (("Nitrile glovs medium", "Nitrile gloves large"),
                            ("Paracetmol tablet", "Paracetamol 500mg tablet"),
                            ("Glucometer", "Glucometer strips"),
                            ("Cotton roll 100g", "Cotton roll 500g")):
            self.product.name, self.old_product.name = left, right
            self.assertFalse(safe_variant(self.product, self.old_product), (left, right))

    def test_api_uses_batched_ai_fallback_and_keeps_entered_price(self):
        source = self.typo_products()
        line = QuotationLine.objects.create(quotation=self.quote, product=self.product,
            item_name_snapshot=self.product.name, unit="NOS", unit_price=350, match_status="confirmed")
        client = APIClient(); client.force_authenticate(self.staff)
        # Verify the endpoint invokes the preparation outside save operations.
        with patch("quotations.price_identity.prepare_price_identity_matches", side_effect=lambda q, p: self.review()) as prepare:
            response = client.get(reverse("quotation-product-prices", args=[self.quote.pk]), {"products": str(self.product.pk)})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(prepare.call_count, 1)
        result = response.data["results"][str(self.product.pk)]["line_recommendations"][str(line.pk)]
        self.assertEqual(result["history_id"], source.pk)
        self.assertEqual(response.data["results"][str(self.product.pk)]["history"][0]["source_product_id"], self.old_product.pk)
        line.refresh_from_db(); self.assertEqual(line.unit_price, 350)
