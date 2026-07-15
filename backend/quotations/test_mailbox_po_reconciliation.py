from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .mailbox_po_reconciliation import reconcile_mailbox_po_audit
from .models import (
    Company,
    GmailOAuthConnection,
    MailboxPOAuditRun,
    MailboxPOMatchRun,
    MailboxPOMessage,
    ProformaInvoice,
    Quotation,
    QuotationLPO,
    QuotationLine,
    QuotationPOEvidence,
)


class MailboxPOReconciliationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("mailbox-matcher", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@pharmacy.example",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(name="Acme Medical", email="buyer@acme.example")
        self.sent_at = timezone.now() - timedelta(days=2)
        self.quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0001",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            subtotal=Decimal("150.00"),
            total=Decimal("150.00"),
            created_by=self.staff,
        )
        self.line_1 = self.line(self.quote, "Nitrile Gloves Blue Size M Box 100", 10, 10, 0)
        self.line_2 = self.line(self.quote, "Sterile Gauze Swab 10 x 10 cm", 5, 10, 1)
        self.run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def line(self, quote, name, quantity, price, order):
        return QuotationLine.objects.create(
            quotation=quote,
            item_name_snapshot=name,
            quantity=Decimal(str(quantity)),
            unit_price=Decimal(str(price)),
            sort_order=order,
        )

    def message(self, message_id, *, rows=None, subject="Purchase Order attached", body="Please proceed", labels=None, attachment=True):
        manifest = []
        if attachment:
            manifest.append(
                {
                    "attachment_id": f"att-{message_id}",
                    "part_id": "1",
                    "filename": f"LPO-{message_id}.pdf",
                    "mime_type": "application/pdf",
                    "size": 1200,
                    "status": "parsed",
                    "source_sha256": (message_id.encode("utf-8").hex() + "0" * 64)[:64],
                    "original_text": body,
                    "lines": rows or [],
                    "line_count": len(rows or []),
                }
            )
        return MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=message_id,
            gmail_thread_id=f"thread-{message_id}",
            mailbox_email=self.connection.email,
            label_ids=labels or ["INBOX"],
            subject=subject,
            sender="Acme Buyer <buyer@acme.example>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=12),
            snippet=body,
            newest_body_text=body,
            attachment_manifest=manifest,
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=not bool({"SPAM", "TRASH"}.intersection(labels or [])),
            first_seen_run=self.run,
            last_seen_run=self.run,
            full_message_fetched_at=timezone.now(),
            attachments_audited_at=timezone.now(),
            last_audited_at=timezone.now(),
        )

    def test_decisive_subset_match_uses_items_quantities_prices_time_and_selected_attachment(self):
        before_quote = (self.quote.outcome_status, self.quote.outcome_last_updated_at)
        before_lines = list(
            self.quote.lines.values_list("id", "outcome_status", "accepted_quantity", "accepted_total")
        )
        message = self.message(
            "decisive",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            body="Local Purchase Order\nNitrile Gloves Blue Size M Box 100\nGrand Total: AED 100.00",
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(quotation=self.quote, gmail_message_id="decisive")
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(match_run.summary["decisive_messages"], 1)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(evidence.mailbox_message, message)
        self.assertEqual(evidence.mailbox_match_run, match_run)
        self.assertEqual(evidence.selected_attachment_id, "att-decisive")
        self.assertEqual(evidence.selected_attachment_filename, "LPO-decisive.pdf")
        self.assertTrue(evidence.attachments[0]["is_selected"])
        self.assertEqual(evidence.match_signals["candidate"]["item_coverage"], 1.0)
        self.assertEqual(evidence.match_signals["candidate"]["quote_coverage"], 0.5)
        self.quote.refresh_from_db()
        self.assertEqual((self.quote.outcome_status, self.quote.outcome_last_updated_at), before_quote)
        self.assertEqual(
            list(self.quote.lines.values_list("id", "outcome_status", "accepted_quantity", "accepted_total")),
            before_lines,
        )
        self.assertEqual(QuotationLPO.objects.count(), 0)
        self.assertEqual(ProformaInvoice.objects.count(), 0)

    def test_missing_quantity_or_commercial_value_stays_ambiguous_even_with_exact_quote_reference(self):
        self.message(
            "missing-commercial",
            subject=f"LPO for {self.quote.quotation_number}",
            body=f"Purchase Order for {self.quote.quotation_number}",
            rows=[{"raw_name": "Nitrile Gloves Blue Size M Box 100"}],
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="missing-commercial")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertTrue(evidence.quote_reference_present)

    def test_explicit_reference_to_another_quote_blocks_item_based_link_to_this_quote(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0002",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at + timedelta(hours=1),
            created_by=self.staff,
        )
        self.line(other, "Surgical Mask 3 Ply", 10, 2, 0)
        self.message(
            "wrong-ref",
            subject=f"Purchase Order for {other.quotation_number}",
            body=f"LPO for quotation {other.quotation_number}",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        self.assertFalse(
            QuotationPOEvidence.objects.filter(quotation=self.quote, gmail_message_id="wrong-ref").exists()
        )
        other_evidence = QuotationPOEvidence.objects.get(quotation=other, gmail_message_id="wrong-ref")
        self.assertEqual(other_evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    def test_spam_message_is_inventory_complete_but_never_decisive(self):
        self.message(
            "spam-match",
            labels=["SPAM"],
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="spam-match")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertIn("Spam or Trash", evidence.error)
        self.assertEqual(match_run.summary["spam_or_trash_messages"], 1)

    def test_complete_reconciliation_supersedes_old_unreviewed_noise_but_preserves_parsed(self):
        irrelevant = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="old-noise",
            mailbox_email=self.connection.email,
            subject="Invoice reminder",
            sender="buyer@acme.example",
            sent_at=self.sent_at + timedelta(hours=2),
            classification=MailboxPOMessage.CLASS_OTHER,
            is_relevant=False,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )
        old = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="old-noise",
            subject="Old broad candidate",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )
        parsed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="approved-source",
            subject="Approved source",
            status=QuotationPOEvidence.STATUS_PARSED,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        old.refresh_from_db()
        parsed.refresh_from_db()
        self.assertEqual(old.mailbox_message, irrelevant)
        self.assertEqual(old.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertEqual(parsed.status, QuotationPOEvidence.STATUS_PARSED)

    def test_stale_sweep_never_supersedes_a_manually_approved_link(self):
        inventory = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="approved-but-not-parsed",
            mailbox_email=self.connection.email,
            subject="Reviewed source",
            sender="buyer@acme.example",
            sent_at=self.sent_at + timedelta(hours=2),
            classification=MailboxPOMessage.CLASS_OTHER,
            is_relevant=False,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=inventory.gmail_message_id,
            subject="Staff-approved source",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
            link_approved_by=self.staff,
            link_approved_at=timezone.now(),
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence.refresh_from_db()
        self.assertEqual(evidence.mailbox_message, inventory)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertIsNotNone(evidence.link_approved_at)

    def test_repeated_reconciliation_is_idempotent_for_evidence_and_outcomes(self):
        self.message(
            "repeat",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        first = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)
        second = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        self.assertEqual(QuotationPOEvidence.objects.filter(gmail_message_id="repeat").count(), 1)
        evidence = QuotationPOEvidence.objects.get(gmail_message_id="repeat")
        self.assertEqual(evidence.mailbox_match_run, second)
        self.assertNotEqual(first, second)
        self.assertEqual(self.quote.lines.filter(outcome_status=QuotationLine.OUTCOME_PENDING).count(), 2)


class MailboxPOAuditAPITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("audit-api", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="API Audit Company", email="buyer@example.test")
        self.quote = Quotation.objects.create(
            company=company,
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(hours=2),
            created_by=self.staff,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_start_endpoint_creates_then_resumes_one_incomplete_run(self):
        first = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")
        second = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["run"]["id"], second.data["run"]["id"])
        self.assertFalse(first.data["inventory_done"])

    @patch("quotations.views.scan_mailbox_po_audit_page")
    def test_scan_page_is_bounded_for_web_requests(self, scan_page):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
        )
        scan_page.return_value = run

        response = self.client.post(
            reverse("quotation-mailbox-po-audit-scan-page", args=[run.id]),
            {"page_size": 999},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        scan_page.assert_called_once_with(run, page_size=25)

    def test_completed_inventory_can_be_reconciled_and_latest_reports_done(self):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

        response = self.client.post(
            reverse("quotation-mailbox-po-audit-reconcile", args=[run.id]),
            {},
            format="json",
        )
        latest = self.client.get(reverse("quotation-mailbox-po-audit-latest"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["done"])
        self.assertEqual(response.data["match_run"]["status"], MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(latest.data["run"]["id"], run.id)
        self.assertTrue(latest.data["done"])

    def test_latest_reports_exhausted_but_incomplete_tombstone_inventory(self):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 before:2 -from:me",
            exhausted=True,
            incomplete_messages=2,
            completed_at=timezone.now(),
        )

        latest = self.client.get(reverse("quotation-mailbox-po-audit-latest"))

        self.assertEqual(latest.data["run"]["id"], run.id)
        self.assertEqual(latest.data["run"]["incomplete_messages"], 2)
        self.assertTrue(latest.data["inventory_done"])
        self.assertFalse(latest.data["inventory_complete"])

    def test_anonymous_user_cannot_start_a_mailbox_audit(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")

        self.assertIn(response.status_code, {401, 403})
