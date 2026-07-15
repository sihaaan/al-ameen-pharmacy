from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .mailbox_po_audit import scan_mailbox_po_audit_page, start_mailbox_po_audit
from .models import (
    Company,
    CompanyPriceHistory,
    GmailOAuthConnection,
    MailboxPOMessage,
    Quotation,
    QuotationAuditLog,
    QuotationLPO,
    QuotationLine,
    QuotationPOEvidence,
)
from .services import finalize_quotation, revise_quotation, update_quotation_outcome


class MailboxHistoryRegressionTests(APITestCase):
    """Protect durable quote history from read-only mailbox inventory work."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username="mailbox-history-staff",
            password="pass",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@pharmacy.example",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(
            name="Mailbox History Customer",
            email="buyer@customer.example",
        )
        self.product = Product.objects.create(
            name="Mailbox History Gloves",
            price=Decimal("1.00"),
            pack_size="box",
            status="draft",
        )
        self.client.force_authenticate(self.staff)

    def _finalized_quote(self, *, unit_price="10.00", quantity="5.000"):
        quotation = Quotation.objects.create(company=self.company, created_by=self.staff)
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=self.product,
            item_name_snapshot=self.product.name,
            quantity=Decimal(quantity),
            unit="box",
            unit_price=Decimal(unit_price),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        finalize_quotation(quotation, self.staff)
        quotation.refresh_from_db()
        line.refresh_from_db()
        return quotation, line

    def _mark_sent_and_partially_accepted(self, quotation, line):
        quotation.status = Quotation.STATUS_SENT
        quotation.sent_at = timezone.now() - timedelta(days=2)
        quotation.save(update_fields=["status", "sent_at", "updated_at"])
        update_quotation_outcome(
            quotation,
            {
                "line_updates": [
                    {
                        "id": line.id,
                        "outcome_status": QuotationLine.OUTCOME_QUANTITY_CHANGED,
                        "accepted_quantity": "3.000",
                        "accepted_unit_price": "8.750",
                        "outcome_notes": "Customer ordered a smaller quantity.",
                    }
                ]
            },
            self.staff,
        )
        Quotation.objects.filter(pk=quotation.pk).update(outcome_date=date(2026, 7, 12))
        QuotationLPO.objects.create(
            quotation=quotation,
            source_type=QuotationLPO.SOURCE_GMAIL,
            gmail_message_id="gmail-history-1",
            mailbox_email=self.connection.email,
            lpo_number="LPO-HISTORY-1",
            status=QuotationLPO.STATUS_CONFIRMED,
            received_by=self.staff,
        )
        quotation.refresh_from_db()
        line.refresh_from_db()

    def _price_context(self, quotation):
        return self.client.get(
            reverse("quotation-product-price", args=[quotation.id]),
            {"product": self.product.id, "history_limit": 50},
        )

    @staticmethod
    def _quote_snapshot(quotation_id):
        return Quotation.objects.filter(pk=quotation_id).values(
            "status",
            "subtotal",
            "vat_total",
            "total",
            "outcome_status",
            "outcome_status_is_manual",
            "outcome_date",
            "outcome_notes",
            "outcome_closed_at",
            "outcome_closed_by_id",
            "outcome_last_updated_at",
            "outcome_last_updated_by_id",
            "updated_at",
        ).get()

    @staticmethod
    def _line_snapshot(line_id):
        return QuotationLine.objects.filter(pk=line_id).values(
            "quantity",
            "unit_price",
            "line_total",
            "outcome_status",
            "accepted_quantity",
            "accepted_unit_price",
            "accepted_total",
            "lost_value",
            "outcome_reason",
            "outcome_notes",
            "updated_at",
        ).get()

    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_repeated_mailbox_inventory_preserves_outcome_lpo_price_and_audit_history(
        self,
        list_messages,
        fetch_message,
    ):
        source_quote, source_line = self._finalized_quote()
        self._mark_sent_and_partially_accepted(source_quote, source_line)
        current_quote = Quotation.objects.create(company=self.company, created_by=self.staff)

        price_before = self._price_context(current_quote)
        self.assertEqual(price_before.status_code, status.HTTP_200_OK)
        self.assertEqual(price_before.data["latest_accepted"]["lpo_number"], "LPO-HISTORY-1")

        quote_before = self._quote_snapshot(source_quote.id)
        line_before = self._line_snapshot(source_line.id)
        price_rows_before = list(
            CompanyPriceHistory.objects.filter(quotation=source_quote)
            .order_by("id")
            .values(
                "company_id",
                "product_id",
                "quotation_id",
                "quotation_line_id",
                "unit_price",
                "currency",
                "quantity",
                "unit",
                "quoted_at",
            )
        )
        lpos_before = list(
            QuotationLPO.objects.filter(quotation=source_quote)
            .order_by("id")
            .values("id", "lpo_number", "status", "source_type", "gmail_message_id", "updated_at")
        )
        audit_before = list(
            QuotationAuditLog.objects.filter(quotation=source_quote)
            .order_by("id")
            .values("id", "action", "message", "changes", "created_at")
        )

        list_messages.return_value = {
            "messages": [{"id": "gmail-history-1"}],
            "next_page_token": "",
            "result_size_estimate": 1,
        }
        fetch_message.return_value = {
            "gmail_message_id": "gmail-history-1",
            "gmail_thread_id": "thread-history-1",
            "full_headers": [{"name": "Subject", "value": f"LPO for {source_quote.quotation_number}"}],
            "subject": f"LPO for {source_quote.quotation_number}",
            "sender": "buyer@customer.example",
            "recipients": self.connection.email,
            "cc": "",
            "reply_to": "",
            "sent_at": timezone.now() - timedelta(days=1),
            "snippet": "Please find our purchase order.",
            "newest_body_text": "Please proceed with the quantities in our attached LPO.",
            "attachment_manifest": [],
            "_attachment_refs": [],
        }

        first_run = start_mailbox_po_audit(
            self.connection,
            requested_by=self.staff,
            earliest_quote_at=source_quote.created_at,
        )
        first_run = scan_mailbox_po_audit_page(first_run)
        second_run = start_mailbox_po_audit(
            self.connection,
            requested_by=self.staff,
            earliest_quote_at=source_quote.created_at,
        )
        second_run = scan_mailbox_po_audit_page(second_run)

        self.assertEqual(first_run.status, first_run.STATUS_COMPLETED)
        self.assertEqual(second_run.status, second_run.STATUS_COMPLETED)
        self.assertEqual(first_run.messages_created, 1)
        self.assertEqual(second_run.messages_created, 0)
        self.assertEqual(MailboxPOMessage.objects.count(), 1)
        self.assertEqual(MailboxPOMessage.objects.get().newest_body_text, fetch_message.return_value["newest_body_text"])

        self.assertEqual(self._quote_snapshot(source_quote.id), quote_before)
        self.assertEqual(self._line_snapshot(source_line.id), line_before)
        self.assertEqual(
            list(
                CompanyPriceHistory.objects.filter(quotation=source_quote)
                .order_by("id")
                .values(*price_rows_before[0].keys())
            ),
            price_rows_before,
        )
        self.assertEqual(
            list(
                QuotationLPO.objects.filter(quotation=source_quote)
                .order_by("id")
                .values(*lpos_before[0].keys())
            ),
            lpos_before,
        )
        self.assertEqual(
            list(
                QuotationAuditLog.objects.filter(quotation=source_quote)
                .order_by("id")
                .values(*audit_before[0].keys())
            ),
            audit_before,
        )

        price_after = self._price_context(current_quote)
        self.assertEqual(price_after.status_code, status.HTTP_200_OK)
        self.assertEqual(price_after.data, price_before.data)

    def test_canonical_candidate_is_visible_but_review_only(self):
        source_quote, source_line = self._finalized_quote()
        self._mark_sent_and_partially_accepted(source_quote, source_line)
        current_quote = Quotation.objects.create(company=self.company, created_by=self.staff)
        price_before = self._price_context(current_quote)
        quote_before = self._quote_snapshot(source_quote.id)
        line_before = self._line_snapshot(source_line.id)

        run = start_mailbox_po_audit(
            self.connection,
            requested_by=self.staff,
            earliest_quote_at=source_quote.created_at,
        )
        message = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="gmail-review-only",
            gmail_thread_id="thread-review-only",
            mailbox_email=self.connection.email,
            subject=f"LPO for {source_quote.quotation_number}",
            sender="buyer@customer.example",
            recipients=self.connection.email,
            sent_at=timezone.now(),
            newest_body_text="The attached order confirms three boxes.",
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            relevance_reason="PO language and exact quote reference found.",
            first_seen_run=run,
            last_seen_run=run,
        )
        evidence = QuotationPOEvidence.objects.create(
            quotation=source_quote,
            mailbox_message=message,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id=message.gmail_message_id,
            gmail_thread_id=message.gmail_thread_id,
            sender=message.sender,
            recipients=message.recipients,
            subject=message.subject,
            sent_at=message.sent_at,
            snippet=message.newest_body_text,
            matching_reason="Exact quote reference, customer, quantity, and timing matched.",
            match_signals={
                "quote_reference": {"matched": True, "value": source_quote.quotation_number},
                "quantity": {"matched": True, "exact": 1},
            },
            confidence=96,
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )

        outcome_response = self.client.get(reverse("quotation-outcome", args=[source_quote.id]))
        self.assertEqual(outcome_response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in outcome_response.data["po_evidence"]], [evidence.id])
        self.assertEqual(outcome_response.data["quotation"]["outcome_status"], source_quote.outcome_status)

        self.assertEqual(self._quote_snapshot(source_quote.id), quote_before)
        self.assertEqual(self._line_snapshot(source_line.id), line_before)
        self.assertEqual(QuotationLPO.objects.filter(quotation=source_quote).count(), 1)
        self.assertEqual(CompanyPriceHistory.objects.filter(quotation=source_quote).count(), 1)
        price_after = self._price_context(current_quote)
        self.assertEqual(price_after.data, price_before.data)

    def test_legacy_evidence_has_unknown_quote_reference_provenance(self):
        source_quote, _source_line = self._finalized_quote()

        evidence = QuotationPOEvidence.objects.create(
            quotation=source_quote,
            gmail_connection=self.connection,
            gmail_message_id="gmail-before-reference-audit",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )

        self.assertIsNone(evidence.quote_reference_present)

    def test_revised_quotes_keep_ordered_price_and_accepted_lpo_history(self):
        original, original_line = self._finalized_quote(unit_price="9.000")
        self._mark_sent_and_partially_accepted(original, original_line)

        revision = revise_quotation(original, self.staff)
        original.refresh_from_db()
        self.assertEqual(original.status, Quotation.STATUS_REVISED)

        before_revision_finalize = self._price_context(revision)
        self.assertEqual(before_revision_finalize.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["quotation"] for row in before_revision_finalize.data["history"]],
            [original.id],
        )
        self.assertEqual(before_revision_finalize.data["latest_accepted"]["quotation"], original.id)
        self.assertEqual(before_revision_finalize.data["latest_accepted"]["lpo_number"], "LPO-HISTORY-1")

        revision_line = revision.lines.get()
        revision_line.unit_price = Decimal("11.500")
        revision_line.save()
        finalize_quotation(revision, self.staff)
        current_quote = Quotation.objects.create(company=self.company, created_by=self.staff)

        response = self._price_context(current_quote)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            [row["quotation"] for row in response.data["history"]],
            [revision.id, original.id],
        )
        self.assertEqual(response.data["latest_quoted"]["quoted_unit_price"], "11.50")
        self.assertEqual(response.data["latest_accepted"]["quotation"], original.id)
        self.assertEqual(response.data["latest_accepted"]["accepted_unit_price"], "8.75")
        self.assertEqual(response.data["latest_accepted"]["lpo_number"], "LPO-HISTORY-1")
        self.assertEqual(CompanyPriceHistory.objects.filter(company=self.company, product=self.product).count(), 2)
