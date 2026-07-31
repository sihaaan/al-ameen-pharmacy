import base64
import hashlib
import urllib.error
import urllib.parse
from datetime import timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.signing import TimestampSigner
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    encrypt_token,
    exchange_gmail_code,
)
from .models import (
    Company,
    CompanyContact,
    GmailInquiryImport,
    GmailOAuthConnection,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailThreadSelection,
    QuotationLine,
)
from .quotation_email_delivery import _mark_delivery_failure


class QuotationEmailDeliveryAPITests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="email-staff",
            password="pass",
            is_staff=True,
        )
        self.customer_user = User.objects.create_user(
            username="email-customer",
            password="pass",
        )
        self.company = Company.objects.create(
            name="Email Customer LLC",
            email="accounts@example.com",
        )
        self.contact = CompanyContact.objects.create(
            company=self.company,
            name="Celine Buyer",
            email="buyer@example.com",
            is_primary=True,
        )
        self.product = Product.objects.create(
            name="Email Test Product",
            price=Decimal("10.00"),
            status="draft",
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="pharmacydxb@gmail.com",
            scopes=[GMAIL_READONLY_SCOPE, GMAIL_SEND_SCOPE],
            status=GmailOAuthConnection.STATUS_CONNECTED,
            access_token_encrypted=encrypt_token("access-token"),
            token_expiry=timezone.now() + timedelta(hours=1),
        )
        self.client.force_authenticate(self.staff)

    def create_quote(self, *, contact=True, status_value=Quotation.STATUS_DRAFT):
        quotation = Quotation.objects.create(
            company=self.company,
            contact=self.contact if contact else None,
            created_by=self.staff,
            status=status_value,
        )
        QuotationLine.objects.create(
            quotation=quotation,
            product=self.product,
            item_name_snapshot="Email Test Product",
            quantity=Decimal("2.000"),
            unit="PCS",
            unit_price=Decimal("10.000"),
            vat_rate=Decimal("5.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        return quotation

    @staticmethod
    def source_metadata(
        message_id="gmail-inbound-1",
        *,
        thread_id="gmail-thread-1",
        sender="Customer Buyer <buyer@example.com>",
        reply_to="orders@example.com",
        subject="RFQ medical supplies",
        rfc_message_id="<customer-1@example.com>",
        references="<older@example.com>",
    ):
        return {
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "label_ids": ["INBOX"],
            "subject": subject,
            "sender": sender,
            "reply_to": reply_to,
            "recipients": "pharmacydxb@gmail.com",
            "cc": "",
            "rfc_message_id": rfc_message_id,
            "references": references,
            "in_reply_to": "",
            "sent_at": timezone.now(),
            "snippet": "Please quote the attached items.",
        }

    def gmail_link(self, quotation):
        gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id="gmail-thread-1",
            anchor_message_id="gmail-inbound-1",
            status=GmailInquiryImport.STATUS_CONFIRMED,
            quotation=quotation,
            message_manifest=[
                {
                    "gmail_message_id": "gmail-inbound-1",
                    "gmail_thread_id": "gmail-thread-1",
                    "subject": "RFQ medical supplies",
                    "sender": "Customer Buyer <buyer@example.com>",
                    "reply_to": "orders@example.com",
                    "sent_at": "2026-07-30T08:00:00+00:00",
                    "is_outbound": False,
                    "classification": "initial_inquiry",
                    "usage": "used",
                },
                {
                    "gmail_message_id": "gmail-followup-2",
                    "gmail_thread_id": "gmail-thread-1",
                    "subject": "RFQ medical supplies",
                    "sender": "Customer Buyer <buyer@example.com>",
                    "reply_to": "orders@example.com",
                    "sent_at": "2026-07-31T08:00:00+00:00",
                    "is_outbound": False,
                    "classification": "follow_up",
                    "usage": "context",
                },
            ],
        )
        return gmail_import

    def manual_payload(self, **overrides):
        payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
        }
        payload.update(overrides)
        return payload

    def gmail_payload(self, **overrides):
        payload = {
            "to": ["orders@example.com"],
            "cc": [],
            "subject": "RFQ medical supplies",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
        }
        payload.update(overrides)
        return payload

    def test_manual_preview_is_read_only_and_prefills_explicit_contact(self):
        quotation = self.create_quote()
        response = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["delivery_mode"], "new_email")
        self.assertEqual(response.data["to"], ["buyer@example.com"])
        self.assertIsNone(response.data["delivery_id"])
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_preview_uses_latest_relevant_inbound_even_when_context(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(message_id="gmail-followup-2")

        response = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(fetch.call_args.args[1], "gmail-followup-2")
        self.assertEqual(response.data["delivery_mode"], "gmail_reply")
        self.assertEqual(response.data["to"], ["orders@example.com"])
        self.assertEqual(response.data["subject"], "RFQ medical supplies")
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_wrong_gmail_recipient_is_rejected_before_finalization(self, send):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        with patch(
            "quotations.quotation_email_delivery.gmail_fetch_reply_metadata",
            return_value=self.source_metadata(message_id="gmail-followup-2"),
        ):
            response = self.client.post(
                reverse("quotation-finalize-and-send", args=[quotation.id]),
                self.gmail_payload(to=["attacker@example.com"]),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_recipient_mismatch")
        self.assertFalse(response.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_source_without_rfc_message_id_fails_before_finalization(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(
            message_id="gmail-followup-2",
            rfc_message_id="",
        )

        preview = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(preview.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(preview.data["code"], "source_message_id_missing")
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_oversized_gmail_source_subject_is_rejected_before_persistence(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(
            message_id="gmail-followup-2",
            subject="S" * 999,
        )

        response = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_subject_too_long")
        self.assertFalse(
            QuotationEmailDelivery.objects.filter(quotation=quotation).exists()
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_disconnected_import_mailbox_returns_reconnect_error_without_fetch(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        self.connection.status = GmailOAuthConnection.STATUS_DISCONNECTED
        self.connection.save(update_fields=["status", "updated_at"])

        response = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_reconnect_required")
        self.assertTrue(response.data["retryable"])
        fetch.assert_not_called()

    @patch(
        "quotations.quotation_email_delivery.gmail_fetch_reply_metadata",
        side_effect=RuntimeError("Gmail connection expired or was revoked. Reconnect Gmail."),
    )
    def test_source_refresh_failure_returns_safe_reconnect_error(self, _fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)

        response = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_reconnect_required")
        self.assertNotIn("revoked", response.data["detail"].lower())

    def test_missing_send_scope_is_reported_before_finalization(self):
        quotation = self.create_quote()
        self.connection.scopes = [GMAIL_READONLY_SCOPE]
        self.connection.save(update_fields=["scopes", "updated_at"])

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_reconnect_required")
        self.assertTrue(response.data["retryable"])
        self.assertFalse(response.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_finalization_validation_error_is_clean_and_leaves_deletable_draft(self, send):
        quotation = self.create_quote()
        line = quotation.lines.get()
        line.unit_price = Decimal("0.000")
        line.save(update_fields=["unit_price", "updated_at"])

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "quotation_finalization_failed")
        self.assertFalse(response.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        prepared = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(prepared.status, QuotationEmailDelivery.STATUS_PREPARED)
        self.assertEqual(prepared.attempt_count, 0)
        send.assert_not_called()

        deleted = self.client.delete(reverse("quotation-detail", args=[quotation.id]))
        self.assertEqual(deleted.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(pk=prepared.pk).exists())

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_reply_sends_exact_thread_headers_and_marks_quote_sent(
        self,
        fetch,
        send,
        _pdf,
    ):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(message_id="gmail-followup-2")
        send.return_value = {"id": "gmail-sent-1", "threadId": "gmail-thread-1"}

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.gmail_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["quote"]["status"], Quotation.STATUS_SENT)
        self.assertEqual(response.data["delivery"]["status"], "sent")
        self.assertEqual(send.call_args.kwargs["thread_id"], "gmail-thread-1")
        raw = send.call_args.args[1]
        padded = raw + ("=" * (-len(raw) % 4))
        message = BytesParser(policy=policy.default).parsebytes(
            base64.urlsafe_b64decode(padded.encode("ascii"))
        )
        self.assertEqual(message["To"], "orders@example.com")
        self.assertEqual(message["Subject"], "RFQ medical supplies")
        self.assertEqual(message["In-Reply-To"], "<customer-1@example.com>")
        self.assertEqual(
            message["References"],
            "<older@example.com> <customer-1@example.com>",
        )
        self.assertTrue(message["Message-ID"].startswith("<quotation-"))

    def test_late_failure_cannot_downgrade_terminal_sent_delivery(self):
        quotation = self.create_quote(status_value=Quotation.STATUS_SENT)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_SENT,
            to_addresses=["buyer@example.com"],
            subject="Quotation",
            body="Attached.",
            attachment_filename="quotation.pdf",
            attempt_count=1,
            sent_at=timezone.now(),
            gmail_message_id="gmail-already-sent",
            sent_gmail_thread_id="gmail-thread-already-sent",
        )
        audit_count = QuotationAuditLog.objects.filter(
            quotation=quotation,
            action__in=[
                QuotationAuditLog.ACTION_EMAIL_FAILED,
                QuotationAuditLog.ACTION_EMAIL_UNKNOWN,
            ],
        ).count()

        result = _mark_delivery_failure(
            delivery.id,
            unknown=True,
            message="Late timeout after reconciliation completed.",
            actor=self.staff,
        )

        self.assertEqual(result.status, QuotationEmailDelivery.STATUS_SENT)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENT)
        self.assertEqual(delivery.gmail_message_id, "gmail-already-sent")
        self.assertEqual(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action__in=[
                    QuotationAuditLog.ACTION_EMAIL_FAILED,
                    QuotationAuditLog.ACTION_EMAIL_UNKNOWN,
                ],
            ).count(),
            audit_count,
        )

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_known_provider_rejection_leaves_quote_finalized_and_retryable(self, _send, _pdf):
        quotation = self.create_quote()

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(response.data["quote_finalized"])
        self.assertTrue(response.data["retryable"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_FAILED)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"same-pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_identical_pdf_known_failure_retries_once_without_refinalizing(
        self,
        send,
        _pdf,
    ):
        quotation = self.create_quote()
        send.side_effect = [
            RuntimeError("Google API request failed with HTTP 400: invalid"),
            {"id": "gmail-retry-sent", "threadId": "gmail-retry-thread"},
        ]

        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        quotation.refresh_from_db()
        finalized_at = quotation.finalized_at
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(second.data["quote"]["status"], Quotation.STATUS_SENT)
        self.assertEqual(second.data["delivery"]["attempt_count"], 2)
        self.assertEqual(QuotationEmailDelivery.objects.filter(quotation=quotation).count(), 1)
        quotation.refresh_from_db()
        self.assertEqual(quotation.finalized_at, finalized_at)
        self.assertEqual(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action=QuotationAuditLog.ACTION_FINALIZED,
            ).count(),
            1,
        )
        self.assertEqual(send.call_count, 2)

    @patch(
        "quotations.quotation_email_delivery.build_quotation_pdf",
        side_effect=ValueError("renderer failed"),
    )
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_pdf_preparation_exception_is_known_failure_not_unbound_or_unknown(
        self,
        send,
        _pdf,
    ):
        quotation = self.create_quote()

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "email_prepare_failed")
        self.assertTrue(response.data["quote_finalized"])
        self.assertTrue(response.data["retryable"])
        self.assertEqual(
            QuotationEmailDelivery.objects.get(quotation=quotation).status,
            QuotationEmailDelivery.STATUS_FAILED,
        )
        send.assert_not_called()

    @patch(
        "quotations.quotation_email_delivery.get_valid_access_token",
        side_effect=urllib.error.URLError("token refresh timeout"),
    )
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_token_refresh_timeout_is_reconnect_precondition_before_finalization(
        self,
        send,
        _pdf,
        _token,
    ):
        quotation = self.create_quote()

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_reconnect_required")
        self.assertTrue(response.data["retryable"])
        self.assertFalse(response.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 503: unavailable"),
    )
    def test_temporary_http_send_response_is_unknown_not_retryable(
        self,
        _send,
        _pdf,
        search,
        _fetch,
    ):
        quotation = self.create_quote()
        search.return_value = {"messages": []}

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "delivery_unknown")
        self.assertFalse(response.data["retryable"])
        self.assertEqual(
            QuotationEmailDelivery.objects.get(quotation=quotation).status,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        return_value={"id": "gmail-sent-without-thread"},
    )
    def test_new_email_incomplete_receipt_without_thread_is_unknown(
        self,
        _send,
        _pdf,
        search,
        _fetch,
    ):
        quotation = self.create_quote()
        search.return_value = {"messages": []}

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "delivery_unknown")
        self.assertEqual(
            QuotationEmailDelivery.objects.get(quotation=quotation).status,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        )

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_repeated_click_after_success_is_idempotent(self, send, _pdf):
        quotation = self.create_quote()
        send.return_value = {"id": "gmail-sent-1", "threadId": "new-thread"}
        url = reverse("quotation-finalize-and-send", args=[quotation.id])

        first = self.client.post(url, self.manual_payload(), format="json")
        second = self.client.post(url, self.manual_payload(), format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["idempotent"])
        self.assertEqual(send.call_count, 1)

    def test_sending_or_unknown_delivery_blocks_cancel_and_revision_race(self):
        for delivery_status in [
            QuotationEmailDelivery.STATUS_SENDING,
            QuotationEmailDelivery.STATUS_UNKNOWN,
        ]:
            for action_name in ["quotation-cancel", "quotation-revise"]:
                with self.subTest(status=delivery_status, action=action_name):
                    quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
                    QuotationEmailDelivery.objects.create(
                        quotation=quotation,
                        gmail_connection=self.connection,
                        actor=self.staff,
                        delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
                        status=delivery_status,
                        to_addresses=["buyer@example.com"],
                        subject="Quotation",
                        body="Attached.",
                        attachment_filename="quotation.pdf",
                        sending_started_at=timezone.now(),
                    )

                    response = self.client.post(
                        reverse(action_name, args=[quotation.id]),
                        {},
                        format="json",
                    )

                    self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                    quotation.refresh_from_db()
                    self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
                    self.assertFalse(Quotation.objects.filter(parent=quotation).exists())

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=urllib.error.URLError("timeout"),
    )
    def test_ambiguous_network_failure_is_unknown_and_never_blindly_resent(
        self,
        send,
        _pdf,
        search,
        _fetch,
    ):
        quotation = self.create_quote()
        search.return_value = {"messages": []}
        url = reverse("quotation-finalize-and-send", args=[quotation.id])

        first = self.client.post(url, self.manual_payload(), format="json")
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(first.data["code"], "delivery_unknown")
        self.assertFalse(first.data["retryable"])
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(send.call_count, 1)
        self.assertIn("rfc822msgid:", search.call_args.args[1])

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconcile_email_finds_stable_message_id_without_sending(
        self,
        search,
        fetch,
        send,
    ):
        quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_UNKNOWN,
            to_addresses=["buyer@example.com"],
            subject="Quotation",
            body="Attached.",
            attachment_filename="quotation.pdf",
            attempt_count=1,
        )
        search.return_value = {"messages": [{"id": "gmail-reconciled"}]}
        fetch.return_value = self.source_metadata(
            message_id="gmail-reconciled",
            thread_id="gmail-reconciled-thread",
            rfc_message_id=delivery.outbound_rfc_message_id,
        )

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["reconciled"])
        self.assertEqual(response.data["delivery"]["status"], "sent")
        self.assertEqual(response.data["quote"]["status"], Quotation.STATUS_SENT)
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconcile_email_not_found_remains_unknown_without_sending(
        self,
        search,
        send,
    ):
        quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
        QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_UNKNOWN,
            to_addresses=["buyer@example.com"],
            subject="Quotation",
            body="Attached.",
            attachment_filename="quotation.pdf",
            attempt_count=1,
        )
        search.return_value = {"messages": []}

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["reconciled"])
        self.assertEqual(response.data["delivery"]["status"], "unknown")
        self.assertEqual(response.data["quote"]["status"], Quotation.STATUS_FINALIZED)
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    def test_retry_refuses_changed_pdf_snapshot(self, pdf, send):
        quotation = self.create_quote()
        pdf.side_effect = [b"first-pdf", b"changed-pdf"]
        send.side_effect = RuntimeError("Google API request failed with HTTP 400: invalid")

        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.data["code"], "attachment_snapshot_mismatch")
        self.assertFalse(second.data["retryable"])
        self.assertEqual(send.call_count, 1)

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"same-pdf")
    def test_failed_manual_delivery_preview_retains_staff_edits(self, _pdf, send):
        quotation = self.create_quote()
        send.side_effect = RuntimeError("Google API request failed with HTTP 400: invalid")
        payload = self.manual_payload(
            to=["chosen@example.com"],
            cc=["manager@example.com"],
            subject="Edited quotation subject",
            body="Edited staff body",
        )
        self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            payload,
            format="json",
        )

        preview = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(preview.data["to"], ["chosen@example.com"])
        self.assertEqual(preview.data["cc"], ["manager@example.com"])
        self.assertEqual(preview.data["subject"], "Edited quotation subject")
        self.assertEqual(preview.data["body"], "Edited staff body")

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_manual_thread_candidates_use_opaque_expiring_server_side_selection(
        self,
        search,
        fetch,
    ):
        quotation = self.create_quote()
        expired_selection = QuotationEmailThreadSelection.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            created_by=self.staff,
            token_hash="e" * 64,
            source_email="old@example.com",
            gmail_message_id="old-private-message",
            gmail_thread_id="old-private-thread",
            expires_at=timezone.now() - timedelta(minutes=1),
        )
        search.return_value = {"messages": [{"id": "private-message-id"}]}
        fetch.return_value = self.source_metadata(
            message_id="private-message-id",
            thread_id="private-thread-id",
            reply_to="buyer@example.com",
        )

        response = self.client.get(
            reverse("quotation-email-thread-candidates", args=[quotation.id]),
            {"recipient": "buyer@example.com"},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            QuotationEmailThreadSelection.objects.filter(pk=expired_selection.pk).exists()
        )
        candidate = response.data["candidates"][0]
        token = candidate["selection_token"]
        self.assertNotIn("gmail_message_id", candidate)
        self.assertNotIn("gmail_thread_id", candidate)
        self.assertNotIn("private-message-id", token)
        self.assertNotIn("buyer", token.lower())
        selection = QuotationEmailThreadSelection.objects.get()
        self.assertEqual(
            selection.token_hash,
            hashlib.sha256(token.encode("utf-8")).hexdigest(),
        )
        sibling = QuotationEmailThreadSelection.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            created_by=self.staff,
            token_hash="s" * 64,
            source_email="buyer@example.com",
            gmail_message_id="unused-private-message",
            gmail_thread_id="unused-private-thread",
            expires_at=timezone.now() + timedelta(minutes=30),
        )

        linked_preview = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"thread_selection_token": token},
        )
        self.assertEqual(linked_preview.status_code, status.HTTP_200_OK)
        self.assertEqual(linked_preview.data["delivery_mode"], "gmail_reply")
        self.assertNotIn("id", linked_preview.data["thread"])
        self.assertNotIn("private-message-id", str(linked_preview.data))
        self.assertNotIn("private-thread-id", str(linked_preview.data))
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        self.assertFalse(
            QuotationEmailThreadSelection.objects.filter(pk=sibling.pk).exists()
        )

        other_staff = User.objects.create_user(
            username="other-email-staff",
            password="pass",
            is_staff=True,
        )
        self.client.force_authenticate(other_staff)
        other_user = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"thread_selection_token": token},
        )
        self.assertEqual(other_user.status_code, status.HTTP_400_BAD_REQUEST)
        self.client.force_authenticate(self.staff)

        tampered = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"thread_selection_token": token + "x"},
        )
        self.assertEqual(tampered.status_code, status.HTTP_400_BAD_REQUEST)
        selection.expires_at = timezone.now() - timedelta(seconds=1)
        selection.save(update_fields=["expires_at"])
        expired = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"thread_selection_token": token},
        )
        self.assertEqual(expired.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            QuotationEmailThreadSelection.objects.filter(pk=selection.pk).exists()
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_new_manual_thread_search_replaces_abandoned_selection_tokens(
        self,
        search,
        fetch,
    ):
        quotation = self.create_quote()
        search.return_value = {"messages": [{"id": "private-message-id"}]}
        fetch.return_value = self.source_metadata(
            message_id="private-message-id",
            thread_id="private-thread-id",
            reply_to="buyer@example.com",
        )

        first = self.client.get(
            reverse("quotation-email-thread-candidates", args=[quotation.id]),
            {"recipient": "buyer@example.com"},
        )
        first_token = first.data["candidates"][0]["selection_token"]
        first_hash = hashlib.sha256(first_token.encode("utf-8")).hexdigest()
        second = self.client.get(
            reverse("quotation-email-thread-candidates", args=[quotation.id]),
            {"recipient": "buyer@example.com"},
        )

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertFalse(
            QuotationEmailThreadSelection.objects.filter(token_hash=first_hash).exists()
        )
        self.assertEqual(
            QuotationEmailThreadSelection.objects.filter(
                quotation=quotation,
                created_by=self.staff,
            ).count(),
            1,
        )

    def test_email_actions_are_staff_only(self):
        quotation = self.create_quote()
        self.client.force_authenticate(self.customer_user)
        for route_name, method in [
            ("quotation-email-preview", "get"),
            ("quotation-email-thread-candidates", "get"),
            ("quotation-finalize-and-send", "post"),
            ("quotation-send-email", "post"),
            ("quotation-reconcile-email", "post"),
        ]:
            with self.subTest(route=route_name):
                response = getattr(self.client, method)(
                    reverse(route_name, args=[quotation.id]),
                    {},
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_gmail_connection_reports_send_reconnect_requirement(self):
        self.connection.scopes = [GMAIL_READONLY_SCOPE]
        self.connection.save(update_fields=["scopes", "updated_at"])

        response = self.client.get(reverse("quotation-gmail-connection"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(GMAIL_SEND_SCOPE, response.data["required_scopes"])
        self.assertFalse(response.data["send_scope_granted"])
        self.assertTrue(response.data["reconnect_required"])

    def test_email_preview_reports_whether_actor_can_manage_shared_gmail(self):
        quotation = self.create_quote()

        owner_preview = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        self.assertEqual(owner_preview.status_code, status.HTTP_200_OK)
        self.assertTrue(owner_preview.data["gmail_can_manage"])

        other_staff = User.objects.create_user(
            username="preview-non-owner",
            password="pass",
            is_staff=True,
        )
        self.client.force_authenticate(other_staff)
        non_owner_preview = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )

        self.assertEqual(non_owner_preview.status_code, status.HTTP_200_OK)
        self.assertFalse(non_owner_preview.data["gmail_can_manage"])

    @patch("quotations.views.exchange_gmail_code")
    @override_settings(
        FRONTEND_URL="https://frontend.example",
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://api.example.com/api/quotations/gmail/oauth/callback/",
    )
    def test_oauth_callback_returns_to_signed_quote_path(self, exchange):
        return_path = "/admin?quotation_tab=quotations&quote_id=401"
        start = self.client.post(
            reverse("quotation-gmail-connection"),
            {"return_path": return_path},
            format="json",
        )
        state_value = urllib.parse.parse_qs(
            urllib.parse.urlsplit(start.data["auth_url"]).query
        )["state"][0]

        callback = self.client.get(
            reverse("quotation-gmail-oauth-callback"),
            {"state": state_value, "code": "oauth-code"},
        )

        self.assertEqual(start.status_code, status.HTTP_200_OK)
        self.assertEqual(callback.status_code, status.HTTP_302_FOUND)
        destination = urllib.parse.urlsplit(callback["Location"])
        self.assertEqual(destination.scheme, "https")
        self.assertEqual(destination.netloc, "frontend.example")
        self.assertEqual(destination.path, "/admin")
        self.assertEqual(
            urllib.parse.parse_qs(destination.query),
            {
                "quotation_tab": ["quotations"],
                "quote_id": ["401"],
                "gmail": ["connected"],
            },
        )
        exchange.assert_called_once()

    @patch("quotations.views.exchange_gmail_code")
    @override_settings(
        FRONTEND_URL="https://frontend.example",
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://api.example.com/api/quotations/gmail/oauth/callback/",
    )
    def test_external_oauth_return_path_is_rejected_and_falls_back(self, exchange):
        start = self.client.post(
            reverse("quotation-gmail-connection"),
            {"return_path": "https://evil.example/admin?quote_id=401"},
            format="json",
        )
        state_value = urllib.parse.parse_qs(
            urllib.parse.urlsplit(start.data["auth_url"]).query
        )["state"][0]

        callback = self.client.get(
            reverse("quotation-gmail-oauth-callback"),
            {"state": state_value, "code": "oauth-code"},
        )

        destination = urllib.parse.urlsplit(callback["Location"])
        self.assertEqual(destination.netloc, "frontend.example")
        self.assertEqual(destination.path, "/admin")
        self.assertEqual(
            urllib.parse.parse_qs(destination.query),
            {
                "quotation_tab": ["contract-intelligence"],
                "gmail": ["connected"],
            },
        )
        self.assertNotIn("evil.example", callback["Location"])
        exchange.assert_called_once()

    @patch("quotations.views.exchange_gmail_code")
    @override_settings(FRONTEND_URL="https://frontend.example")
    def test_legacy_oauth_state_remains_backward_compatible(self, exchange):
        legacy_state = TimestampSigner(salt="quotation-gmail-oauth").sign(
            str(self.staff.id)
        )

        callback = self.client.get(
            reverse("quotation-gmail-oauth-callback"),
            {"state": legacy_state, "code": "oauth-code"},
        )

        destination = urllib.parse.urlsplit(callback["Location"])
        self.assertEqual(destination.netloc, "frontend.example")
        self.assertEqual(destination.path, "/admin")
        self.assertEqual(
            urllib.parse.parse_qs(destination.query),
            {
                "quotation_tab": ["contract-intelligence"],
                "gmail": ["connected"],
            },
        )
        exchange.assert_called_once()

    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        GOOGLE_OAUTH_REDIRECT_URI="https://api.example.com/api/quotations/gmail/oauth/callback/",
    )
    def test_oauth_missing_scope_never_infers_gmail_send(self, form_request, json_request):
        form_request.return_value = {
            "access_token": "access-token",
            "refresh_token": "refresh-token",
            "expires_in": 3600,
        }
        json_request.return_value = {"emailAddress": self.connection.email}

        connection = exchange_gmail_code(self.staff, "auth-code")

        self.assertEqual(connection.scopes, [GMAIL_READONLY_SCOPE])
        self.assertNotIn(GMAIL_SEND_SCOPE, connection.scopes)
