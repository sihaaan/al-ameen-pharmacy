from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from api.models import Product
from .creation_matching import review_creation_rows
from .matching import apply_match_to_preview_line, identities_compatible, item_identity, normalize_item_text
from .models import Company, CompanyPriceHistory, ProductAlias, Quotation, QuotationLine
from .pricing import recommend_price


class DimensionSpacingTests(SimpleTestCase):
    def test_inline_decimal_sizes_have_the_same_identity(self):
        for spaced, clean in [
            ("GAUZE SWAB 7. 5CM", "Gauze swab 7.5cm"),
            ("Dressing 10. 25 mm", "Dressing 10.25mm"),
            ("Tape 1.\t5 inch", "Tape 1.5 inch"),
            ("Gauze 7.\u00a05cm", "Gauze 7.5cm"),
        ]:
            with self.subTest(spaced=spaced):
                self.assertEqual(normalize_item_text(spaced), normalize_item_text(clean))
                self.assertEqual(item_identity(spaced), item_identity(clean))

    def test_repair_does_not_join_doses_counts_or_list_numbers(self):
        for text, joined in [
            ("Tablet 7. 5mg", "Tablet 7.5mg"),
            ("Gauze 7. 5pcs", "Gauze 7.5pcs"),
            ("Gauze 7.\n5cm", "Gauze 7.5cm"),
            ("1. 5cm gauze", "1.5cm gauze"),
            ("Gauze\n1. 5cm", "Gauze\n1.5cm"),
        ]:
            with self.subTest(text=text):
                self.assertNotEqual(normalize_item_text(text), normalize_item_text(joined))

    def test_distinct_sizes_sterility_and_pack_counts_stay_distinct(self):
        requested = item_identity("Non sterile gauze 7.5cm 100 pcs")
        for other in ["Non sterile gauze 5cm 100 pcs", "Non sterile gauze 75cm 100 pcs",
                      "Sterile gauze 7. 5cm 100 pcs", "Non sterile gauze 7. 5cm 50 pcs"]:
            with self.subTest(other=other):
                self.assertFalse(identities_compatible(requested, item_identity(other)))


@override_settings(QUOTATION_COMPANY_PRICE_AUTOFILL_ENABLED=True)
class SpacedDimensionWorkflowTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="dimension-reviewer", is_staff=True)
        self.client.force_authenticate(self.staff)
        self.company = Company.objects.create(name="Intermass")
        self.product = Product.objects.create(name="GAUZE SWAB 7. 5CM", pack_size="PKT", price=1)
        self.alias = ProductAlias.objects.create(company=self.company, product=self.product, alias="Gauze Swab 7.5cm")
        # This was the misleading first suggestion before the saved alias could be read.
        Product.objects.create(name="Gauze Swab 7.5 x 7.5cm", pack_size="each", price=1)
        self.quote = Quotation.objects.create(company=self.company, created_by=self.staff)

    def test_inquiry_and_creation_review_choose_the_saved_company_product(self):
        row = {"raw_name": self.alias.alias, "unit": "PKT"}
        apply_match_to_preview_line(row, self.company)
        self.assertEqual(row["matched_product"], self.product.pk)
        self.assertEqual(row["match_method"], "company_alias")
        self.assertEqual(row["match_status"], "confirmed")
        with patch("quotations.ai_parsing.get_ai_parse_provider") as provider:
            reviewed = review_creation_rows([{"id": 1, "name": self.alias.alias, "unit": "PKT"}], self.company)
        provider.assert_not_called()
        self.assertEqual(reviewed[0]["matched_product_id"], self.product.pk)
        self.assertEqual(reviewed[0]["ai_status"], "existing_match")

    def test_existing_draft_bulk_creation_reuses_product_without_another_prompt(self):
        line = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot=self.alias.alias,
            unit="PKT", unit_price=Decimal("9.00"), match_status="unresolved")
        product_count = Product.objects.count()
        response = self.client.post(reverse("quotation-bulk-create-products-for-lines", args=[self.quote.pk]),
                                    {"line_ids": [line.pk]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["confirmation_required"], [])
        self.assertEqual(response.data["created_products"], 0)
        self.assertEqual(response.data["reused_products"], 1)
        line.refresh_from_db()
        self.alias.refresh_from_db()
        self.product.refresh_from_db()
        self.assertEqual(line.product_id, self.product.pk)
        self.assertEqual(line.unit_price, Decimal("9.00"))
        self.assertEqual(line.item_name_snapshot, self.alias.alias)
        self.assertEqual(line.match_status, "confirmed")
        self.assertEqual(self.alias.product_id, self.product.pk)
        self.assertEqual(self.product.name, "GAUZE SWAB 7. 5CM")
        self.assertEqual(Product.objects.count(), product_count)
        self.assertEqual(ProductAlias.objects.count(), 1)

    def test_history_price_is_eligible_after_size_normalization(self):
        old_quote = Quotation.objects.create(company=self.company, created_by=self.staff, status="sent")
        old_line = QuotationLine.objects.create(quotation=old_quote, product=self.product,
            item_name_snapshot=self.alias.alias, unit="PKT", unit_price=10, match_status="confirmed")
        source = CompanyPriceHistory.objects.create(company=self.company, product=self.product,
            quotation=old_quote, quotation_line=old_line, unit="PKT", unit_price=10)
        result = recommend_price(self.quote, self.product, "PKT", source_wording=self.alias.alias)
        self.assertTrue(result["eligible"], result)
        self.assertEqual(result["history_id"], source.pk)
        self.assertEqual(Decimal(result["amount"]), Decimal("10.00"))
        self.assertFalse(recommend_price(self.quote, self.product, "each", source_wording=self.alias.alias)["eligible"])
        other_quote = Quotation.objects.create(company=Company.objects.create(name="Other customer"), created_by=self.staff)
        self.assertFalse(recommend_price(other_quote, self.product, "PKT", source_wording=self.alias.alias)["eligible"])

    def test_company_links_remain_independent(self):
        other_company = Company.objects.create(name="Other customer")
        other_product = Product.objects.create(name="Gauze Swab 7.5cm", pack_size="BOX", price=1)
        ProductAlias.objects.create(company=other_company, product=other_product, alias=self.alias.alias)
        for company, expected in [(self.company, self.product), (other_company, other_product)]:
            with self.subTest(company=company.name):
                row = {"raw_name": self.alias.alias}
                apply_match_to_preview_line(row, company)
                self.assertEqual(row["matched_product"], expected.pk)
