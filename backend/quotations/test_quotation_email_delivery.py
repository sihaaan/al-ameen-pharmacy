import base64
import hashlib
import time
import urllib.error
import urllib.parse
from datetime import timedelta
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.signing import TimestampSigner
from django.db import connection
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Brand, Product

from .contract_intelligence import (
    GMAIL_READONLY_SCOPE,
    GMAIL_SEND_SCOPE,
    encrypt_token,
    exchange_gmail_code,
    gmail_fetch_reply_metadata,
    transfer_shared_gmail_credential_owner,
)
from .models import (
    Company,
    CompanyContact,
    GmailInquiryImport,
    GmailOAuthConnection,
    Quotation,
    QuotationAuditLog,
    QuotationEmailDelivery,
    QuotationEmailDeliveryAttempt,
    QuotationEmailDeliveryAttemptEvent,
    QuotationEmailOutboundSnapshot,
    QuotationEmailThreadSelection,
    QuotationLine,
    QuotationSettings,
)
from .quotation_email_delivery import (
    _delivery_snapshot,
    _mark_delivery_failure,
    _record_successful_delivery,
)


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
        self.latest_quotation = quotation
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
        label_ids=None,
        from_header_values=None,
        reply_to_header_values=None,
    ):
        if from_header_values is None:
            from_header_values = [sender]
        if reply_to_header_values is None:
            reply_to_header_values = [reply_to] if reply_to else []
        return {
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
            "label_ids": ["INBOX"] if label_ids is None else list(label_ids),
            "subject": subject,
            "sender": sender,
            "from_header_values": list(from_header_values),
            "reply_to": reply_to,
            "reply_to_header_values": list(reply_to_header_values),
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

    def preview_fingerprint(self, quotation=None, *, params=None):
        quotation = quotation or self.latest_quotation
        request_params = dict(params or {})
        request_params.setdefault(
            "quotation_review_fingerprint",
            self.quotation_review_fingerprint(quotation),
        )
        response = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            request_params,
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data.get("preview_fingerprint"))
        return response.data["preview_fingerprint"]

    def quotation_review_fingerprint(self, quotation=None):
        quotation = quotation or self.latest_quotation
        response = self.client.get(
            reverse("quotation-detail", args=[quotation.id]),
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data.get("quotation_review_fingerprint"))
        return response.data["quotation_review_fingerprint"]

    def manual_payload(self, *, include_preview=True, quotation=None, **overrides):
        payload = {
            "to": ["buyer@example.com"],
            "cc": [],
            "subject": "Quotation preview",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
        }
        if include_preview:
            payload["preview_fingerprint"] = self.preview_fingerprint(quotation)
        payload.update(overrides)
        return payload

    def gmail_payload(self, *, include_preview=True, quotation=None, **overrides):
        payload = {
            "to": ["orders@example.com"],
            "cc": [],
            "subject": "RFQ medical supplies",
            "body": "Please find the quotation attached.",
            "confirm_recipient": True,
        }
        if include_preview:
            payload["preview_fingerprint"] = self.preview_fingerprint(quotation)
        payload.update(overrides)
        return payload

    def test_manual_preview_is_read_only_and_prefills_explicit_contact(self):
        quotation = self.create_quote()
        response = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["delivery_mode"], "new_email")
        self.assertEqual(response.data["to"], ["buyer@example.com"])
        self.assertIsNone(response.data["delivery_id"])
        self.assertTrue(response.data["preview_fingerprint"])
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)

        repeated = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))
        self.assertEqual(
            repeated.data["preview_fingerprint"],
            response.data["preview_fingerprint"],
        )

    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_send_requires_a_reviewed_preview_before_any_side_effect(self, send, pdf):
        quotation = self.create_quote()

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(include_preview=False),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "email_preview_required")
        self.assertTrue(response.data["refresh_preview"])
        self.assertFalse(response.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action=QuotationAuditLog.ACTION_EMAIL_PREPARED,
            ).exists()
        )
        pdf.assert_not_called()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-current")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        return_value={"id": "gmail-current", "threadId": "thread-current"},
    )
    def test_changed_quotation_rejects_old_preview_then_fresh_review_sends(self, send, pdf):
        quotation = self.create_quote()
        old_fingerprint = self.preview_fingerprint(quotation)
        line = quotation.lines.get()
        line.item_name_snapshot = "Changed after preview"
        line.save(update_fields=["item_name_snapshot", "updated_at"])

        stale = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(
                include_preview=False,
                preview_fingerprint=old_fingerprint,
            ),
            format="json",
        )

        self.assertEqual(stale.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(stale.data["code"], "stale_email_preview")
        self.assertTrue(stale.data["refresh_preview"])
        self.assertFalse(stale.data["quote_finalized"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())
        pdf.assert_not_called()
        send.assert_not_called()

        fresh_fingerprint = self.preview_fingerprint(quotation)
        self.assertNotEqual(fresh_fingerprint, old_fingerprint)
        sent = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(
                include_preview=False,
                preview_fingerprint=fresh_fingerprint,
            ),
            format="json",
        )
        self.assertEqual(sent.status_code, status.HTTP_200_OK)
        self.assertEqual(sent.data["quote"]["status"], Quotation.STATUS_SENT)
        pdf.assert_called_once()
        send.assert_called_once()

    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_preview_fingerprint_is_bound_to_quote_and_staff_actor(self, send, pdf):
        quotation_a = self.create_quote()
        fingerprint_a = self.preview_fingerprint(quotation_a)
        quotation_b = self.create_quote()

        wrong_quote = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation_b.id]),
            self.manual_payload(
                include_preview=False,
                quotation=quotation_b,
                preview_fingerprint=fingerprint_a,
            ),
            format="json",
        )
        self.assertEqual(wrong_quote.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(wrong_quote.data["code"], "stale_email_preview")

        other_staff = User.objects.create_user(
            username="other-email-staff",
            password="pass",
            is_staff=True,
        )
        self.client.force_authenticate(other_staff)
        wrong_actor = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation_a.id]),
            self.manual_payload(
                include_preview=False,
                quotation=quotation_a,
                preview_fingerprint=fingerprint_a,
            ),
            format="json",
        )
        self.assertEqual(wrong_actor.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(wrong_actor.data["code"], "stale_email_preview")
        self.assertFalse(QuotationEmailDelivery.objects.exists())
        pdf.assert_not_called()
        send.assert_not_called()

    def test_preview_fingerprint_tracks_customer_facing_but_not_internal_state(self):
        quotation = self.create_quote()
        baseline = self.preview_fingerprint(quotation)

        quotation.outcome_notes = "Internal sales follow-up only"
        quotation.save(update_fields=["outcome_notes", "updated_at"])
        self.assertEqual(self.preview_fingerprint(quotation), baseline)

        self.company.billing_address = "New customer-facing billing address"
        self.company.save(update_fields=["billing_address", "updated_at"])
        company_changed = self.preview_fingerprint(quotation)
        self.assertNotEqual(company_changed, baseline)

        settings_obj = QuotationSettings.get_solo()
        settings_obj.default_terms = "Updated customer-facing quotation terms."
        settings_obj.save(update_fields=["default_terms", "updated_at"])
        self.assertNotEqual(self.preview_fingerprint(quotation), company_changed)

    def test_preview_rejects_a_quotation_revision_not_shown_in_the_editor(self):
        quotation = self.create_quote()
        displayed_fingerprint = self.quotation_review_fingerprint(quotation)

        current = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"quotation_review_fingerprint": displayed_fingerprint},
        )
        self.assertEqual(current.status_code, status.HTTP_200_OK, current.data)

        line = quotation.lines.get()
        line.item_name_snapshot = "Changed by another employee"
        line.save(update_fields=["item_name_snapshot", "updated_at"])
        stale = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"quotation_review_fingerprint": displayed_fingerprint},
        )

        self.assertEqual(stale.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(stale.data["code"], "stale_quotation_review")
        self.assertTrue(stale.data["refresh_quote"])
        self.assertEqual(
            stale.data["quote"]["lines"][0]["item_name_snapshot"],
            "Changed by another employee",
        )
        self.assertFalse(
            QuotationEmailDelivery.objects.filter(quotation=quotation).exists()
        )

    def test_preview_does_not_create_default_settings_or_invalidate_review(self):
        quotation = self.create_quote()
        self.assertFalse(QuotationSettings.objects.exists())
        displayed_fingerprint = self.quotation_review_fingerprint(quotation)

        response = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id]),
            {"quotation_review_fingerprint": displayed_fingerprint},
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertTrue(response.data["preview_fingerprint"])
        self.assertFalse(QuotationSettings.objects.exists())

    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_attachment_metadata_change_invalidates_reviewed_preview(self, send, pdf):
        quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_FAILED,
            to_addresses=["buyer@example.com"],
            subject="Quotation preview",
            body="Please find the quotation attached.",
            attachment_filename="EMAIL-CUSTOMER.pdf",
            attachment_sha256="a" * 64,
            attachment_size=123,
        )
        fingerprint = self.preview_fingerprint(quotation)
        delivery.attachment_filename = "UNREVIEWED-NAME.pdf"
        delivery.save(update_fields=["attachment_filename", "updated_at"])

        response = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(
                include_preview=False,
                quotation=quotation,
                preview_fingerprint=fingerprint,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "stale_email_preview")
        self.assertTrue(response.data["quote_finalized"])
        pdf.assert_not_called()
        send.assert_not_called()

    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        return_value={"id": "gmail-projected", "threadId": "thread-projected"},
    )
    def test_reviewed_draft_projection_matches_finalized_pdf_state(self, send):
        brand = Brand.objects.create(name="Projected Brand")
        self.product.brand = brand
        self.product.save(update_fields=["brand", "updated_at"])
        quotation = self.create_quote()
        quotation.subtotal = Decimal("999.00")
        quotation.vat_total = Decimal("999.00")
        quotation.total = Decimal("1998.00")
        quotation.save(update_fields=["subtotal", "vat_total", "total", "updated_at"])

        rendered = {}

        def render_pdf(current, *, config=None):
            current.refresh_from_db()
            line = current.lines.get()
            rendered.update(
                status=current.status,
                brand=line.brand_name_snapshot,
                subtotal=current.subtotal,
                total=current.total,
                sender=config.company_name,
            )
            return b"%PDF-projected"

        with patch(
            "quotations.quotation_email_delivery.build_quotation_pdf",
            side_effect=render_pdf,
        ):
            response = self.client.post(
                reverse("quotation-finalize-and-send", args=[quotation.id]),
                self.manual_payload(quotation=quotation),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(rendered["status"], Quotation.STATUS_FINALIZED)
        self.assertEqual(rendered["brand"], "Projected Brand")
        self.assertEqual(rendered["subtotal"], Decimal("20.00"))
        self.assertEqual(rendered["total"], Decimal("21.00"))
        self.assertTrue(rendered["sender"])
        send.assert_called_once()

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
        self.assertTrue(response.data["preview_fingerprint"])
        self.assertFalse(QuotationEmailDelivery.objects.filter(quotation=quotation).exists())

        fetch.return_value = self.source_metadata(
            message_id="gmail-followup-2",
            rfc_message_id="<changed-source@example.com>",
        )
        changed_source = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        self.assertNotEqual(
            changed_source.data["preview_fingerprint"],
            response.data["preview_fingerprint"],
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_reply_without_reply_to_uses_verified_physical_from(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(
            message_id="gmail-followup-2",
            reply_to="",
        )

        response = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["to"], ["buyer@example.com"])

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_reply_rejects_every_ambiguous_physical_from_shape(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        ambiguous_cases = [
            self.source_metadata(
                message_id="gmail-followup-2",
                sender="buyer@example.com, attacker@evil.example",
            ),
            self.source_metadata(
                message_id="gmail-followup-2",
                sender="buyer@example.com, buyer@example.com",
            ),
            self.source_metadata(
                message_id="gmail-followup-2",
                from_header_values=[
                    "Customer Buyer <buyer@example.com>",
                    "Customer Buyer <buyer@example.com>",
                ],
            ),
            self.source_metadata(
                message_id="gmail-followup-2",
                sender="malformed-address, Customer Buyer <buyer@example.com>",
            ),
        ]

        for metadata in ambiguous_cases:
            with self.subTest(metadata=metadata):
                fetch.return_value = metadata
                response = self.client.get(
                    reverse("quotation-email-preview", args=[quotation.id])
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(
                    response.data["code"],
                    "ambiguous_source_recipient",
                )
        self.assertFalse(
            QuotationEmailDelivery.objects.filter(quotation=quotation).exists()
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_gmail_reply_rejects_every_ambiguous_reply_to_shape(self, fetch):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        ambiguous_cases = [
            self.source_metadata(
                message_id="gmail-followup-2",
                reply_to="Bad <> , orders@example.com",
            ),
            self.source_metadata(
                message_id="gmail-followup-2",
                reply_to="orders@example.com, orders@example.com",
            ),
            self.source_metadata(
                message_id="gmail-followup-2",
                reply_to_header_values=[
                    "orders@example.com",
                    "attacker@evil.example",
                ],
            ),
        ]

        for metadata in ambiguous_cases:
            with self.subTest(metadata=metadata):
                fetch.return_value = metadata
                response = self.client.get(
                    reverse("quotation-email-preview", args=[quotation.id])
                )
                self.assertEqual(
                    response.status_code,
                    status.HTTP_400_BAD_REQUEST,
                )
                self.assertEqual(
                    response.data["code"],
                    "ambiguous_source_recipient",
                )

    @patch("quotations.contract_intelligence._json_request")
    @patch(
        "quotations.contract_intelligence.get_valid_access_token",
        return_value="access-token",
    )
    def test_reply_metadata_preserves_duplicate_header_fields(
        self,
        _token,
        request,
    ):
        request.return_value = {
            "id": "message-duplicates",
            "threadId": "thread-duplicates",
            "internalDate": "1785600000000",
            "payload": {
                "headers": [
                    {"name": "From", "value": "buyer@example.com"},
                    {"name": "From", "value": "attacker@evil.example"},
                    {"name": "Reply-To", "value": "orders@example.com"},
                    {"name": "Reply-To", "value": "other@example.com"},
                    {"name": "Subject", "value": "RFQ"},
                    {"name": "Message-ID", "value": "<rfq@example.com>"},
                ]
            },
        }

        metadata = gmail_fetch_reply_metadata(
            self.connection,
            "message-duplicates",
        )

        self.assertEqual(
            metadata["from_header_values"],
            ["buyer@example.com", "attacker@evil.example"],
        )
        self.assertEqual(
            metadata["reply_to_header_values"],
            ["orders@example.com", "other@example.com"],
        )

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
        raw_bytes = base64.urlsafe_b64decode(padded.encode("ascii"))
        message = BytesParser(policy=policy.default).parsebytes(raw_bytes)
        self.assertEqual(message["To"], "orders@example.com")
        self.assertEqual(message["Subject"], "RFQ medical supplies")
        self.assertEqual(message["In-Reply-To"], "<customer-1@example.com>")
        self.assertEqual(
            message["References"],
            "<older@example.com> <customer-1@example.com>",
        )
        self.assertTrue(message["Message-ID"].startswith("<quotation-"))
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(delivery=delivery)
        attempt = QuotationEmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(bytes(snapshot.raw_mime), raw_bytes)
        self.assertEqual(snapshot.gmail_api_thread_id, "gmail-thread-1")
        self.assertEqual(snapshot.source_rfc_message_id, "<customer-1@example.com>")
        self.assertEqual(snapshot.raw_mime_sha256, hashlib.sha256(bytes(snapshot.raw_mime)).hexdigest())
        self.assertEqual(attempt.sequence, 1)
        event = attempt.events.get()
        self.assertEqual(event.event_type, QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_SENT)
        self.assertEqual(event.provider_message_id, "gmail-sent-1")
        self.assertEqual(event.provider_thread_id, "gmail-thread-1")

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
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_late_provider_outcome_is_appended_after_reconciliation_wins(
        self,
        send,
        _pdf,
    ):
        quotation = self.create_quote()

        def reconcile_then_timeout(*_args, **_kwargs):
            delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
            attempt = delivery.provider_attempts.get()
            _record_successful_delivery(
                delivery.id,
                "gmail-reconciled-before-return",
                "gmail-reconciled-thread",
                self.staff,
                attempt_id=attempt.id,
                reconciled=True,
            )
            raise urllib.error.URLError("provider response arrived too late")

        send.side_effect = reconcile_then_timeout
        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENT)
        attempt = delivery.provider_attempts.get()
        events = list(attempt.events.order_by("created_at", "id"))
        self.assertEqual(
            [event.event_key for event in events],
            [
                "provider_unknown:reconciliation_pending",
                QuotationEmailDeliveryAttemptEvent.EVENT_RECONCILED_SENT,
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
            ],
        )
        self.assertEqual(events[-1].error_code, "delivery_unknown")
        self.assertEqual(events[-1].error_class, "URLError")
        self.assertFalse(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action__in=[
                    QuotationAuditLog.ACTION_EMAIL_FAILED,
                    QuotationAuditLog.ACTION_EMAIL_UNKNOWN,
                ],
            ).exists()
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
        self.assertTrue(response.data["refresh_preview"])
        self.assertTrue(response.data["delivery"]["outbound_snapshot_frozen"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_FAILED)
        self.assertTrue(hasattr(delivery, "outbound_snapshot"))
        attempt = delivery.provider_attempts.get()
        event = attempt.events.get()
        self.assertEqual(event.event_type, QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_FAILED)
        self.assertEqual(event.provider_http_status, 400)
        self.assertEqual(event.error_code, "gmail_send_failed")

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_real_pdf_known_failure_retries_once_without_refinalizing(self, send):
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
        first_pdf_hash = QuotationEmailDelivery.objects.get(
            quotation=quotation
        ).attachment_sha256
        retry_staff = User.objects.create_user(
            username="email-retry-staff",
            password="pass",
            is_staff=True,
        )
        self.client.force_authenticate(retry_staff)
        # Cross ReportLab's normal timestamp boundary. Without invariant PDF
        # output, identical visible content receives a different document ID.
        time.sleep(1.1)
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
        self.assertEqual(
            QuotationEmailDelivery.objects.get(quotation=quotation).attachment_sha256,
            first_pdf_hash,
        )
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
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.provider_attempts.count(), 2)
        self.assertEqual(
            list(
                QuotationEmailDeliveryAttemptEvent.objects.filter(
                    attempt__delivery=delivery
                ).order_by("attempt__sequence").values_list("event_type", flat=True)
            ),
            [
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_FAILED,
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_SENT,
            ],
        )
        self.assertEqual(
            list(delivery.provider_attempts.values_list("actor_username", flat=True)),
            [self.staff.username, retry_staff.username],
        )
        first_raw = send.call_args_list[0].args[1]
        second_raw = send.call_args_list[1].args[1]
        self.assertEqual(second_raw, first_raw)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"reply-pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_failed_gmail_reply_retry_reuses_exact_mime_and_original_thread(
        self,
        fetch,
        send,
        pdf,
    ):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(message_id="gmail-followup-2")
        send.side_effect = [
            RuntimeError("Google API request failed with HTTP 400: invalid"),
            {"id": "gmail-reply-retry", "threadId": "gmail-thread-1"},
        ]

        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.gmail_payload(),
            format="json",
        )
        fetches_before_retry = fetch.call_count
        retry_payload = self.gmail_payload()
        self.assertEqual(fetch.call_count, fetches_before_retry)
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            retry_payload,
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(pdf.call_count, 1)
        self.assertEqual(fetch.call_count, fetches_before_retry)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1], send.call_args_list[1].args[1])
        self.assertEqual(send.call_args_list[0].kwargs["thread_id"], "gmail-thread-1")
        self.assertEqual(send.call_args_list[1].kwargs["thread_id"], "gmail-thread-1")
        snapshot = QuotationEmailOutboundSnapshot.objects.get(
            delivery__quotation=quotation
        )
        self.assertEqual(snapshot.delivery_mode, QuotationEmailDelivery.MODE_GMAIL_REPLY)
        self.assertEqual(snapshot.gmail_api_thread_id, "gmail-thread-1")
        self.assertEqual(
            snapshot.delivery.trusted_source["sender_validation_contract"],
            "gmail_reply_sender_identity_v1",
        )

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"reply-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    def test_legacy_frozen_gmail_reply_without_sender_contract_cannot_retry(
        self,
        fetch,
        send,
        _pdf,
    ):
        quotation = self.create_quote()
        self.gmail_link(quotation)
        fetch.return_value = self.source_metadata(message_id="gmail-legacy-reply")
        retry_payload = self.gmail_payload(quotation=quotation)

        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            retry_payload,
            format="json",
        )
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        trusted_source = dict(delivery.trusted_source or {})
        trusted_source.pop("sender_validation_contract", None)
        delivery.trusted_source = trusted_source
        delivery.save(update_fields=["trusted_source", "updated_at"])
        fetches_before_retry = fetch.call_count

        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            retry_payload,
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(
            second.data["code"],
            "gmail_reply_source_reverification_required",
        )
        self.assertEqual(fetch.call_count, fetches_before_retry)
        self.assertEqual(send.call_count, 1)

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
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertFalse(
            QuotationEmailOutboundSnapshot.objects.filter(delivery=delivery).exists()
        )
        self.assertFalse(delivery.provider_attempts.exists())
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"legacy-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        return_value={"id": "gmail-legacy-retry", "threadId": "legacy-thread"},
    )
    def test_legacy_failed_delivery_freezes_first_honest_snapshot_on_retry(
        self,
        send,
        _pdf,
    ):
        quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_NEW_EMAIL,
            status=QuotationEmailDelivery.STATUS_FAILED,
            to_addresses=["buyer@example.com"],
            cc_addresses=[],
            subject="Quotation preview",
            body="Please find the quotation attached.",
            attachment_filename="legacy.pdf",
            attachment_sha256=hashlib.sha256(b"legacy-pdf").hexdigest(),
            attachment_size=len(b"legacy-pdf"),
            attempt_count=1,
        )

        response = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(delivery=delivery)
        attempt = QuotationEmailDeliveryAttempt.objects.get(delivery=delivery)
        self.assertEqual(snapshot.attachment_sha256, delivery.attachment_sha256)
        self.assertEqual(attempt.sequence, 2)
        self.assertEqual(
            attempt.events.get().event_type,
            QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_SENT,
        )
        send.assert_called_once()

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

    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_changed_gmail_credential_generation_aborts_before_finalization(
        self,
        send,
        pdf,
    ):
        quotation = self.create_quote()

        def rotate_credential(_connection):
            GmailOAuthConnection.objects.filter(pk=self.connection.pk).update(
                access_token_encrypted=encrypt_token("replacement-access-token"),
                token_expiry=timezone.now() + timedelta(hours=1),
            )
            return "access-token-from-prior-generation"

        with patch(
            "quotations.quotation_email_delivery.get_valid_access_token",
            side_effect=rotate_credential,
        ):
            response = self.client.post(
                reverse("quotation-finalize-and-send", args=[quotation.id]),
                self.manual_payload(),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "gmail_connection_changed")
        self.assertTrue(response.data["retryable"])
        quotation.refresh_from_db()
        self.assertEqual(quotation.status, Quotation.STATUS_DRAFT)
        send.assert_not_called()
        pdf.assert_not_called()
        self.assertFalse(
            QuotationEmailOutboundSnapshot.objects.filter(
                delivery__quotation=quotation
            ).exists()
        )
        self.assertFalse(
            QuotationEmailDeliveryAttempt.objects.filter(
                delivery__quotation=quotation
            ).exists()
        )

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
        attempt = QuotationEmailDeliveryAttempt.objects.get(
            delivery__quotation=quotation
        )
        event = attempt.events.get()
        self.assertEqual(event.event_type, QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN)
        self.assertEqual(event.error_code, "delivery_unknown")
        self.assertEqual(event.error_class, "RuntimeError")
        self.assertEqual(event.provider_http_status, 503)

        late = _mark_delivery_failure(
            attempt.delivery_id,
            unknown=False,
            message="A delayed handler classified the same call as rejected.",
            actor=self.staff,
            attempt_id=attempt.id,
            error_code="gmail_send_failed",
            error_class="LateDefiniteFailure",
            provider_http_status=400,
        )
        self.assertEqual(late.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        attempt.refresh_from_db()
        self.assertEqual(
            list(attempt.events.values_list("event_type", flat=True)),
            [
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_FAILED,
            ],
        )
        self.assertEqual(
            QuotationAuditLog.objects.filter(
                quotation=quotation,
                action=QuotationAuditLog.ACTION_EMAIL_FAILED,
            ).count(),
            0,
        )

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch(
        "quotations.quotation_email_delivery.gmail_search_messages",
        return_value={"messages": []},
    )
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=urllib.error.URLError("accepted but response lost"),
    )
    def test_unknown_delivery_does_not_refresh_send_token_after_genuine_not_found(
        self,
        send,
        _pdf,
        search,
        _fetch,
    ):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_409_CONFLICT)

        with patch(
            "quotations.quotation_email_delivery.get_valid_access_token",
            side_effect=AssertionError(
                "A delivery locked as unknown must not enter the send-token path."
            ),
        ) as token:
            second = self.client.post(
                reverse("quotation-send-email", args=[quotation.id]),
                self.manual_payload(),
                format="json",
            )

        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data["code"], "delivery_unknown")
        self.assertFalse(second.data["retryable"])
        token.assert_not_called()
        self.assertEqual(send.call_count, 1)
        self.assertEqual(search.call_count, 2)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_active_sending_delivery_does_not_refresh_send_token(self, send, _pdf):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        QuotationEmailDelivery.objects.filter(pk=delivery.pk).update(
            status=QuotationEmailDelivery.STATUS_SENDING,
            sending_started_at=timezone.now(),
        )

        with patch(
            "quotations.quotation_email_delivery.get_valid_access_token",
            side_effect=AssertionError(
                "An active delivery must not enter the send-token path."
            ),
        ) as token:
            second = self.client.post(
                reverse("quotation-send-email", args=[quotation.id]),
                self.manual_payload(),
                format="json",
            )

        self.assertEqual(second.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(second.data["code"], "delivery_in_progress")
        self.assertFalse(second.data["retryable"])
        token.assert_not_called()
        self.assertEqual(send.call_count, 1)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"attempt-order-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_older_attempt_failure_cannot_overwrite_newer_sending_state(self, _send, _pdf):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(delivery=delivery)
        first_attempt = delivery.provider_attempts.get(sequence=1)
        delivery.status = QuotationEmailDelivery.STATUS_SENDING
        delivery.attempt_count = 2
        delivery.save(update_fields=["status", "attempt_count", "updated_at"])
        QuotationEmailDeliveryAttempt.objects.create(
            delivery=delivery,
            snapshot=snapshot,
            sequence=2,
            actor=self.staff,
            actor_username=self.staff.username,
            gmail_connection_id_snapshot=self.connection.id,
            mailbox_email=snapshot.mailbox_email,
            raw_mime_sha256=snapshot.raw_mime_sha256,
            expected_thread_id=snapshot.gmail_api_thread_id,
        )

        late = _mark_delivery_failure(
            delivery.id,
            unknown=False,
            message="Late failure from attempt one.",
            actor=self.staff,
            attempt_id=first_attempt.id,
            error_code="gmail_send_failed",
            error_class="LateAttemptFailure",
            provider_http_status=400,
        )

        self.assertEqual(late.status, QuotationEmailDelivery.STATUS_SENDING)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_SENDING)
        self.assertEqual(delivery.attempt_count, 2)

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

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=urllib.error.URLError("accepted but response lost"),
    )
    def test_ambiguous_attempt_preserves_initial_outcome_when_reconciled(
        self,
        send,
        _pdf,
        search,
        fetch,
    ):
        quotation = self.create_quote()
        search.return_value = {"messages": [{"id": "gmail-proven-sent"}]}

        def metadata(*_args, **_kwargs):
            delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
            return self.source_metadata(
                message_id="gmail-proven-sent",
                thread_id="gmail-proven-thread",
                sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
                rfc_message_id=delivery.outbound_rfc_message_id,
                label_ids=["SENT"],
            )

        fetch.side_effect = metadata

        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["delivery"]["status"], "sent")
        attempt = QuotationEmailDeliveryAttempt.objects.get(
            delivery__quotation=quotation
        )
        events = list(attempt.events.order_by("created_at", "id"))
        self.assertEqual(
            [event.event_type for event in events],
            [
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
                QuotationEmailDeliveryAttemptEvent.EVENT_RECONCILED_SENT,
            ],
        )
        self.assertEqual(events[0].error_code, "delivery_unknown")
        self.assertEqual(events[0].error_class, "URLError")
        self.assertEqual(events[1].provider_message_id, "gmail-proven-sent")
        self.assertEqual(events[1].provider_thread_id, "gmail-proven-thread")
        self.assertEqual(send.call_count, 1)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    def test_repeated_click_after_success_is_idempotent(self, send, _pdf):
        quotation = self.create_quote()
        send.return_value = {"id": "gmail-sent-1", "threadId": "new-thread"}
        url = reverse("quotation-finalize-and-send", args=[quotation.id])

        first = self.client.post(url, self.manual_payload(), format="json")
        self.connection.status = GmailOAuthConnection.STATUS_DISCONNECTED
        self.connection.save(update_fields=["status", "updated_at"])
        second_payload = self.manual_payload()
        with patch(
            "quotations.quotation_email_delivery.get_valid_access_token",
            side_effect=AssertionError("A terminal SENT delivery must not refresh OAuth."),
        ) as token:
            second = self.client.post(url, second_payload, format="json")

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertTrue(second.data["idempotent"])
        self.assertEqual(send.call_count, 1)
        token.assert_not_called()

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
            sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            rfc_message_id=delivery.outbound_rfc_message_id,
            label_ids=["SENT"],
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
        query = search.call_args.args[1]
        self.assertIn("in:sent", query)
        self.assertIn("from:pharmacydxb@gmail.com", query)
        self.assertIn("rfc822msgid:", query)
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
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconcile_rejects_candidates_without_all_trust_signals(
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
        search.return_value = {"messages": [{"id": "gmail-candidate"}]}
        trusted = {
            "message_id": "gmail-candidate",
            "thread_id": "gmail-reconciled-thread",
            "sender": "Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            "rfc_message_id": delivery.outbound_rfc_message_id,
            "label_ids": ["SENT"],
        }
        variants = [
            {**trusted, "label_ids": ["INBOX"]},
            {**trusted, "sender": "Attacker <attacker@example.com>"},
            {**trusted, "rfc_message_id": "<wrong-message@example.com>"},
            {**trusted, "thread_id": ""},
        ]

        for metadata in variants:
            with self.subTest(metadata=metadata):
                fetch.return_value = self.source_metadata(**metadata)
                response = self.client.post(
                    reverse("quotation-reconcile-email", args=[quotation.id]),
                    {},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertFalse(response.data["reconciled"])
                delivery.refresh_from_db()
                quotation.refresh_from_db()
                self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
                self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconcile_never_accepts_duplicate_physical_from_fields(
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
        search.return_value = {"messages": [{"id": "gmail-ambiguous-from"}]}
        fetch.return_value = self.source_metadata(
            message_id="gmail-ambiguous-from",
            thread_id="gmail-reconciled-thread",
            sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            from_header_values=[
                "Al Ameen Pharmacy <pharmacydxb@gmail.com>",
                "Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            ],
            rfc_message_id=delivery.outbound_rfc_message_id,
            label_ids=["SENT"],
        )

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "gmail_reconciliation_unavailable")
        delivery.refresh_from_db()
        quotation.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        self.assertEqual(quotation.status, Quotation.STATUS_FINALIZED)
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_gmail_reply_reconciliation_requires_expected_thread(
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
            delivery_mode=QuotationEmailDelivery.MODE_GMAIL_REPLY,
            status=QuotationEmailDelivery.STATUS_UNKNOWN,
            to_addresses=["orders@example.com"],
            subject="RFQ medical supplies",
            body="Attached.",
            gmail_thread_id="expected-thread",
            source_gmail_message_id="source-message",
            source_rfc_message_id="<source@example.com>",
            attachment_filename="quotation.pdf",
            attempt_count=1,
        )
        search.return_value = {"messages": [{"id": "gmail-candidate"}]}
        fetch.return_value = self.source_metadata(
            message_id="gmail-candidate",
            thread_id="wrong-thread",
            sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            rfc_message_id=delivery.outbound_rfc_message_id,
            label_ids=["SENT"],
        )

        mismatch = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )
        self.assertEqual(mismatch.status_code, status.HTTP_200_OK)
        self.assertFalse(mismatch.data["reconciled"])

        fetch.return_value = self.source_metadata(
            message_id="gmail-candidate",
            thread_id="expected-thread",
            sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
            rfc_message_id=delivery.outbound_rfc_message_id,
            label_ids=["SENT"],
        )
        matched = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(matched.status_code, status.HTTP_200_OK)
        self.assertTrue(matched.data["reconciled"])
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"snapshot-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=urllib.error.URLError("accepted but response lost"),
    )
    def test_reconciliation_uses_frozen_snapshot_when_mutable_delivery_drifts(
        self,
        send,
        _pdf,
        search,
        fetch,
    ):
        quotation = self.create_quote()
        self.gmail_link(quotation)

        def metadata(_connection, message_id):
            if message_id == "gmail-followup-2":
                return self.source_metadata(message_id="gmail-followup-2")
            snapshot = QuotationEmailOutboundSnapshot.objects.get(
                delivery__quotation=quotation
            )
            return self.source_metadata(
                message_id=message_id,
                thread_id=snapshot.gmail_api_thread_id,
                sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
                rfc_message_id=snapshot.outbound_rfc_message_id,
                label_ids=["SENT"],
            )

        fetch.side_effect = metadata
        search.return_value = {"messages": []}
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.gmail_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_409_CONFLICT)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(
            delivery__quotation=quotation
        )
        QuotationEmailDelivery.objects.filter(quotation=quotation).update(
            outbound_rfc_message_id="invalid mutable message id",
            gmail_thread_id="tampered-thread",
        )
        search.return_value = {"messages": [{"id": "gmail-frozen-proof"}]}

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["reconciled"])
        query = search.call_args.args[1]
        self.assertIn(snapshot.outbound_rfc_message_id[1:-1], query)
        self.assertEqual(fetch.call_args.args[1], "gmail-frozen-proof")
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.sent_gmail_thread_id, snapshot.gmail_api_thread_id)
        event_types = list(
            delivery.provider_attempts.get().events.values_list("event_type", flat=True)
        )
        self.assertEqual(
            event_types,
            [
                QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN,
                QuotationEmailDeliveryAttemptEvent.EVENT_RECONCILED_SENT,
            ],
        )
        self.assertEqual(send.call_count, 1)

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_gmail_reply_reconciliation_rejects_missing_expected_thread(
        self,
        search,
        send,
    ):
        quotation = self.create_quote(status_value=Quotation.STATUS_FINALIZED)
        delivery = QuotationEmailDelivery.objects.create(
            quotation=quotation,
            gmail_connection=self.connection,
            actor=self.staff,
            delivery_mode=QuotationEmailDelivery.MODE_GMAIL_REPLY,
            status=QuotationEmailDelivery.STATUS_UNKNOWN,
            to_addresses=["orders@example.com"],
            subject="RFQ medical supplies",
            body="Attached.",
            gmail_thread_id="",
            source_gmail_message_id="source-message",
            source_rfc_message_id="<source@example.com>",
            attachment_filename="quotation.pdf",
            attempt_count=1,
        )

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "delivery_reconciliation_invalid")
        self.assertFalse(response.data["retryable"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        search.assert_not_called()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconciliation_requires_the_shared_mailbox_connection(
        self,
        search,
        send,
    ):
        self.connection.is_shared = False
        self.connection.save(update_fields=["is_shared", "updated_at"])
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

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "gmail_reconnect_required")
        self.assertFalse(response.data["retryable"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        search.assert_not_called()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconciliation_rejects_malformed_stored_message_id(
        self,
        search,
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
        QuotationEmailDelivery.objects.filter(pk=delivery.pk).update(
            outbound_rfc_message_id="invalid id from legacy data"
        )

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "delivery_reconciliation_invalid")
        self.assertFalse(response.data["retryable"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        search.assert_not_called()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconciliation_rejects_multiple_fully_verified_candidates(
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
            delivery_mode=QuotationEmailDelivery.MODE_GMAIL_REPLY,
            status=QuotationEmailDelivery.STATUS_UNKNOWN,
            to_addresses=["buyer@example.com"],
            subject="Quotation",
            body="Attached.",
            gmail_thread_id="expected-thread",
            attachment_filename="quotation.pdf",
            attempt_count=1,
        )
        search.return_value = {
            "messages": [{"id": "gmail-candidate-1"}, {"id": "gmail-candidate-2"}]
        }
        fetch.side_effect = [
            self.source_metadata(
                message_id=candidate_id,
                thread_id="expected-thread",
                sender="Al Ameen Pharmacy <pharmacydxb@gmail.com>",
                rfc_message_id=delivery.outbound_rfc_message_id,
                label_ids=["SENT"],
            )
            for candidate_id in ("gmail-candidate-1", "gmail-candidate-2")
        ]

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["code"], "delivery_reconciliation_ambiguous")
        self.assertFalse(response.data["retryable"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        self.assertEqual(fetch.call_count, 2)
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconciliation_does_not_accept_ambiguous_from_header(
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
        search.return_value = {"messages": [{"id": "gmail-candidate"}]}
        fetch.return_value = self.source_metadata(
            message_id="gmail-candidate",
            thread_id="gmail-thread",
            sender=(
                "Al Ameen Pharmacy <pharmacydxb@gmail.com>, "
                "Attacker <attacker@example.com>"
            ),
            rfc_message_id=delivery.outbound_rfc_message_id,
            label_ids=["SENT"],
        )

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "gmail_reconciliation_unavailable")
        self.assertFalse(response.data["retryable"])
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        fetch.assert_called_once()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch(
        "quotations.quotation_email_delivery.gmail_search_messages",
        side_effect=RuntimeError("Google API request failed with HTTP 503: unavailable"),
    )
    def test_reconcile_api_failure_is_not_reported_as_not_found(self, search, send):
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

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "gmail_reconciliation_unavailable")
        self.assertFalse(response.data["retryable"])
        self.assertNotIn("reconciled", response.data)
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        search.assert_called_once()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch(
        "quotations.quotation_email_delivery.gmail_fetch_reply_metadata",
        side_effect=RuntimeError("Google API request failed with HTTP 503: unavailable"),
    )
    @patch("quotations.quotation_email_delivery.gmail_search_messages")
    def test_reconcile_metadata_failure_is_not_reported_as_not_found(
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
        search.return_value = {"messages": [{"id": "gmail-candidate"}]}

        response = self.client.post(
            reverse("quotation-reconcile-email", args=[quotation.id]),
            {},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["code"], "gmail_reconciliation_unavailable")
        delivery.refresh_from_db()
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        fetch.assert_called_once()
        send.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_fetch_reply_metadata")
    @patch(
        "quotations.quotation_email_delivery.gmail_search_messages",
        side_effect=RuntimeError("Google API request failed with HTTP 503: unavailable"),
    )
    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"%PDF-test")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=urllib.error.URLError("timeout"),
    )
    def test_ambiguous_send_with_reconciliation_failure_stays_locked_unknown(
        self,
        send,
        _pdf,
        search,
        fetch,
    ):
        quotation = self.create_quote()
        url = reverse("quotation-finalize-and-send", args=[quotation.id])

        first = self.client.post(url, self.manual_payload(), format="json")
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(first.data["code"], "gmail_reconciliation_unavailable")
        self.assertEqual(second.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(second.data["code"], "gmail_reconciliation_unavailable")
        self.assertFalse(first.data["retryable"])
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.status, QuotationEmailDelivery.STATUS_UNKNOWN)
        attempt = delivery.provider_attempts.get()
        initial_event = attempt.events.get(
            event_type=QuotationEmailDeliveryAttemptEvent.EVENT_PROVIDER_UNKNOWN
        )
        self.assertEqual(initial_event.error_code, "delivery_unknown")
        self.assertEqual(initial_event.error_class, "URLError")
        self.assertFalse(
            attempt.events.filter(
                event_type=QuotationEmailDeliveryAttemptEvent.EVENT_RECONCILED_SENT
            ).exists()
        )
        self.assertEqual(send.call_count, 1)
        self.assertEqual(search.call_count, 2)
        fetch.assert_not_called()

    @patch("quotations.quotation_email_delivery.gmail_send_raw_message")
    @patch("quotations.quotation_email_delivery.build_quotation_pdf")
    def test_retry_uses_persisted_mime_without_rerendering_pdf(self, pdf, send):
        quotation = self.create_quote()
        pdf.side_effect = [b"first-pdf", b"changed-pdf"]
        send.side_effect = [
            RuntimeError("Google API request failed with HTTP 400: invalid"),
            {"id": "gmail-retry", "threadId": "gmail-retry-thread"},
        ]

        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        frozen_preview = self.client.get(
            reverse("quotation-email-preview", args=[quotation.id])
        )
        second = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertTrue(frozen_preview.data["outbound_snapshot_frozen"])
        self.assertNotIn("raw_mime", frozen_preview.data)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.assertEqual(pdf.call_count, 1)
        self.assertEqual(send.call_count, 2)
        self.assertEqual(send.call_args_list[0].args[1], send.call_args_list[1].args[1])
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)
        self.assertEqual(delivery.provider_attempts.count(), 2)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"first-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_frozen_retry_rejects_edited_customer_facing_fields(self, send, _pdf):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(
                cc=["accounts@example.com"],
                subject="Reviewed subject",
                body="Reviewed body",
            ),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        frozen = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))

        for field, value in [
            ("to", ["different@example.com"]),
            ("cc", ["different@example.com"]),
            ("subject", "Different subject"),
            ("body", "Different body"),
        ]:
            with self.subTest(field=field):
                payload = {
                    "to": frozen.data["to"],
                    "cc": frozen.data["cc"],
                    "subject": frozen.data["subject"],
                    "body": frozen.data["body"],
                    "confirm_recipient": True,
                    "preview_fingerprint": frozen.data["preview_fingerprint"],
                    field: value,
                }
                response = self.client.post(
                    reverse("quotation-send-email", args=[quotation.id]),
                    payload,
                    format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
                self.assertEqual(response.data["code"], "outbound_snapshot_mismatch")
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            QuotationEmailDeliveryAttempt.objects.filter(
                delivery__quotation=quotation
            ).count(),
            1,
        )

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"deferred-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_metadata_snapshot_fetch_defers_raw_mime_for_preview_paths(self, _send, _pdf):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        delivery = QuotationEmailDelivery.objects.get(quotation=quotation)

        metadata_snapshot = _delivery_snapshot(delivery)
        self.assertIn("raw_mime", metadata_snapshot.get_deferred_fields())
        preview = self.client.get(reverse("quotation-email-preview", args=[quotation.id]))
        self.assertEqual(preview.status_code, status.HTTP_200_OK)
        self.assertTrue(preview.data["outbound_snapshot_frozen"])
        self.assertNotIn("raw_mime", preview.data)

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"first-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_corrupt_frozen_mime_blocks_retry_before_gmail(self, send, _pdf):
        quotation = self.create_quote()
        first = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(
            delivery__quotation=quotation
        )
        table_name = connection.ops.quote_name(snapshot._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE {table_name} SET raw_mime = %s WHERE id = %s",
                [b"corrupt", snapshot.pk],
            )

        response = self.client.post(
            reverse("quotation-send-email", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["code"], "outbound_snapshot_corrupt")
        self.assertFalse(response.data["retryable"])
        self.assertFalse(response.data["refresh_preview"])
        self.assertEqual(send.call_count, 1)
        self.assertEqual(
            QuotationEmailDeliveryAttempt.objects.filter(
                delivery__quotation=quotation
            ).count(),
            1,
        )

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
        self.assertTrue(preview.data["outbound_snapshot_frozen"])

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"immutable-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_snapshot_and_completed_attempt_reject_mutation_and_deletion(self, _send, _pdf):
        quotation = self.create_quote()
        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(
            delivery__quotation=quotation
        )
        attempt = QuotationEmailDeliveryAttempt.objects.get(
            delivery__quotation=quotation
        )
        event = attempt.events.get()

        snapshot.subject = "Tampered"
        with self.assertRaises(ValidationError):
            snapshot.save()
        with self.assertRaises(ValidationError):
            snapshot.delete()
        with self.assertRaises(ValidationError):
            QuotationEmailOutboundSnapshot.objects.filter(pk=snapshot.pk).update(
                subject="Bulk tamper"
            )
        with self.assertRaises(ValidationError):
            QuotationEmailOutboundSnapshot.objects.filter(pk=snapshot.pk).delete()

        attempt.actor_username = "Tampered"
        with self.assertRaises(ValidationError):
            attempt.save()
        with self.assertRaises(ValidationError):
            attempt.delete()
        with self.assertRaises(ValidationError):
            QuotationEmailDeliveryAttempt.objects.filter(pk=attempt.pk).update(
                actor_username="Bulk tamper"
            )
        with self.assertRaises(ValidationError):
            QuotationEmailDeliveryAttempt.objects.filter(pk=attempt.pk).delete()

        event.error_summary = "Tampered"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        with self.assertRaises(ValidationError):
            QuotationEmailDeliveryAttemptEvent.objects.filter(pk=event.pk).update(
                error_summary="Bulk tamper"
            )
        with self.assertRaises(ValidationError):
            QuotationEmailDeliveryAttemptEvent.objects.filter(pk=event.pk).delete()

    @patch("quotations.quotation_email_delivery.build_quotation_pdf", return_value=b"immutable-pdf")
    @patch(
        "quotations.quotation_email_delivery.gmail_send_raw_message",
        side_effect=RuntimeError("Google API request failed with HTTP 400: invalid"),
    )
    def test_employee_deletion_nulls_history_actor_but_retains_username(self, _send, _pdf):
        quotation = self.create_quote()
        response = self.client.post(
            reverse("quotation-finalize-and-send", args=[quotation.id]),
            self.manual_payload(),
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        snapshot = QuotationEmailOutboundSnapshot.objects.get(
            delivery__quotation=quotation
        )
        attempt = QuotationEmailDeliveryAttempt.objects.get(
            delivery__quotation=quotation
        )
        event = attempt.events.get()
        username = self.staff.username

        successor = User.objects.create_user(
            username="email-credential-successor",
            is_staff=True,
        )
        transfer_admin = User.objects.create_superuser(
            username="email-transfer-admin",
            email="email-transfer-admin@example.com",
            password="pass",
        )
        with override_settings(
            GMAIL_ADDON_SHARED_MAILBOX_EMAIL="pharmacydxb@gmail.com",
            QUOTATION_GMAIL_DESIGNATED_MAILBOX_ENFORCEMENT_ENABLED=True,
        ):
            transfer_result = transfer_shared_gmail_credential_owner(
                initiated_by=transfer_admin,
                new_owner=successor,
                confirmed_mailbox="pharmacydxb@gmail.com",
                apply=True,
            )
        self.assertTrue(transfer_result["applied"])

        self.staff.delete()

        snapshot.refresh_from_db()
        attempt.refresh_from_db()
        event.refresh_from_db()
        self.assertIsNone(snapshot.created_by_id)
        self.assertIsNone(attempt.actor_id)
        self.assertIsNone(event.actor_id)
        self.assertEqual(snapshot.created_by_username, username)
        self.assertEqual(attempt.actor_username, username)
        self.assertEqual(event.actor_username, username)

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
