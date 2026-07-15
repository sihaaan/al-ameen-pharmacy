from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.utils import timezone

from . import mailbox_po_reconciliation
from .mailbox_po_reconciliation import (
    MailboxPOMatchBusy,
    reconcile_mailbox_po_audit_page,
)
from .models import (
    Company,
    GmailOAuthConnection,
    MailboxPOAuditRun,
    MailboxPOAuditRunMessage,
    MailboxPOMatchRun,
    MailboxPOMessage,
    Quotation,
    QuotationLPO,
    QuotationLine,
    QuotationPOEvidence,
)


class ResumableMailboxPOMatchingTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("resume-matcher", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            email="orders@example.test",
            is_shared=True,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="Resume Medical", email="buyer@resume.example")
        self.sent_at = timezone.now() - timedelta(days=2)
        self.quote = Quotation.objects.create(
            company=company,
            quotation_number="QT-20260712-0001",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            subtotal=Decimal("100.00"),
            total=Decimal("100.00"),
            created_by=self.staff,
        )
        QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot="Nitrile Gloves Blue Size M Box 100",
            quantity=Decimal("10"),
            unit_price=Decimal("10"),
        )
        self.audit = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            mailbox_cutoff_at=timezone.now(),
            gmail_query="in:anywhere after:1 before:2 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def add_message(self, suffix, *, warnings=None):
        message = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=f"resume-{suffix}",
            mailbox_email=self.connection.email,
            subject="Purchase Order attached",
            sender="Resume Buyer <buyer@resume.example>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=1),
            newest_body_text="Please find the purchase order attached.",
            attachment_manifest=[
                {
                    "attachment_id": f"att-{suffix}",
                    "filename": f"LPO-{suffix}.pdf",
                    "mime_type": "application/pdf",
                    "status": "parsed",
                    "source_sha256": f"{int(suffix):064x}",
                    "original_text": (
                        f"Purchase Order for {self.quote.quotation_number}\n"
                        "Nitrile Gloves Blue Size M Box 100\nGrand Total: AED 100.00"
                    ),
                    "totals": {"grand_total": "100.00"},
                    "warnings": list(warnings or []),
                    "lines": [
                        {
                            "raw_name": "Nitrile Gloves Blue Size M Box 100",
                            "quantity": "10",
                            "unit_price": "10",
                            "line_total": "100",
                        }
                    ],
                }
            ],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
            first_seen_run=self.audit,
            last_seen_run=self.audit,
        )
        MailboxPOAuditRunMessage.objects.create(audit_run=self.audit, message=message)
        return message

    def test_matching_advances_a_durable_cursor_in_bounded_pages(self):
        messages = [self.add_message(index) for index in range(1, 4)]

        first = reconcile_mailbox_po_audit_page(
            self.audit,
            requested_by=self.staff,
            page_size=1,
        )
        self.assertEqual(first.status, MailboxPOMatchRun.STATUS_RUNNING)
        self.assertEqual(first.cursor_message_id, messages[0].id)
        self.assertEqual(first.summary["relevant_messages"], 1)

        second = reconcile_mailbox_po_audit_page(
            self.audit,
            requested_by=self.staff,
            match_run=first,
            page_size=1,
        )
        self.assertEqual(second.status, MailboxPOMatchRun.STATUS_RUNNING)
        self.assertEqual(second.cursor_message_id, messages[1].id)

        completed = reconcile_mailbox_po_audit_page(
            self.audit,
            requested_by=self.staff,
            match_run=second,
            page_size=1,
        )
        self.assertEqual(completed.status, MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(completed.cursor_message_id, messages[2].id)
        self.assertEqual(completed.summary["relevant_messages"], 3)
        self.assertEqual(
            QuotationPOEvidence.objects.filter(
                quotation=self.quote,
                selected_attachment_id__gt="",
            ).count(),
            3,
        )
        self.assertEqual(QuotationLPO.objects.count(), 0)

    def test_an_active_lease_blocks_a_second_matching_worker(self):
        self.add_message(1)
        match_run = MailboxPOMatchRun.objects.create(
            audit_run=self.audit,
            requested_by=self.staff,
            algorithm_version="mailbox_match_v2",
            lease_token="another-worker",
            lease_expires_at=timezone.now() + timedelta(seconds=60),
        )

        with self.assertRaises(MailboxPOMatchBusy):
            reconcile_mailbox_po_audit_page(
                self.audit,
                requested_by=self.staff,
                match_run=match_run,
                page_size=1,
            )

    def test_active_worker_heartbeats_while_matching_a_page(self):
        self.add_message(1)

        with patch(
            "quotations.mailbox_po_reconciliation._renew_match_lease",
            wraps=mailbox_po_reconciliation._renew_match_lease,
        ) as renew:
            match_run = reconcile_mailbox_po_audit_page(
                self.audit,
                requested_by=self.staff,
                page_size=1,
            )

        self.assertGreaterEqual(renew.call_count, 4)
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertIsNotNone(match_run.last_heartbeat_at)
        self.assertEqual(match_run.lease_token, "")

    def test_stale_worker_cannot_persist_cursor_or_summary_after_lease_is_stolen(self):
        self.add_message(1)

        def steal_lease(*_args, **_kwargs):
            MailboxPOMatchRun.objects.filter(audit_run=self.audit).update(
                lease_token="replacement-worker",
                lease_expires_at=timezone.now() + timedelta(seconds=60),
            )
            return (), False, 1

        with patch(
            "quotations.mailbox_po_reconciliation._proposals_for_message",
            side_effect=steal_lease,
        ):
            with self.assertRaises(MailboxPOMatchBusy):
                reconcile_mailbox_po_audit_page(
                    self.audit,
                    requested_by=self.staff,
                    page_size=1,
                )

        match_run = MailboxPOMatchRun.objects.get(audit_run=self.audit)
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_RUNNING)
        self.assertEqual(match_run.lease_token, "replacement-worker")
        self.assertEqual(match_run.cursor_message_id, 0)
        self.assertEqual(match_run.summary["relevant_messages"], 0)
        self.assertEqual(QuotationPOEvidence.objects.count(), 0)

    def test_stale_worker_cannot_mark_replacement_lease_failed(self):
        self.add_message(1)

        def steal_lease_then_fail():
            MailboxPOMatchRun.objects.filter(audit_run=self.audit).update(
                lease_token="replacement-worker",
                lease_expires_at=timezone.now() + timedelta(seconds=60),
            )
            raise ValueError("simulated matcher failure after lease expiry")

        with patch(
            "quotations.mailbox_po_reconciliation.eligible_quotations",
            side_effect=steal_lease_then_fail,
        ):
            with self.assertRaisesRegex(ValueError, "simulated matcher failure"):
                reconcile_mailbox_po_audit_page(
                    self.audit,
                    requested_by=self.staff,
                    page_size=1,
                )

        match_run = MailboxPOMatchRun.objects.get(audit_run=self.audit)
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_RUNNING)
        self.assertEqual(match_run.lease_token, "replacement-worker")
        self.assertIsNone(match_run.completed_at)
        self.assertEqual(match_run.errors, [])

    def test_attachment_parser_warning_forces_staff_review(self):
        self.add_message(
            1,
            warnings=("OCR confidence was low on the quantity column.",),
        )

        match_run = reconcile_mailbox_po_audit_page(
            self.audit,
            requested_by=self.staff,
            page_size=1,
        )

        evidence = QuotationPOEvidence.objects.get(selected_attachment_id="att-1")
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(
            evidence.match_signals["candidate"]["parser_warnings"],
            ["OCR confidence was low on the quantity column."],
        )
        self.assertTrue(
            any(
                "parser reported" in blocker
                for blocker in evidence.match_signals["automatic_blockers"]
            )
        )
