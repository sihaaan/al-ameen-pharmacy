from decimal import Decimal
from unittest.mock import patch
from unittest import skipUnless
from queue import Queue
from threading import Barrier, Thread

from django.contrib.auth.models import User
from django.test import TransactionTestCase, override_settings
from django.db import connection, close_old_connections, connections
from django.urls import reverse
from rest_framework.test import APITestCase, APIClient

from api.models import Product
from .creation_matching import review_creation_rows
from .matching import create_or_reuse_product, suggest_product_for_text
from .models import Company, Inquiry, InquiryLine, ProductAlias, Quotation, QuotationAuditLog, QuotationLine
from .quotation_email_delivery import quotation_review_fingerprint


@override_settings(QUOTATION_COMPANY_PRICE_AUTOFILL_ENABLED=True)
class SavedCompanyMatchTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="saved-match-reviewer", is_staff=True)
        self.client.force_authenticate(self.staff)
        self.company = Company.objects.create(name="Customer A")
        self.previous = Product.objects.create(name="Gauze Swab 5cm", pack_size="PKT", price=1)
        self.replacement = Product.objects.create(name="Gauze Swab 7.5cm", pack_size="PKT", price=1)
        self.wording = "Gauze Swab 7.5cm"
        self.alias = ProductAlias.objects.create(company=self.company, product=self.previous, alias=self.wording)
        self.quote = Quotation.objects.create(company=self.company, created_by=self.staff)
        self.line = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot=self.wording,
            unit="PKT", unit_price=Decimal("9"), quantity=3, match_status="unresolved")

    def review(self):
        return suggest_product_for_text(self.wording, self.company).saved_match_review

    def resolve(self, product=None, resolution="confirm", token=None, **extra):
        self.quote.refresh_from_db()
        return self.client.post(reverse("quotation-resolve-saved-match", args=[self.quote.pk]), {
            "line_id": self.line.pk, "product": (product or self.previous).pk,
            "resolution": resolution, "review_token": token if token is not None else self.review()["token"],
            "quotation_review_fingerprint": quotation_review_fingerprint(self.quote), **extra,
        }, format="json")

    def test_existing_link_stays_visible_instead_of_falling_through_to_new_ai_match(self):
        match = suggest_product_for_text(self.wording, self.company)
        self.assertIsNone(match.product)
        self.assertEqual(match.method, "saved_alias_review")
        self.assertEqual([c.product.pk for c in match.candidates], [self.previous.pk])
        self.assertIn("Size differs", match.reason)
        self.assertIn("5cm", match.reason)
        with patch("quotations.ai_parsing.get_ai_parse_provider") as provider:
            result = review_creation_rows([{"id": self.line.pk, "name": self.wording}], self.company)
        provider.assert_not_called()
        self.assertEqual(result[0]["ai_status"], "needs_review")
        blocked = create_or_reuse_product(name=self.wording, company=self.company, confirm_create=True)
        self.assertTrue(blocked.creation_blocked)
        self.assertIsNone(blocked.product)

    def test_confirmation_survives_the_next_inquiry_and_draft_without_changing_price(self):
        result = self.resolve()
        self.assertEqual(result.status_code, 200, result.data)
        self.line.refresh_from_db()
        self.alias.refresh_from_db()
        self.assertEqual(self.line.product_id, self.previous.pk)
        self.assertEqual(self.line.unit_price, Decimal("9"))
        self.assertEqual(self.line.quantity, 3)
        self.assertEqual(self.alias.identity_confirmation["actor_id"], self.staff.pk)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)
        second_line = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot=self.wording,
            unit="PKT", unit_price=11, match_status="unresolved")
        created = self.client.post(reverse("quotation-bulk-create-products-for-lines", args=[self.quote.pk]),
                                   {"line_ids": [second_line.pk]}, format="json")
        self.assertEqual(created.status_code, 200, created.data)
        self.assertEqual(created.data["confirmation_required"], [])
        second_line.refresh_from_db()
        self.assertEqual(second_line.product_id, self.previous.pk)
        self.assertEqual(second_line.unit_price, 11)
        self.assertEqual(QuotationAuditLog.objects.filter(quotation=self.quote, changes__has_key="saved_match_review").count(), 1)

    def test_company_correction_does_not_change_other_companies_or_old_quotes(self):
        other = Company.objects.create(name="Customer B")
        other_alias = ProductAlias.objects.create(company=other, product=self.previous, alias=self.wording)
        global_alias = ProductAlias.objects.create(product=self.previous, alias=self.wording)
        old_quote = Quotation.objects.create(company=self.company, created_by=self.staff, status="sent")
        old_line = QuotationLine.objects.create(quotation=old_quote, product=self.previous, item_name_snapshot=self.wording, unit_price=10)
        result = self.resolve(self.replacement, "correct")
        self.assertEqual(result.status_code, 200, result.data)
        self.alias.refresh_from_db(); other_alias.refresh_from_db(); global_alias.refresh_from_db(); old_line.refresh_from_db()
        self.assertEqual(self.alias.product_id, self.replacement.pk)
        self.assertEqual(other_alias.product_id, self.previous.pk)
        self.assertEqual(global_alias.product_id, self.previous.pk)
        self.assertEqual(old_line.product_id, self.previous.pk)
        self.assertEqual(old_line.unit_price, 10)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.replacement)
        self.assertIsNone(suggest_product_for_text(self.wording, other).product)

    def test_changed_product_details_expire_confirmation_but_formatting_does_not(self):
        self.assertEqual(self.resolve().status_code, 200)
        self.previous.name = "GAUZE SWAB 5CM"
        self.previous.pack_size = "Packets"
        self.previous.save()
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)
        self.previous.pack_size = "100 pieces"
        self.previous.save()
        self.assertEqual(suggest_product_for_text(self.wording, self.company).method, "saved_alias_review")

    def test_stale_alias_review_cannot_overwrite_a_concurrent_correction(self):
        token = self.review()["token"]
        self.alias.product = self.replacement
        self.alias.save()
        response = self.resolve(token=token)
        self.assertEqual(response.status_code, 409, response.data)
        self.assertEqual(response.data["code"], "saved_product_match_review")
        self.assertNotEqual(response.data["saved_match_review"]["token"], token)
        self.line.refresh_from_db(); self.alias.refresh_from_db()
        self.assertIsNone(self.line.product_id)
        self.assertEqual(self.alias.product_id, self.replacement.pk)

    def test_stale_product_review_is_rejected_before_recording_confirmation(self):
        token = self.review()["token"]
        self.previous.name = "Gauze Swab 10cm"
        self.previous.save()
        response = self.resolve(token=token)
        self.assertEqual(response.status_code, 409, response.data)
        self.alias.refresh_from_db()
        self.assertFalse(self.alias.identity_confirmation)

    def test_stale_quote_or_foreign_line_cannot_change_alias(self):
        other_quote = Quotation.objects.create(company=self.company, created_by=self.staff)
        other_line = QuotationLine.objects.create(quotation=other_quote, item_name_snapshot=self.wording)
        for extra in [{"quotation_review_fingerprint": "stale"}, {"line_id": other_line.pk}]:
            with self.subTest(extra=extra):
                response = self.resolve(**extra)
                self.assertIn(response.status_code, (400, 409))
                self.alias.refresh_from_db()
                self.assertFalse(self.alias.identity_confirmation)

    def test_compatible_but_different_manual_selection_returns_actionable_conflict(self):
        self.previous.name = "Customer Gauze"
        self.previous.save()
        response = self.client.post(reverse("quotation-bulk-update-lines", args=[self.quote.pk]),
            {"lines": [{"id": self.line.pk, "product": self.replacement.pk, "match_status": "confirmed"}]}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "saved_product_match_review")
        self.assertEqual(response.data["line_id"], self.line.pk)
        self.assertEqual(response.data["selected_product_id"], self.replacement.pk)
        self.assertEqual(self.resolve(self.replacement, "correct", token=response.data["saved_match_review"]["token"]).status_code, 200)

    def test_conflicting_equivalent_aliases_are_resolved_together(self):
        variant = ProductAlias.objects.create(company=self.company, product=self.replacement, alias="Gauze-Swab 7.5cm")
        self.assertEqual(suggest_product_for_text(self.wording, self.company).method, "alias_conflict")
        response = self.resolve(self.replacement, "confirm")
        self.assertEqual(response.status_code, 200, response.data)
        self.alias.refresh_from_db(); variant.refresh_from_db()
        self.assertEqual(self.alias.product_id, self.replacement.pk)
        self.assertEqual(variant.product_id, self.replacement.pk)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.replacement)

    def test_catalogue_alias_confirmation_creates_only_a_company_override(self):
        self.alias.company = None
        self.alias.save()
        response = self.resolve()
        self.assertEqual(response.status_code, 200, response.data)
        self.alias.refresh_from_db()
        self.assertIsNone(self.alias.company_id)
        self.assertFalse(self.alias.identity_confirmation)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)
        self.assertIsNone(suggest_product_for_text(self.wording).product)

    def test_archived_saved_product_remains_visible_and_requires_replacement(self):
        self.previous.status = "archived"
        self.previous.save()
        review = self.review()
        self.assertFalse(review["products"][0]["can_confirm"])
        self.assertIn("archived", " ".join(review["differences"]))
        self.assertEqual(self.resolve().status_code, 400)
        self.assertEqual(self.resolve(self.replacement, "correct").status_code, 200)

    def test_provisional_exact_name_does_not_bypass_saved_conflict(self):
        self.replacement.identity_review_state = "provisional"
        self.replacement.save()
        result = create_or_reuse_product(name=self.wording, company=self.company)
        self.assertTrue(result.creation_blocked)
        self.assertIsNone(result.product)

    def test_existing_inquiry_selection_remains_an_explicit_confirmation(self):
        inquiry = Inquiry.objects.create(company=self.company, created_by=self.staff, subject="Customer request")
        line = InquiryLine.objects.create(inquiry=inquiry, raw_name=self.wording)
        response = self.client.patch(reverse("quotation-inquiry-line-detail", args=[line.pk]),
            {"matched_product": self.previous.pk, "match_status": "confirmed"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)

    def test_spelling_variant_cannot_bypass_review_and_reuses_the_confirmed_decision(self):
        self.line.item_name_snapshot = "Gauze-Swab 7.5cm"
        self.line.save()
        response = self.client.post(reverse("quotation-bulk-update-lines", args=[self.quote.pk]),
            {"lines": [{"id": self.line.pk, "product": self.previous.pk, "match_status": "confirmed"}]}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(response.data["code"], "saved_product_match_review")
        self.assertEqual(self.resolve(token=response.data["saved_match_review"]["token"]).status_code, 200)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)
        self.assertEqual(suggest_product_for_text(self.line.item_name_snapshot, self.company).product, self.previous)

    def test_saved_provisional_company_match_does_not_add_an_owner_approval_requirement(self):
        self.previous.name = self.wording
        self.previous.identity_review_state = "provisional"
        self.previous.save()
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.previous)
        response = self.client.post(reverse("quotation-bulk-create-products-for-lines", args=[self.quote.pk]),
            {"line_ids": [self.line.pk]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["confirmation_required"], [])
        self.previous.refresh_from_db()
        self.assertEqual(self.previous.identity_review_state, "provisional")

    def test_correcting_to_a_provisional_product_does_not_verify_it_globally(self):
        self.replacement.identity_review_state = "provisional"
        self.replacement.save()
        response = self.resolve(self.replacement, "correct")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.replacement)
        self.replacement.refresh_from_db()
        self.assertEqual(self.replacement.identity_review_state, "provisional")

    def test_verified_canonical_duplicate_link_does_not_raise_alias_conflict(self):
        self.previous.canonical_product = self.replacement
        self.previous.save()
        self.assertEqual(suggest_product_for_text(self.wording, self.company).product, self.replacement)
        response = self.client.post(reverse("quotation-bulk-create-products-for-lines", args=[self.quote.pk]),
            {"line_ids": [self.line.pk]}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["confirmation_required"], [])

    def test_nonstaff_cannot_confirm_or_correct_company_matches(self):
        self.client.force_authenticate(User.objects.create_user(username="customer"))
        self.assertEqual(self.resolve().status_code, 403)
        self.alias.refresh_from_db()
        self.assertFalse(self.alias.identity_confirmation)


