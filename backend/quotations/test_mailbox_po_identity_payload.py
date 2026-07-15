from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .mailbox_po_reconciliation import document_variants, reconcile_mailbox_po_audit
from .models import (
    Company,
    GmailOAuthConnection,
    MailboxPOAuditRun,
    MailboxPOMessage,
    Quotation,
    QuotationLine,
    QuotationPOEvidence,
)
from .quote_po_intelligence import (
    _get_evidence_gmail_connection,
    _lock_and_resolve_evidence_approval,
    _preview_from_gmail_payload,
    _reviewed_message_conflicts,
)
from .serializers import QuotationPOEvidenceSerializer


class MultiAttachmentEvidenceIdentityTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("multi-lpo-reviewer", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="Multi LPO Buyer", email="buyer@example.test")
        self.sent_at = timezone.now() - timedelta(days=2)
        self.quote_a = self._quote(company, "QT-20260710-0101", "Nitrile Gloves Blue Size M", 10, 10)
        self.quote_b = self._quote(company, "QT-20260710-0102", "Digital Thermometer DT-100", 4, 25)
        self.run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote_a.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def _quote(self, company, number, item, quantity, price):
        quote = Quotation.objects.create(
            company=company,
            quotation_number=number,
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            subtotal=Decimal(str(quantity * price)),
            total=Decimal(str(quantity * price)),
            created_by=self.staff,
        )
        QuotationLine.objects.create(
            quotation=quote,
            item_name_snapshot=item,
            quantity=Decimal(str(quantity)),
            unit_price=Decimal(str(price)),
        )
        return quote

    def _attachment(self, identifier, filename, quote, item, quantity, price, digest):
        return {
            "attachment_id": identifier,
            "part_id": identifier,
            "filename": filename,
            "mime_type": "application/pdf",
            "size": 1200,
            "status": "parsed",
            "source_sha256": digest,
            "original_text": (
                f"PURCHASE ORDER\nQuotation: {quote.quotation_number}\n{item}\n"
                f"Quantity {quantity}\nGrand Total AED {quantity * price}.00"
            ),
            "lines": [
                {
                    "raw_name": item,
                    "quantity": str(quantity),
                    "unit_price": str(price),
                    "line_total": str(quantity * price),
                }
            ],
            "line_count": 1,
        }

    def test_two_lpo_attachments_create_and_approve_independent_quote_sources(self):
        attachment_a = self._attachment(
            "attachment-a", "LPO-A.pdf", self.quote_a, "Nitrile Gloves Blue Size M", 10, 10, "a" * 64
        )
        attachment_b = self._attachment(
            "attachment-b", "LPO-B.pdf", self.quote_b, "Digital Thermometer DT-100", 4, 25, "b" * 64
        )
        MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="message-with-two-lpos",
            gmail_thread_id="thread-with-two-lpos",
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject="Purchase orders attached",
            sender="Buyer <buyer@example.test>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=6),
            newest_body_text="Please process the two attached purchase orders.",
            attachment_manifest=[attachment_a, attachment_b],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence_a = QuotationPOEvidence.objects.get(
            quotation=self.quote_a,
            gmail_message_id="message-with-two-lpos",
        )
        evidence_b = QuotationPOEvidence.objects.get(
            quotation=self.quote_b,
            gmail_message_id="message-with-two-lpos",
        )
        self.assertEqual(evidence_a.selected_attachment_id, "attachment-a")
        self.assertEqual(evidence_b.selected_attachment_id, "attachment-b")
        self.assertNotEqual(evidence_a.source_key, evidence_b.source_key)

        payload = {
            "gmail_message_id": "message-with-two-lpos",
            "subject": "Purchase orders attached",
            "body_text": "Please process the two attached purchase orders.",
            "attachments": [attachment_a, attachment_b],
        }
        self.assertEqual(
            _preview_from_gmail_payload(payload, evidence_a)["meta"]["selected_attachment_id"],
            "attachment-a",
        )
        self.assertEqual(
            _preview_from_gmail_payload(payload, evidence_b)["meta"]["selected_attachment_id"],
            "attachment-b",
        )

        with transaction.atomic():
            locked_a, _ = _lock_and_resolve_evidence_approval(evidence_a, self.connection, payload)
            locked_a.link_approved_at = timezone.now()
            locked_a.link_approved_by = self.staff
            locked_a.save(update_fields=["link_approved_at", "link_approved_by", "updated_at"])
        self.assertEqual(_reviewed_message_conflicts(evidence_b, self.connection), [])
        with transaction.atomic():
            locked_b, _ = _lock_and_resolve_evidence_approval(evidence_b, self.connection, payload)
            locked_b.link_approved_at = timezone.now()
            locked_b.link_approved_by = self.staff
            locked_b.save(update_fields=["link_approved_at", "link_approved_by", "updated_at"])

        evidence_a.refresh_from_db()
        evidence_b.refresh_from_db()
        self.assertIsNotNone(evidence_a.link_approved_at)
        self.assertIsNotNone(evidence_b.link_approved_at)

    def test_independent_body_order_is_kept_beside_an_unrelated_attachment(self):
        attachment_a = self._attachment(
            "attachment-a", "LPO-A.pdf", self.quote_a, "Nitrile Gloves Blue Size M", 10, 10, "a" * 64
        )
        body = (
            f"Purchase Order for {self.quote_b.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Digital Thermometer DT-100 | 4 | 25 | 100\n"
            "Grand Total: 100"
        )
        MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="attachment-and-independent-body",
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject="Two purchase orders",
            sender="Buyer <buyer@example.test>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=6),
            newest_body_text=body,
            attachment_manifest=[attachment_a],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.filter(
            gmail_message_id="attachment-and-independent-body"
        )
        self.assertEqual(evidence.count(), 2)
        attachment_evidence = evidence.get(quotation=self.quote_a)
        body_evidence = evidence.get(quotation=self.quote_b)
        self.assertEqual(attachment_evidence.selected_attachment_id, "attachment-a")
        self.assertEqual(attachment_evidence.match_signals["source"]["kind"], "attachment")
        self.assertEqual(body_evidence.selected_attachment_id, "")
        self.assertEqual(body_evidence.match_signals["source"]["kind"], "email_body")

    def test_body_that_mirrors_an_attachment_is_not_emitted_twice(self):
        attachment = self._attachment(
            "attachment-mirror",
            "LPO-mirror.pdf",
            self.quote_a,
            "Nitrile Gloves Blue Size M",
            10,
            10,
            "c" * 64,
        )
        mirrored_body = (
            f"Purchase Order for {self.quote_a.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        attachment["original_text"] = mirrored_body
        MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="mirrored-body-and-attachment",
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject="Purchase order attached",
            sender="Buyer <buyer@example.test>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=6),
            newest_body_text=mirrored_body,
            attachment_manifest=[attachment],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.filter(
            gmail_message_id="mirrored-body-and-attachment"
        )
        self.assertEqual(evidence.count(), 1)
        self.assertEqual(evidence.get().selected_attachment_id, "attachment-mirror")

    def test_body_subset_of_attachment_rows_remains_an_independent_variant(self):
        attachment = self._attachment(
            "attachment-with-two-rows",
            "LPO-two-rows.pdf",
            self.quote_a,
            "Nitrile Gloves Blue Size M",
            10,
            10,
            "f" * 64,
        )
        attachment["lines"].append(
            {
                "raw_name": "Surgical Mask Type IIR",
                "quantity": "5",
                "unit_price": "5",
                "line_total": "25",
            }
        )
        message = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="body-is-row-subset",
            mailbox_email=self.connection.email,
            subject=f"Purchase Order for {self.quote_a.quotation_number}",
            sender="Buyer <buyer@example.test>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=6),
            newest_body_text=(
                f"Purchase Order for {self.quote_a.quotation_number}\n"
                "Item | Qty | Unit Price | Total\n"
                "Nitrile Gloves Blue Size M | 10 | 10 | 100\n"
                "Grand Total: 100"
            ),
            attachment_manifest=[attachment],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )

        variants = document_variants(message)

        self.assertEqual(
            [variant.source_kind for variant in variants],
            ["attachment", "email_body"],
        )

    def test_body_with_multiple_quote_refs_does_not_poison_attachment_local_refs(self):
        attachment_a = self._attachment(
            "mixed-reference-a", "LPO-A.pdf", self.quote_a, "Nitrile Gloves Blue Size M", 10, 10, "d" * 64
        )
        attachment_b = self._attachment(
            "mixed-reference-b", "LPO-B.pdf", self.quote_b, "Digital Thermometer DT-100", 4, 25, "e" * 64
        )
        MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="mixed-body-refs-with-local-attachment-refs",
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject="Purchase orders attached",
            sender="Buyer <buyer@example.test>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=6),
            newest_body_text=(
                f"Please process the attached purchase orders for "
                f"{self.quote_a.quotation_number} and {self.quote_b.quotation_number}."
            ),
            attachment_manifest=[attachment_a, attachment_b],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.filter(
            gmail_message_id="mixed-body-refs-with-local-attachment-refs"
        )
        self.assertEqual(evidence.count(), 2)
        self.assertEqual(evidence.get(quotation=self.quote_a).selected_attachment_id, "mixed-reference-a")
        self.assertEqual(evidence.get(quotation=self.quote_b).selected_attachment_id, "mixed-reference-b")

    def test_same_source_document_remains_exclusive_between_quotes(self):
        first = QuotationPOEvidence.objects.create(
            quotation=self.quote_a,
            gmail_connection=self.connection,
            gmail_message_id="shared-source-message",
            selected_attachment_id="same-source",
            source_sha256="c" * 64,
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_at=timezone.now(),
            link_approved_by=self.staff,
        )
        second = QuotationPOEvidence.objects.create(
            quotation=self.quote_b,
            gmail_connection=self.connection,
            gmail_message_id="shared-source-message",
            selected_attachment_id="same-source",
            source_sha256="c" * 64,
        )

        conflicts = _reviewed_message_conflicts(second, self.connection)

        self.assertEqual([row.id for row in conflicts], [first.id])


class EvidencePayloadAndSharedMailboxAPITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("payload-reviewer", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="shared@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="Payload Buyer")
        self.quote = Quotation.objects.create(
            company=company,
            quotation_number="QT-20260710-0201",
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(hours=2),
            created_by=self.staff,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def _evidence(self, *, status=QuotationPOEvidence.STATUS_CANDIDATE, index=0, connection=None):
        connection = connection or self.connection
        return QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=connection,
            mailbox_email=connection.email,
            gmail_message_id=f"payload-message-{connection.id}-{index}",
            selected_attachment_id=f"attachment-{index}",
            source_sha256=f"{index + 1:064x}",
            extracted_text="FULL SELECTED SOURCE TEXT",
            attachments=[
                {
                    "attachment_id": f"attachment-{index}",
                    "part_id": str(index),
                    "filename": f"LPO-{index}.pdf",
                    "mime_type": "application/pdf",
                    "size": 500,
                    "status": "parsed",
                    "line_count": 1,
                    "original_text": "PRIVATE ORIGINAL TEXT",
                    "lines": [{"raw_name": "Private row", "quantity": "10"}],
                    "totals": {"grand_total": "100.00"},
                    "meta": {"raw": "private parser metadata"},
                }
            ],
            status=status,
            created_by=self.staff,
        )

    def test_summary_serializer_returns_only_attachment_metadata(self):
        evidence = self._evidence()

        data = QuotationPOEvidenceSerializer(evidence).data

        attachment = data["attachments"][0]
        self.assertEqual(attachment["filename"], "LPO-0.pdf")
        self.assertNotIn("original_text", attachment)
        self.assertNotIn("lines", attachment)
        self.assertNotIn("totals", attachment)
        self.assertNotIn("meta", attachment)
        self.assertNotIn("extracted_text_preview", data)
        self.assertNotIn("email_body_preview", data)

    def test_archived_evidence_is_paginated_while_active_is_always_returned(self):
        active = self._evidence(index=100)
        for index in range(25):
            self._evidence(status=QuotationPOEvidence.STATUS_SUPERSEDED, index=index)
        url = reverse("quotation-po-evidence", args=[self.quote.id])

        first = self.client.get(url)
        second = self.client.get(url, {"archived_offset": 20})

        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["pagination"]["active_count"], 1)
        self.assertEqual(first.data["pagination"]["archived_count"], 25)
        self.assertTrue(first.data["pagination"]["archived_has_more"])
        self.assertIn(active.id, [row["id"] for row in first.data["results"]])
        self.assertEqual(len(first.data["results"]), 21)
        self.assertIn(active.id, [row["id"] for row in second.data["results"]])
        self.assertEqual(len(second.data["results"]), 6)

    def test_source_text_is_lazy_and_authenticated(self):
        evidence = self._evidence()
        url = reverse("quotation-po-evidence-source", args=[evidence.id])

        response = self.client.get(url)
        self.client.force_authenticate(user=None)
        anonymous = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["extracted_text"], "FULL SELECTED SOURCE TEXT")
        self.assertEqual(response["Cache-Control"], "private, no-store, max-age=0")
        self.assertIn(anonymous.status_code, {401, 403})

    @patch("quotations.views.gmail_fetch_attachment_content")
    def test_same_mailbox_oauth_rotation_preserves_review_and_approval_access(self, mock_fetch):
        previous_owner = User.objects.create_user("previous-mail-owner", is_staff=True)
        previous_connection = GmailOAuthConnection.objects.create(
            user=previous_owner,
            is_shared=False,
            email=self.connection.email,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        evidence = self._evidence(connection=previous_connection, index=501)
        mock_fetch.return_value = {
            "content": b"%PDF-1.4\nrotated mailbox evidence",
            "filename": "LPO-501.pdf",
            "mime_type": "application/pdf",
            "size": 34,
        }

        evidence_page = self.client.get(reverse("quotation-po-evidence", args=[self.quote.id]))
        source = self.client.get(reverse("quotation-po-evidence-source", args=[evidence.id]))
        attachment = self.client.get(
            reverse("quotation-po-evidence-attachment", args=[evidence.id]),
            {"attachment_id": "attachment-501"},
        )
        quote_list = self.client.get(reverse("quotation-list"))

        self.assertEqual(evidence_page.status_code, 200)
        self.assertIn(evidence.id, [row["id"] for row in evidence_page.data["results"]])
        self.assertEqual(source.status_code, 200)
        self.assertEqual(attachment.status_code, 200)
        mock_fetch.assert_called_once_with(
            self.connection,
            evidence.gmail_message_id,
            attachment_id="attachment-501",
            part_id="501",
            max_bytes=20 * 1024 * 1024,
        )
        quote_rows = quote_list.data.get("results", []) if isinstance(quote_list.data, dict) else quote_list.data
        quote_row = next(row for row in quote_rows if row["id"] == self.quote.id)
        self.assertEqual(quote_row["po_evidence_count"], 1)
        self.assertEqual(_get_evidence_gmail_connection(evidence, self.staff), self.connection)

    @patch("quotations.views.gmail_fetch_attachment_content")
    def test_non_shared_runs_and_evidence_cannot_be_retrieved_by_id(self, mock_fetch):
        legacy_owner = User.objects.create_user("legacy-mail-owner", is_staff=True)
        legacy = GmailOAuthConnection.objects.create(
            user=legacy_owner,
            is_shared=False,
            email="legacy@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=legacy,
            requested_by=legacy_owner,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1",
        )
        evidence = self._evidence(connection=legacy, index=999)

        run_response = self.client.get(reverse("quotation-mailbox-po-audit-detail", args=[run.id]))
        source_response = self.client.get(reverse("quotation-po-evidence-source", args=[evidence.id]))
        attachment_response = self.client.get(
            reverse("quotation-po-evidence-attachment", args=[evidence.id]),
            {"attachment_id": "attachment-999"},
        )
        quote_list = self.client.get(reverse("quotation-list"))

        self.assertEqual(run_response.status_code, 404)
        self.assertEqual(source_response.status_code, 404)
        self.assertEqual(attachment_response.status_code, 404)
        quote_rows = quote_list.data.get("results", []) if isinstance(quote_list.data, dict) else quote_list.data
        quote_row = next(row for row in quote_rows if row["id"] == self.quote.id)
        self.assertEqual(quote_row["po_evidence_count"], 0)
        mock_fetch.assert_not_called()
