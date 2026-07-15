"""Adversarial regression tests for mailbox-wide PO inventory and matching.

These tests concentrate on retry/snapshot behaviour and on preserving manual
review decisions.  They intentionally exercise the seams between the Gmail
inventory, canonical messages, reconciliation, and the HTTP resume workflow.
"""

import hashlib
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .mailbox_po_audit import (
    run_mailbox_po_audit,
    scan_mailbox_po_audit_page,
    start_mailbox_po_audit,
)
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


def _sha(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


class MailboxPOPageRetryAdversarialTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("mailbox-page-retry", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="Retry Customer", email="buyer@example.test")
        self.quote = Quotation.objects.create(
            company=company,
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(days=1),
            created_by=self.staff,
        )

    def gmail_message(self, message_id):
        return {
            "gmail_message_id": message_id,
            "gmail_thread_id": f"thread-{message_id}",
            "label_ids": ["INBOX"],
            "full_headers": [{"name": "Subject", "value": "Purchase Order"}],
            "subject": "Purchase Order",
            "sender": "buyer@example.test",
            "recipients": self.connection.email,
            "cc": "",
            "reply_to": "",
            "sent_at": timezone.now(),
            "snippet": "Please proceed with this LPO",
            "newest_body_text": "Please proceed with this LPO",
            "attachment_manifest": [],
            "_attachment_refs": [],
        }

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_mid_page_failure_replays_page_without_duplicate_counts(
        self,
        list_messages,
        fetch_message,
        _hydrate,
    ):
        list_messages.return_value = {
            "messages": [{"id": "first"}, {"id": "second"}],
            "next_page_token": "",
            "result_size_estimate": 2,
        }
        fetch_message.side_effect = [
            self.gmail_message("first"),
            RuntimeError("temporary Gmail 503"),
            self.gmail_message("first"),
            self.gmail_message("second"),
        ]
        run = start_mailbox_po_audit(self.connection, requested_by=self.staff)

        failed = scan_mailbox_po_audit_page(run)

        self.assertEqual(failed.status, MailboxPOAuditRun.STATUS_FAILED)
        self.assertEqual(failed.page_token, "")
        self.assertEqual(failed.pages_scanned, 0)
        self.assertEqual(failed.messages_scanned, 0)
        self.assertEqual(MailboxPOMessage.objects.count(), 1)

        completed = scan_mailbox_po_audit_page(failed)

        self.assertEqual(completed.status, MailboxPOAuditRun.STATUS_COMPLETED)
        self.assertEqual(completed.pages_scanned, 1)
        self.assertEqual(completed.messages_scanned, 2)
        self.assertEqual(completed.messages_created, 2)
        self.assertEqual(completed.relevant_messages, 2)
        self.assertEqual(MailboxPOMessage.objects.count(), 2)
        self.assertEqual(fetch_message.call_count, 4)
        self.assertEqual(
            [call.kwargs["page_token"] for call in list_messages.call_args_list],
            ["", ""],
        )


class MailboxPOReconciliationAdversarialTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("mailbox-adversarial", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@pharmacy.example",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(
            name="Acme Medical",
            email="buyer@acme.example",
        )
        self.sent_at = timezone.now() - timedelta(days=3)
        self.quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260712-0001",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            subtotal=Decimal("150.00"),
            total=Decimal("150.00"),
            created_by=self.staff,
        )
        self.line = QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot="Nitrile Gloves Blue Size M Box 100",
            quantity=Decimal("10"),
            unit_price=Decimal("10"),
            sort_order=0,
        )
        QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot="Sterile Gauze Swab 10 x 10 cm",
            quantity=Decimal("5"),
            unit_price=Decimal("10"),
            sort_order=1,
        )
        self.run = self.completed_run()

    def completed_run(self):
        return MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def parsed_attachment(
        self,
        attachment_id,
        *,
        filename=None,
        quantity="10",
        unit_price="10",
        line_total="100",
        document_total="100",
        item_name="Nitrile Gloves Blue Size M Box 100",
    ):
        filename = filename or f"LPO-{attachment_id}.pdf"
        return {
            "attachment_id": attachment_id,
            "part_id": attachment_id,
            "filename": filename,
            "mime_type": "application/pdf",
            "size": 1200,
            "status": "parsed",
            "source_sha256": _sha(attachment_id),
            "original_text": (
                f"Purchase Order\n{item_name} | {quantity} | {unit_price} | {line_total}\n"
                f"Grand Total: {document_total}"
            ),
            "totals": {"grand_total": document_total},
            "lines": [
                {
                    "raw_name": item_name,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "line_total": line_total,
                }
            ],
            "line_count": 1,
        }

    def inventory(
        self,
        message_id,
        *,
        manifest=None,
        body="Please proceed with the purchase order.",
        subject="Purchase Order attached",
        run=None,
        relevant=True,
    ):
        run = run or self.run
        return MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=message_id,
            gmail_thread_id=f"thread-{message_id}",
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject=subject,
            sender="Acme Buyer <buyer@acme.example>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=12),
            snippet=body,
            newest_body_text=body,
            attachment_manifest=manifest or [],
            classification=(
                MailboxPOMessage.CLASS_PURCHASE_ORDER
                if relevant
                else MailboxPOMessage.CLASS_OTHER
            ),
            is_relevant=relevant,
            auto_link_eligible=True,
            first_seen_run=run,
            last_seen_run=run,
            full_message_fetched_at=timezone.now(),
            attachments_audited_at=timezone.now(),
            last_audited_at=timezone.now(),
        )

    def test_multiple_attachments_are_scored_independently_and_best_source_is_selected(self):
        wrong_reference = self.parsed_attachment(
            "wrong",
            filename="QT-20260712-9999.pdf",
        )
        correct = self.parsed_attachment("correct", filename="LPO-ACME-4401.pdf")
        self.inventory("multi-attachment", manifest=[wrong_reference, correct])

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="multi-attachment")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(evidence.selected_attachment_id, "correct")
        self.assertEqual(evidence.selected_attachment_filename, "LPO-ACME-4401.pdf")
        selected = {
            attachment["attachment_id"]: attachment["is_selected"]
            for attachment in evidence.attachments
        }
        self.assertEqual(selected, {"wrong": False, "correct": True})
        self.assertEqual(evidence.match_signals["source"]["kind"], "attachment")

    def test_wrong_quote_reference_in_the_only_attachment_blocks_identical_items(self):
        attachment = self.parsed_attachment(
            "wrong-only",
            filename="Order-for-QT-20260712-9999.pdf",
        )
        self.inventory("wrong-reference-only", manifest=[attachment])

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        self.assertFalse(
            QuotationPOEvidence.objects.filter(gmail_message_id="wrong-reference-only").exists()
        )
        self.assertEqual(match_run.summary["unmatched_messages"], 1)

    def test_body_only_order_can_be_matched_with_items_quantity_price_total_and_reference(self):
        body = (
            f"Purchase Order for {self.quote.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        self.inventory(
            "body-only",
            manifest=[],
            subject=f"Purchase Order for {self.quote.quotation_number}",
            body=body,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="body-only")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(evidence.selected_attachment_id, "")
        self.assertEqual(evidence.attachments, [])
        self.assertTrue(evidence.quote_reference_present)
        self.assertEqual(evidence.match_signals["source"]["kind"], "email_body")
        candidate = evidence.match_signals["candidate"]
        self.assertEqual(candidate["quantity_exact_count"], 1)
        self.assertEqual(candidate["price_exact_count"], 1)
        self.assertEqual(candidate["total_exact_count"], 1)

    def test_arithmetically_wrong_line_and_document_totals_never_become_decisive(self):
        attachment = self.parsed_attachment(
            "wrong-totals",
            filename=f"LPO-{self.quote.quotation_number}.pdf",
            line_total="90",
            document_total="90",
        )
        self.inventory(
            "wrong-totals",
            manifest=[attachment],
            subject=f"Purchase Order for {self.quote.quotation_number}",
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="wrong-totals")
        candidate = evidence.match_signals["candidate"]
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(candidate["total_conflict_count"], 1)
        self.assertEqual(candidate["document_total_result"], "conflict")
        self.assertEqual(QuotationLPO.objects.count(), 0)
        self.assertEqual(ProformaInvoice.objects.count(), 0)

    def test_manual_parsed_source_fields_survive_reconciliation(self):
        self.inventory(
            "already-parsed",
            manifest=[self.parsed_attachment("new-parser-source")],
        )
        original_attachments = [{"attachment_id": "approved", "filename": "approved.pdf"}]
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="already-parsed",
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_by=self.staff,
            link_approved_at=timezone.now(),
            attachments=original_attachments,
            selected_attachment_id="approved",
            selected_attachment_filename="approved.pdf",
            extracted_text="manually approved extraction",
            source_sha256="a" * 64,
            error="manual review note",
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence.refresh_from_db()
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(evidence.attachments, original_attachments)
        self.assertEqual(evidence.selected_attachment_id, "approved")
        self.assertEqual(evidence.selected_attachment_filename, "approved.pdf")
        self.assertEqual(evidence.extracted_text, "manually approved extraction")
        self.assertEqual(evidence.source_sha256, "a" * 64)
        self.assertEqual(evidence.error, "manual review note")

    def test_manual_not_relevant_source_fields_survive_reconciliation(self):
        self.inventory(
            "already-dismissed",
            manifest=[self.parsed_attachment("new-dismissed-source")],
        )
        original_attachments = [{"attachment_id": "dismissed", "filename": "dismissed.pdf"}]
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="already-dismissed",
            status=QuotationPOEvidence.STATUS_NOT_RELEVANT,
            attachments=original_attachments,
            selected_attachment_id="dismissed",
            selected_attachment_filename="dismissed.pdf",
            extracted_text="staff-dismissed source",
            source_sha256="b" * 64,
            error="Not an LPO for this quotation.",
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence.refresh_from_db()
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_NOT_RELEVANT)
        self.assertEqual(evidence.attachments, original_attachments)
        self.assertEqual(evidence.selected_attachment_id, "dismissed")
        self.assertEqual(evidence.selected_attachment_filename, "dismissed.pdf")
        self.assertEqual(evidence.extracted_text, "staff-dismissed source")
        self.assertEqual(evidence.source_sha256, "b" * 64)
        self.assertEqual(evidence.error, "Not an LPO for this quotation.")

    def test_same_gmail_id_from_another_connection_is_not_linked_or_superseded(self):
        other_staff = User.objects.create_user("other-mailbox-owner", is_staff=True)
        other_connection = GmailOAuthConnection.objects.create(
            user=other_staff,
            is_shared=False,
            email="other-mailbox@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.inventory("mailbox-local-id", relevant=False, body="Ordinary message")
        foreign_evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=other_connection,
            mailbox_email=other_connection.email,
            gmail_message_id="mailbox-local-id",
            subject="Unrelated evidence in another Gmail account",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        foreign_evidence.refresh_from_db()
        self.assertIsNone(foreign_evidence.mailbox_message_id)
        self.assertEqual(foreign_evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(foreign_evidence.subject, "Unrelated evidence in another Gmail account")

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_completed_run_keeps_its_message_snapshot_after_two_later_scans(
        self,
        list_messages,
        fetch_message,
        _hydrate,
    ):
        # Use three real inventory passes.  A single first_seen/last_seen pair
        # cannot represent membership in the middle completed audit.
        MailboxPOAuditRun.objects.filter(pk=self.run.pk).delete()
        body = (
            f"Purchase Order for {self.quote.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        list_messages.return_value = {
            "messages": [{"id": "reused-canonical"}],
            "next_page_token": "",
            "result_size_estimate": 1,
        }
        fetch_message.return_value = {
            "gmail_message_id": "reused-canonical",
            "gmail_thread_id": "thread-reused-canonical",
            "label_ids": ["INBOX"],
            "full_headers": [{"name": "Subject", "value": "Purchase Order"}],
            "subject": f"Purchase Order for {self.quote.quotation_number}",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "cc": "",
            "reply_to": "",
            "sent_at": self.sent_at + timedelta(hours=12),
            "snippet": body,
            "newest_body_text": body,
            "attachment_manifest": [],
            "_attachment_refs": [],
        }
        runs = [
            run_mailbox_po_audit(
                start_mailbox_po_audit(self.connection, requested_by=self.staff)
            )
            for _index in range(3)
        ]
        middle_run = runs[1]
        canonical = MailboxPOMessage.objects.get(gmail_message_id="reused-canonical")
        self.assertEqual(canonical.first_seen_run, runs[0])
        self.assertEqual(canonical.last_seen_run, runs[2])
        self.assertEqual(middle_run.messages_scanned, 1)

        match_run = reconcile_mailbox_po_audit(middle_run, requested_by=self.staff)

        self.assertEqual(match_run.summary["relevant_messages"], 1)
        self.assertTrue(
            QuotationPOEvidence.objects.filter(
                quotation=self.quote,
                gmail_message_id="reused-canonical",
            ).exists()
        )


class MailboxPOAuditAPIAdversarialTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("mailbox-api-adversarial", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="API Customer", email="buyer@example.test")
        self.quote = Quotation.objects.create(
            company=company,
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(days=1),
            created_by=self.staff,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def completed_run(self):
        return MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def test_start_resumes_completed_inventory_that_still_needs_reconciliation(self):
        run = self.completed_run()

        response = self.client.post(
            reverse("quotation-mailbox-po-audit-list"),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["run"]["id"], run.id)
        self.assertTrue(response.data["inventory_done"])
        self.assertFalse(response.data["done"])
        self.assertEqual(MailboxPOAuditRun.objects.count(), 1)

    def test_reconcile_endpoint_is_idempotent_without_force(self):
        run = self.completed_run()
        url = reverse("quotation-mailbox-po-audit-reconcile", args=[run.id])

        first = self.client.post(url, {}, format="json")
        second = self.client.post(url, {}, format="json")

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertTrue(first.data["done"])
        self.assertTrue(second.data["done"])
        self.assertEqual(first.data["match_run"]["id"], second.data["match_run"]["id"])
        self.assertEqual(
            MailboxPOMatchRun.objects.filter(audit_run=run).count(),
            1,
        )