@skipUnless(connection.vendor == "postgresql", "Requires PostgreSQL row locks")
class SavedMatchConcurrencyTests(TransactionTestCase):
    def test_two_quotations_cannot_overwrite_the_same_reviewed_company_link(self):
        staff = User.objects.create_user(username="concurrent-match-reviewer", is_staff=True)
        company = Company.objects.create(name="Concurrent customer")
        previous = Product.objects.create(name="Gauze 5cm", price=1)
        replacement = Product.objects.create(name="Gauze 7.5cm", price=1)
        alias = ProductAlias.objects.create(company=company, product=previous, alias="Gauze 7.5cm")
        token = suggest_product_for_text(alias.alias, company).saved_match_review["token"]
        requests = []
        for target in (previous, replacement):
            quote = Quotation.objects.create(company=company, created_by=staff)
            line = QuotationLine.objects.create(quotation=quote, item_name_snapshot=alias.alias, unit_price=9)
            requests.append((quote.pk, {"line_id": line.pk, "product": target.pk, "resolution": "correct",
                "review_token": token, "quotation_review_fingerprint": quotation_review_fingerprint(quote)}))
        barrier, results = Barrier(2), Queue()

        def resolve(quote_id, payload):
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=staff.pk))
                barrier.wait(timeout=10)
                response = client.post(reverse("quotation-resolve-saved-match", args=[quote_id]), payload, format="json")
                results.put((response.status_code, str(response.data)[:500]))
            except Exception as exc:
                results.put((500, repr(exc)))
            finally:
                connections.close_all()

        threads = [Thread(target=resolve, args=request, daemon=True) for request in requests]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads), "Saved-match resolution deadlocked")
        outcomes = [results.get_nowait() for _ in threads]
        self.assertEqual(sorted(code for code, _ in outcomes), [200, 409], outcomes)
        self.assertEqual(QuotationLine.objects.exclude(product=None).count(), 1)
        alias.refresh_from_db()
        self.assertEqual(alias.identity_confirmation["actor_id"], staff.pk)
