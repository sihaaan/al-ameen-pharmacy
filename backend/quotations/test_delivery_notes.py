from datetime import timedelta
from decimal import Decimal
from io import BytesIO
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models.deletion import ProtectedError
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from pypdf import PdfReader
from rest_framework.test import APIClient

from .delivery import order_progress, issue_delivery_note
from .models import Company, DeliveryNote, DeliveryNoteLine, Quotation, QuotationAuditLog, QuotationLine, QuotationSettings
from .services import transition_quotation_status, update_quotation_outcome


class DeliveryNoteWorkflowTests(TestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="delivery-staff", is_staff=True)
        self.company = Company.objects.create(name="Delivery customer", billing_address="Dubai\nWarehouse 2", trn="100000000000003")
        self.quote = Quotation.objects.create(
            company=self.company, status=Quotation.STATUS_FINALIZED, outcome_status=Quotation.OUTCOME_WON,
        )
        self.line = QuotationLine.objects.create(
            quotation=self.quote, item_name_snapshot="Examination gloves", quantity=10, unit="Box",
            unit_price=Decimal("83.217"), match_status=QuotationLine.MATCH_CONFIRMED,
            outcome_status=QuotationLine.OUTCOME_ACCEPTED, accepted_quantity=10,
            accepted_unit_price=Decimal("83.217"),
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def url(self, action, pk=None):
        return reverse(f"quotation-delivery-note-{action}", kwargs={"pk": pk} if pk else None)

    def draft(self, quantity="10", **overrides):
        payload = {"quotation": self.quote.pk, "lines": [{"quotation_line": self.line.pk, "quantity": quantity}]}
        payload.update(overrides)
        response = self.client.post(self.url("list"), payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def issue(self, note):
        response = self.client.post(self.url("issue", note["id"]), {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def receive(self, note, quantity=None):
        data = {"received_by": "Warehouse receiver", "received_date": str(timezone.localdate())}
        if quantity is not None:
            data["lines"] = [{"id": line["id"], "received_quantity": quantity} for line in note["lines"]]
        response = self.client.post(self.url("confirm-receipt", note["id"]), data, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_draft_and_pdf_do_not_issue_or_complete_an_order(self):
        note = self.draft()
        audit_count = QuotationAuditLog.objects.count()
        response = self.client.get(self.url("pdf", note["id"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Cache-Control"], "private, no-store")
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        for expected in ["DELIVERY NOTE", "Examination gloves", "DRAFT", "Customer", "Acknowledgement of receipt"]:
            self.assertIn(expected, text)
        for excluded in ["83.217", "Grand Total", "Unit price", "TAX INVOICE"]:
            self.assertNotIn(excluded, text)
        self.assertEqual(DeliveryNote.objects.get(pk=note["id"]).status, "draft")
        self.assertEqual(QuotationAuditLog.objects.count(), audit_count)
        self.assertEqual(order_progress(self.quote)["delivery_status"], "accepted")

    def test_partial_receipt_leaves_remaining_available_and_second_delivery_completes(self):
        note = self.issue(self.draft())
        self.assertEqual(order_progress(self.quote)["delivery_status"], "in_progress")
        self.receive(note, "6")
        progress = order_progress(self.quote)
        self.assertEqual(progress["delivery_status"], "partially_delivered")
        self.assertEqual(Decimal(progress["lines"][0]["available_quantity"]), 4)
        self.receive(self.issue(self.draft("4")))
        self.assertEqual(order_progress(self.quote)["delivery_status"], "completed")

    def test_issued_notes_reserve_quantities_and_prevent_duplicate_dispatch(self):
        first = self.draft("7")
        second = self.draft("7")
        self.issue(first)
        response = self.client.post(self.url("issue", second["id"]), {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("only 3", str(response.data))
        self.assertEqual(DeliveryNote.objects.get(pk=second["id"]).status, "draft")

    def test_issue_and_receipt_retries_do_not_double_count(self):
        note = self.issue(self.draft())
        self.issue(note)
        count = QuotationAuditLog.objects.count()
        self.receive(note)
        self.receive(note)
        self.assertEqual(QuotationAuditLog.objects.count(), count + 1)
        self.assertEqual(Decimal(order_progress(self.quote)["lines"][0]["delivered_quantity"]), 10)

    def test_issued_note_is_locked_and_status_cannot_be_written_directly(self):
        note = self.draft(status="delivered", received_by="Forged")
        self.assertEqual(note["status"], "draft")
        self.assertEqual(note["received_by"], "")
        self.issue(note)
        response = self.client.patch(self.url("detail", note["id"]), {"invoice_number": "changed"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.delete(self.url("detail", note["id"])).status_code, 405)

    def test_cross_quotation_customer_and_unaccepted_lines_are_rejected(self):
        other = Company.objects.create(name="Other customer")
        response = self.client.post(self.url("list"), {
            "quotation": self.quote.pk, "company": other.pk,
            "lines": [{"quotation_line": self.line.pk, "quantity": "1"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        other_quote = Quotation.objects.create(company=other, status="finalized")
        response = self.client.post(self.url("list"), {
            "quotation": other_quote.pk, "lines": [{"quotation_line": self.line.pk, "quantity": "1"}],
        }, format="json")
        self.assertEqual(response.status_code, 400)
        self.line.outcome_status = QuotationLine.OUTCOME_PENDING
        self.line.save(update_fields=["outcome_status"])
        response = self.client.post(self.url("list"), {"quotation": self.quote.pk, "lines": [{"quotation_line": self.line.pk, "quantity": "1"}]}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_invalid_and_duplicate_quantities_are_rejected(self):
        for quantity in ["0", "-1", "NaN", "0.0001", "1000000000"]:
            with self.subTest(quantity=quantity):
                response = self.client.post(self.url("list"), {"quotation": self.quote.pk, "lines": [{"quotation_line": self.line.pk, "quantity": quantity}]}, format="json")
                self.assertEqual(response.status_code, 400)
        row = {"quotation_line": self.line.pk, "quantity": "1"}
        response = self.client.post(self.url("list"), {"quotation": self.quote.pk, "lines": [row, row]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(DeliveryNote.objects.exists())

    def test_receipt_requires_receiver_and_valid_quantities_and_date(self):
        note = self.issue(self.draft())
        endpoint = self.url("confirm-receipt", note["id"])
        for payload in [
            {"received_date": str(timezone.localdate())},
            {"received_by": "Receiver", "received_date": str(timezone.localdate() + timedelta(days=1))},
            {"received_by": "Receiver", "received_date": str(timezone.localdate()), "lines": []},
            {"received_by": "Receiver", "received_date": str(timezone.localdate()), "lines": [{"id": note["lines"][0]["id"], "received_quantity": "11"}]},
            {"received_by": "Receiver", "received_date": str(timezone.localdate()), "lines": [{"id": note["lines"][0]["id"], "received_quantity": "0"}]},
        ]:
            self.assertEqual(self.client.post(endpoint, payload, format="json").status_code, 400)
        self.assertIsNone(DeliveryNoteLine.objects.get(delivery_note_id=note["id"]).received_quantity)

    def test_cancelled_receipt_reopens_order_and_keeps_evidence(self):
        note = self.receive(self.issue(self.draft()))
        self.assertEqual(order_progress(self.quote)["delivery_status"], "completed")
        endpoint = self.url("cancel", note["id"])
        self.assertEqual(self.client.post(endpoint, {}, format="json").status_code, 400)
        response = self.client.post(endpoint, {"reason": "Wrong customer's receipt was recorded"}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["received_by"], "Warehouse receiver")
        self.assertEqual(order_progress(self.quote)["delivery_status"], "accepted")
        self.assertTrue(QuotationAuditLog.objects.filter(message__contains="Cancelled delivery note").exists())

    def test_accepted_quantity_cannot_be_reduced_below_active_delivery(self):
        self.issue(self.draft("7"))
        with self.assertRaises(ValidationError):
            update_quotation_outcome(self.quote, {"line_updates": [{"id": self.line.pk, "accepted_quantity": "5"}]}, self.staff)
        self.line.refresh_from_db()
        self.assertEqual(self.line.accepted_quantity, 10)
        with self.assertRaises(ValidationError):
            transition_quotation_status(self.quote, self.staff, Quotation.STATUS_CANCELLED)

    def test_standalone_note_does_not_invent_an_accepted_quotation(self):
        before = Quotation.objects.count()
        note = self.draft(quotation=None, company=self.company.pk, invoice_number="594173", lpo_number="LPO111370", lines=[{"item_name": "Cold packs", "unit": "Packet", "quantity": "5"}])
        self.receive(self.issue(note))
        self.assertEqual(Quotation.objects.count(), before)
        self.assertIsNone(DeliveryNote.objects.get(pk=note["id"]).quotation_id)

    def test_order_filters_summary_and_completion_require_every_accepted_line(self):
        QuotationLine.objects.create(
            quotation=self.quote, item_name_snapshot="Bandage", quantity=2, unit_price=1,
            match_status="confirmed", outcome_status="accepted", accepted_quantity=2,
        )
        self.receive(self.issue(self.draft()))
        response = self.client.get(reverse("quotation-delivery-order-list"), {"status": "partially_delivered"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["summary"]["partially_delivered"], 1)
        self.assertEqual(response.data["results"][0]["completed_line_count"], 1)
        self.assertEqual(self.client.get(reverse("quotation-delivery-order-list"), {"status": "completed"}).data["count"], 0)

    def test_customer_snapshot_and_source_history_are_preserved(self):
        note = self.draft()
        self.company.name = "Renamed customer"
        self.company.save()
        self.assertEqual(DeliveryNote.objects.get(pk=note["id"]).customer_name, "Delivery customer")
        with self.assertRaises(ProtectedError):
            self.quote.delete()

    def test_external_users_cannot_read_or_change_deliveries(self):
        note = self.draft()
        customer = get_user_model().objects.create_user(username="delivery-customer")
        self.client.force_authenticate(customer)
        for endpoint in [self.url("list"), self.url("detail", note["id"]), self.url("pdf", note["id"]), reverse("quotation-delivery-order-list")]:
            self.assertEqual(self.client.get(endpoint).status_code, 403)
        self.assertEqual(self.client.post(self.url("issue", note["id"]), {}, format="json").status_code, 403)

    def test_receipt_pdf_shows_actual_received_quantities(self):
        note = self.receive(self.issue(self.draft()), "6")
        response = self.client.get(self.url("pdf", note["id"]))
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        self.assertIn("Received", text)
        self.assertIn("Warehouse receiver", text)
        self.assertNotIn("DRAFT", text)

    def test_issued_and_received_references_can_be_corrected_without_changing_delivery(self):
        note = self.issue(self.draft(lpo_number="PO112"))
        line_ids = [line["id"] for line in note["lines"]]
        issued_at = note["issued_at"]
        for status in ("issued", "delivered"):
            if status == "delivered":
                self.receive(note)
            corrected = self.client.post(self.url("update-references", note["id"]), {
                "lpo_number": "PO112_112353" if status == "issued" else "PO112_112353-A",
                "invoice_number": "INV_001-2",
            }, format="json")
            self.assertEqual(corrected.status_code, 200, corrected.data)
            self.assertEqual(corrected.data["status"], status)
            self.assertEqual(corrected.data["issued_at"], issued_at)
            self.assertEqual([line["id"] for line in corrected.data["lines"]], line_ids)
            text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(
                self.client.get(self.url("pdf", note["id"])).content)).pages)
            self.assertIn(corrected.data["lpo_number"], text)
        log = QuotationAuditLog.objects.filter(message__startswith="Corrected references").order_by("id").first()
        self.assertEqual(log.changes["lpo_number"], {"before": "PO112", "after": "PO112_112353"})

    def test_reference_correction_rejects_quantity_customer_source_and_cancelled_changes(self):
        note = self.issue(self.draft())
        endpoint = self.url("update-references", note["id"])
        for payload in ({"lines": []}, {"company": self.company.pk}, {"delivery_date": "2026-09-01"},
                        {"quotation_reference": "WRONG"}, {"status": "draft"}, {}, {"lpo_number": "x" * 121}):
            self.assertEqual(self.client.post(endpoint, payload, format="json").status_code, 400, payload)
        self.client.post(self.url("cancel", note["id"]), {"reason": "Incorrect note"}, format="json")
        self.assertEqual(self.client.post(endpoint, {"lpo_number": "NEW"}, format="json").status_code, 400)
        self.client.force_authenticate(get_user_model().objects.create_user("reference-customer"))
        self.assertEqual(self.client.post(endpoint, {"lpo_number": "NEW"}, format="json").status_code, 403)

    def test_reference_retry_is_idempotent_and_standalone_quote_reference_is_editable(self):
        note = self.issue(self.draft(quotation=None, company=self.company.pk,
            lines=[{"item_name": "Bandage", "quantity": "2"}]))
        endpoint = self.url("update-references", note["id"])
        response = self.client.post(endpoint, {"quotation_reference": "QT_123-2"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["quotation_reference"], "QT_123-2")
        before = QuotationAuditLog.objects.count()
        self.client.post(endpoint, {"quotation_reference": "QT_123-2"}, format="json")
        self.assertEqual(QuotationAuditLog.objects.count(), before)

    def test_deferred_standalone_items_are_saved_in_one_linked_draft_on_issue(self):
        rows = [{"item_name": "Available bandage", "quantity": "2", "unit": "Box"},
                {"item_name": "Later cold packs", "quantity": "5", "unit": "Packet", "deliver_later": True}]
        note = self.draft(quotation=None, company=self.company.pk, lpo_number="PO112_112353", lines=rows)
        self.assertTrue(note["lines"][1]["deliver_later"])
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(
            self.client.get(self.url("pdf", note["id"])).content)).pages)
        self.assertIn("Available bandage", text)
        self.assertNotIn("Later cold packs", text)
        issued = self.issue(note)
        self.assertEqual(len(issued["lines"]), 1)
        self.assertEqual(len(issued["later_deliveries"]), 1)
        later = DeliveryNote.objects.get(pk=issued["later_deliveries"][0]["id"])
        self.assertEqual(later.status, "draft")
        self.assertEqual(later.continued_from_id, note["id"])
        self.assertEqual(later.lpo_number, "PO112_112353")
        self.assertEqual(later.lines.get().quantity, Decimal("5"))
        self.assertFalse(later.lines.get().deliver_later)
        self.issue(note)
        self.assertEqual(DeliveryNote.objects.count(), 2)
        self.assertEqual(DeliveryNoteLine.objects.count(), 2)
        self.receive(issued)
        later_data = self.client.get(self.url("detail", later.pk)).data
        self.assertEqual(later_data["continued_from_number"], issued["delivery_number"])
        self.receive(self.issue(later_data))

    def test_deferred_quotation_items_remain_outstanding_until_the_next_delivery(self):
        second = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot="Deferred bandage",
            quantity=3, unit_price=1, match_status="confirmed", outcome_status="accepted", accepted_quantity=3)
        note = self.draft(lines=[{"quotation_line": self.line.pk, "quantity": "10"},
            {"quotation_line": second.pk, "quantity": "3", "deliver_later": True}])
        issued = self.issue(note)
        self.receive(issued)
        progress = order_progress(self.quote)
        self.assertEqual(progress["delivery_status"], "partially_delivered")
        remaining = next(row for row in progress["lines"] if row["id"] == second.pk)
        self.assertEqual(Decimal(remaining["available_quantity"]), 3)
        later = self.client.get(self.url("detail", issued["later_deliveries"][0]["id"])).data
        self.receive(self.issue(later))
        self.assertEqual(order_progress(self.quote)["delivery_status"], "completed")

    def test_all_deferred_can_be_saved_but_not_issued_or_printed(self):
        note = self.draft(lines=[{"quotation_line": self.line.pk, "quantity": "10", "deliver_later": True}])
        self.assertEqual(self.client.post(self.url("issue", note["id"]), {}, format="json").status_code, 400)
        self.assertEqual(self.client.get(self.url("pdf", note["id"])).status_code, 400)
        self.assertEqual(DeliveryNote.objects.count(), 1)
        self.assertTrue(DeliveryNoteLine.objects.get().deliver_later)

    def test_failed_issue_does_not_split_off_deferred_rows(self):
        second = QuotationLine.objects.create(quotation=self.quote, item_name_snapshot="Later item",
            quantity=1, unit_price=1, match_status="confirmed", outcome_status="accepted", accepted_quantity=1)
        note = self.draft(lines=[{"quotation_line": self.line.pk, "quantity": "11"},
            {"quotation_line": second.pk, "quantity": "1", "deliver_later": True}])
        self.assertEqual(self.client.post(self.url("issue", note["id"]), {}, format="json").status_code, 400)
        self.assertEqual(DeliveryNote.objects.count(), 1)
        self.assertEqual(DeliveryNoteLine.objects.filter(delivery_note_id=note["id"]).count(), 2)

    def test_pdf_includes_pharmacy_trn_even_when_hidden_on_quotations(self):
        settings = QuotationSettings.objects.create(pk=1, trn="100000000000017", show_trn=False)
        note = self.draft(lpo_number="PO112_112353")
        text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(
            self.client.get(self.url("pdf", note["id"])).content)).pages)
        self.assertIn("Pharmacy TRN", text)
        self.assertEqual(text.count(settings.trn), 1)
        self.assertIn(self.company.trn, text)
        self.assertIn("PO112_112353", text)
        settings.refresh_from_db()
        self.assertFalse(settings.show_trn)


@skipUnless(connection.vendor == "postgresql", "Row locking requires PostgreSQL")
class DeliveryNoteConcurrencyTests(TransactionTestCase):
    def test_simultaneous_issue_retries_create_only_one_later_draft(self):
        actor = get_user_model().objects.create_user("concurrent-delivery", is_staff=True)
        company = Company.objects.create(name="Concurrent delivery customer")
        note = DeliveryNote.objects.create(company=company, customer_name=company.name, created_by=actor)
        DeliveryNoteLine.objects.create(delivery_note=note, item_name="Now", quantity=1)
        DeliveryNoteLine.objects.create(delivery_note=note, item_name="Later", quantity=2, deliver_later=True)
        barrier = Barrier(2)

        def issue():
            try:
                stale = DeliveryNote.objects.get(pk=note.pk)
                barrier.wait(timeout=10)
                return issue_delivery_note(stale, actor).status
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(issue) for _ in range(2)]
            self.assertEqual([future.result(timeout=20) for future in futures], ["issued", "issued"])
        self.assertEqual(note.later_deliveries.count(), 1)
        self.assertEqual(note.lines.count(), 1)
        self.assertEqual(note.later_deliveries.get().lines.get().quantity, Decimal("2"))
