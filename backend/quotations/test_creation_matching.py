from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from rest_framework.test import APITestCase

from api.models import Product
from .creation_matching import review_creation_rows
from .matching import create_or_reuse_product


class CreationMatchingTests(APITestCase):
    def setUp(self):
        self.client.force_authenticate(User.objects.create_user(username="matcher", is_staff=True))
        self.product = Product.objects.create(name="Digital Thermometer", price=12)

    def review(self, rows, response):
        with patch("quotations.ai_parsing.settings_ai_status", return_value={"status": "ai_available"}), \
             patch("quotations.ai_parsing.get_ai_parse_availability", return_value={"provider": "openai", "text_model": "test"}), \
             patch("quotations.ai_parsing.get_ai_parse_provider") as provider:
            provider.return_value.clean_rows.return_value = ({"rows": response}, {})
            result = review_creation_rows(rows)
            return result, provider.return_value.clean_rows.call_count

    def proposal(self, row=0, product=None, **kwargs):
        return {"row": row, "standard_name": "Digital Thermometre", "decision": "same_product",
                "product_id": (product or self.product).pk, "confidence": 0.99,
                "reason": "Same thermometer; spelling differs.", **kwargs}

    def test_second_pass_reuses_typo_match_without_creating_a_duplicate(self):
        result, calls = self.review([{"id": 91, "name": "Digital Thermometre"}], [self.proposal()])
        self.assertEqual(calls, 1)
        self.assertEqual(result[0]["ai_status"], "ai_matched")
        resolution = create_or_reuse_product(name=result[0]["standard_name"])
        self.assertEqual(resolution.product, self.product)
        self.assertFalse(resolution.created)
        self.assertEqual(Product.objects.count(), 1)

    def test_twenty_rows_use_one_model_request(self):
        result, calls = self.review([{"id": i, "name": "Digital Thermometre"} for i in range(20)],
                                   [self.proposal(row=i) for i in range(20)])
        self.assertEqual(calls, 1)
        self.assertTrue(all(r["matched_product_id"] == self.product.pk for r in result))

    def test_uncertain_review_still_allows_staff_override(self):
        result, _ = self.review([{"id": 1, "name": "Digital Thermometre"}],
                               [self.proposal(decision="uncertain", confidence=0.7)])
        self.assertIsNone(result[0]["matched_product_id"])
        warning = create_or_reuse_product(name=result[0]["standard_name"])
        self.assertTrue(warning.requires_confirmation)
        override = create_or_reuse_product(name=result[0]["standard_name"], confirm_create=True)
        self.assertTrue(override.created)

    def test_invented_candidate_or_duplicate_result_cannot_trigger_reuse(self):
        for proposals in ([self.proposal(product_id=999999)], [self.proposal(), self.proposal()]):
            with self.subTest(proposals=proposals):
                result, _ = self.review([{"id": 1, "name": "Digital Thermometre"}], proposals)
                self.assertIsNone(result[0]["matched_product_id"])
                self.assertEqual(result[0]["standard_name"], "Digital Thermometre")

    def test_missing_size_or_strength_never_becomes_an_ai_match(self):
        for requested, name in [("Nitrile glovs", "Nitrile gloves medium"),
                                ("Paracetmol tablets", "Paracetamol 500mg tablets"),
                                ("Glucometer", "Glucometer strips")]:
            with self.subTest(name=name):
                product = Product.objects.create(name=name, price=1)
                result, _ = self.review([{"id": 1, "name": requested}], [self.proposal(product=product, standard_name=name)])
                self.assertIsNone(result[0]["matched_product_id"])
                self.assertEqual(result[0]["standard_name"], requested)

    def test_api_bounds_batch_and_rejects_duplicate_ids(self):
        url = reverse("quotation-item-creation-review")
        for rows in ([], [{"id": i, "name": "A"} for i in range(21)],
                     [{"id": 1, "name": "A"}, {"id": "1", "name": "B"}]):
            self.assertEqual(self.client.post(url, {"rows": rows}, format="json").status_code, 400)
