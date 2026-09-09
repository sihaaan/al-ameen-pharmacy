from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader
from rest_framework.test import APIClient

from .models import Company, Quotation, QuotationLine, QuotationLPO, QuotationOutcomePOImport
from .pdf import accepted_order_document
from .services import update_quotation_outcome


class SharedOrderDocumentTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="order-staff", is_staff=True)
        self.company = Company.objects.create(name="Order document customer")
        self.quote = Quotation.objects.create(company=self.company, status="sent", subtotal=1020, vat_total=50, total=1070)
        self.line = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot="Wheelchair", quantity=10, unit="nos", unit_price=100, vat_rate=5, match_status="confirmed")
        self.other = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot="Not ordered thermometer", quantity=1, unit_price=20, match_status="confirmed")
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def parse(self):
        preview = {"lines": [{"item_name": "Wheelchair", "quantity": 6, "unit_price": 90, "unit": "nos"}], "meta": {"lpo_number": "LPO-123", "lpo_date": "2026-09-09"}, "warnings": [], "parse_method": "text"}
        suggestions = [{"quotation_line_id": self.line.pk, "accepted_quantity": "6", "accepted_unit_price": "90", "outcome_status": "quantity_changed"}]
        with patch("quotations.views.parse_text_preview", return_value=preview), patch("quotations.views.build_guarded_po_outcome_suggestions", return_value=(preview, suggestions, [], [self.other.pk])):
            response = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"text": "LPO-123 Wheelchair 6 nos 90", "use_ai": False}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def approve(self, lpo_id=None, po_import_id=None):
        payload = {"line_updates": [{"id": self.line.pk, "outcome_status": "quantity_changed", "accepted_quantity": "6", "accepted_unit_price": "90"}, {"id": self.other.pk, "outcome_status": "rejected"}]}
        if lpo_id:
            payload["lpo_id"] = lpo_id
        if po_import_id:
            payload.update(po_import_id=po_import_id, applied_po_line_ids=[self.line.pk])
        return self.client.patch(reverse("quotation-outcome", args=[self.quote.pk]), payload, format="json")

    def test_upload_once_review_approve_proforma_and_delivery_share_the_order(self):
        parsed = self.parse()
        lpo_id = parsed["canonical_lpo"]["id"]
        self.assertEqual(QuotationLPO.objects.count(), 1)
        repeat = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"lpo_id": lpo_id}, format="json")
        self.assertEqual(repeat.status_code, 200, repeat.data)
        self.assertEqual(repeat.data["id"], parsed["id"])
        self.assertEqual(QuotationOutcomePOImport.objects.count(), 1)
        approved = self.approve(lpo_id, parsed["id"])
        self.assertEqual(approved.status_code, 200, approved.data)
        lpo = QuotationLPO.objects.get(pk=lpo_id)
        self.assertEqual(lpo.status, "confirmed")
        self.assertEqual(lpo.parsed_meta["applied_outcome_line_ids"], [self.line.pk])
        self.assertEqual(approved.data["lpos"][0]["id"], lpo_id)
        pdf = self.client.get(reverse("quotation-proforma-pdf", args=[self.quote.pk]), {"basis": "accepted_order", "lpo": lpo_id})
        self.assertEqual(pdf.status_code, 200)
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages)
        for value in ["Wheelchair", "LPO-123", "567.00", "90.00"]:
            self.assertIn(value, text)
        self.assertNotIn("Not ordered thermometer", text)
        self.quote.refresh_from_db(); self.line.refresh_from_db()
        self.assertEqual((self.quote.status, self.quote.total, self.line.quantity, self.line.unit_price), ("sent", Decimal("1070"), Decimal("10"), Decimal("100")))
        note = self.client.post(reverse("quotation-delivery-note-list"), {"quotation": self.quote.pk, "lines": [{"quotation_line": self.line.pk, "quantity": "6"}]}, format="json")
        self.assertEqual(note.status_code, 201, note.data)
        self.assertEqual(Decimal(note.data["lines"][0]["quantity"]), 6)

    def test_unapproved_or_foreign_lpo_cannot_supply_order_documents(self):
        parsed = self.parse()
        pdf_url = reverse("quotation-proforma-pdf", args=[self.quote.pk])
        response = self.client.get(pdf_url, {"basis": "accepted_order", "lpo": parsed["canonical_lpo"]["id"]})
        self.assertEqual(response.status_code, 400)
        other_quote = Quotation.objects.create(company=self.company, status="sent")
        foreign = QuotationLPO.objects.create(quotation=other_quote)
        self.assertEqual(self.approve(foreign.pk).status_code, 400)
        self.line.refresh_from_db()
        self.assertEqual(self.line.outcome_status, "pending")
        self.assertEqual(self.client.get(pdf_url, {"basis": "accepted_order", "lpo": foreign.pk}).status_code, 404)
        invalid = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"lpo_id": "invalid"}, format="json")
        self.assertEqual(invalid.status_code, 400)

    def test_existing_legacy_lpo_can_be_reviewed_without_uploading_again(self):
        lpo = QuotationLPO.objects.create(quotation=self.quote, lpo_number="OLD-PO", parsed_rows=[{"item_name": "Wheelchair", "quantity": 6, "unit_price": 90, "unit": "nos"}])
        with patch("quotations.views.clean_preview_with_ai") as ai:
            response = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"lpo_id": lpo.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["canonical_lpo"]["id"], lpo.pk)
        self.assertEqual(QuotationLPO.objects.count(), 1)
        ai.assert_not_called()

    def test_proforma_keeps_the_existing_proportional_discount_rule(self):
        self.quote.discount_amount = Decimal("10.70")
        self.quote.total = Decimal("1059.30")
        self.quote.save()
        self.assertEqual(self.approve().status_code, 200)
        self.quote.refresh_from_db()
        document, lines = accepted_order_document(self.quote)
        self.assertEqual(len(lines), 1)
        self.assertEqual(document.subtotal, Decimal("540.00"))
        self.assertEqual(document.vat_total, Decimal("27.00"))
        self.assertEqual(document.discount_amount, Decimal("5.67"))
        self.assertEqual(document.total, Decimal("561.33"))

    def test_confirmed_legacy_lpo_source_is_preserved_when_opened_for_review(self):
        lpo = QuotationLPO.objects.create(quotation=self.quote, lpo_number="CONFIRMED-PO", status="confirmed", parsed_meta={"source_note": "Retain original"}, parsed_rows=[{"item_name": "Wheelchair", "quantity": 6, "unit_price": 90}])
        response = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"lpo_id": lpo.pk}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        lpo.refresh_from_db()
        self.assertEqual(lpo.parsed_meta, {"source_note": "Retain original"})
        self.assertEqual(lpo.status, "confirmed")
        again = self.client.post(reverse("quotation-parse-outcome-po", args=[self.quote.pk]), {"lpo_id": lpo.pk}, format="json")
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.data["id"], response.data["id"])

    def test_acceptance_rejects_an_lpo_from_a_different_parsed_source(self):
        parsed = self.parse()
        wrong_lpo = QuotationLPO.objects.create(quotation=self.quote, lpo_number="WRONG-PO")
        response = self.approve(wrong_lpo.pk, parsed["id"])
        self.assertEqual(response.status_code, 400)
        self.line.refresh_from_db()
        self.assertEqual(self.line.outcome_status, "pending")

    def test_selected_lpo_only_includes_its_confirmed_items(self):
        self.approve()
        update_quotation_outcome(self.quote, {"line_updates": [{"id": self.other.pk, "outcome_status": "accepted", "accepted_quantity": "1", "accepted_unit_price": "20"}]}, self.staff)
        lpo = QuotationLPO.objects.create(quotation=self.quote, status="confirmed", parsed_meta={"applied_outcome_line_ids": [self.line.pk]})
        document, lines = accepted_order_document(self.quote, lpo)
        self.assertEqual([line.pk for line in lines], [self.line.pk])
        self.assertEqual(document.total, Decimal("567.00"))
