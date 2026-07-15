import base64
from datetime import timedelta
from decimal import Decimal
from email.message import EmailMessage
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection as db_connection
from django.test import TestCase
from django.utils import timezone

from .import_parsers import parse_file_preview
from .mailbox_po_audit import (
    _attachment_identity,
    _preview_attachment,
    build_mailbox_po_query,
    classify_mailbox_message,
    earliest_eligible_quote_boundary,
    extract_po_references,
    fetch_mailbox_message,
    gmail_list_mailbox_messages,
    hydrate_plausible_attachments,
    run_mailbox_po_audit,
    scan_mailbox_po_audit_page,
    start_mailbox_po_audit,
)
from .models import (
    Company,
    GmailOAuthConnection,
    MailboxPOAuditFailure,
    MailboxPOAuditRun,
    MailboxPOMessage,
    ProformaInvoice,
    Quotation,
    QuotationLine,
)


class MailboxPOAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mailbox-auditor", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.user,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(name="Mailbox Audit Customer")
        self.quote = Quotation.objects.create(
            company=self.company,
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now(),
            created_by=self.user,
        )

    def message(self, message_id, *, subject="General update", body="Hello", labels=None):
        return {
            "gmail_message_id": message_id,
            "gmail_thread_id": f"thread-{message_id}",
            "label_ids": labels or ["INBOX"],
            "full_headers": [
                {"name": "Subject", "value": subject},
                {"name": "X-Customer-Header", "value": "preserved"},
            ],
            "subject": subject,
            "sender": "buyer@example.test",
            "recipients": self.connection.email,
            "cc": "finance@example.test",
            "reply_to": "purchasing@example.test",
            "sent_at": timezone.now(),
            "snippet": body[:30],
            "newest_body_text": body,
            "attachment_manifest": [],
            "_attachment_refs": [],
        }

    def test_boundary_is_first_non_historical_quote_creation_even_if_cancelled(self):
        now = timezone.now()
        historical = Quotation.objects.create(
            company=self.company,
            status=Quotation.STATUS_SENT,
            is_historical_import=True,
        )
        cancelled = Quotation.objects.create(company=self.company, status=Quotation.STATUS_CANCELLED)
        Quotation.objects.filter(pk=historical.pk).update(created_at=now - timedelta(days=20))
        Quotation.objects.filter(pk=cancelled.pk).update(created_at=now - timedelta(days=10))
        Quotation.objects.filter(pk=self.quote.pk).update(created_at=now - timedelta(days=1))

        self.assertEqual(earliest_eligible_quote_boundary(), now - timedelta(days=10))
        cutoff = now.replace(microsecond=0)
        query = build_mailbox_po_query(now - timedelta(days=10), cutoff)
        self.assertIn("in:anywhere", query)
        self.assertIn("-from:me", query)
        self.assertIn(f"before:{int(cutoff.timestamp())}", query)
        self.assertLess(query.index("after:"), query.index("before:"))

    def test_new_run_freezes_a_second_granular_upper_mailbox_boundary(self):
        run = start_mailbox_po_audit(self.connection, requested_by=self.user)

        self.assertEqual(run.mailbox_cutoff_at.microsecond, 0)
        self.assertIn(f"before:{int(run.mailbox_cutoff_at.timestamp())}", run.gmail_query)
        self.assertIn(f"after:{max(int(run.earliest_quote_at.timestamp()) - 1, 0)}", run.gmail_query)

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._json_request")
    def test_global_listing_explicitly_includes_spam_and_trash(self, request, _token):
        request.return_value = {"messages": [], "nextPageToken": "next", "resultSizeEstimate": 123}

        result = gmail_list_mailbox_messages(
            self.connection,
            "in:anywhere after:1 -from:me",
            page_size=500,
            page_token="cursor",
        )

        url = request.call_args.args[0]
        self.assertIn("includeSpamTrash=true", url)
        self.assertIn("maxResults=500", url)
        self.assertIn("pageToken=cursor", url)
        self.assertEqual(result["next_page_token"], "next")

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._json_request")
    def test_full_fetch_preserves_all_headers_and_newest_body_without_attachment_bytes(self, request, _token):
        encoded_body = base64.urlsafe_b64encode(
            b"New purchase order details\nOn Monday Buyer wrote:\n> old quoted details"
        ).decode("ascii").rstrip("=")
        request.return_value = {
            "id": "full-message",
            "threadId": "thread-full-message",
            "labelIds": ["INBOX", "IMPORTANT"],
            "internalDate": str(int(timezone.now().timestamp() * 1000)),
            "snippet": "New purchase order details",
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "LPO 77881"},
                    {"name": "From", "value": "buyer@example.test"},
                    {"name": "X-Unusual-Header", "value": "must survive"},
                ],
                "parts": [
                    {"mimeType": "text/plain", "body": {"data": encoded_body}},
                    {
                        "filename": "LPO-77881.pdf",
                        "mimeType": "application/pdf",
                        "partId": "2",
                        "body": {"attachmentId": "gmail-attachment", "size": 1000},
                    },
                ],
            },
        }

        result = fetch_mailbox_message(self.connection, "full-message")

        self.assertEqual(result["newest_body_text"], "New purchase order details")
        self.assertEqual(result["full_headers"][2], {"name": "X-Unusual-Header", "value": "must survive"})
        self.assertEqual(result["label_ids"], ["INBOX", "IMPORTANT"])
        self.assertEqual(result["attachment_manifest"][0]["attachment_id"], "gmail-attachment")
        self.assertNotIn("_inline_data", result["attachment_manifest"][0])
        self.assertEqual(request.call_count, 1)

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._json_request")
    def test_full_fetch_hydrates_a_large_detached_email_body(self, request, _token):
        encoded_body = base64.urlsafe_b64encode(b"Detached LPO body with item quantity 12").decode("ascii").rstrip("=")
        request.side_effect = [
            {
                "id": "detached-body-message",
                "payload": {
                    "mimeType": "multipart/alternative",
                    "headers": [{"name": "Subject", "value": "Purchase Order"}],
                    "parts": [
                        {
                            "mimeType": "text/plain",
                            "partId": "1",
                            "body": {"attachmentId": "large-body-part", "size": 39},
                        }
                    ],
                },
            },
            {"data": encoded_body},
        ]

        result = fetch_mailbox_message(self.connection, "detached-body-message")

        self.assertEqual(result["newest_body_text"], "Detached LPO body with item quantity 12")
        self.assertIn("/attachments/large-body-part", request.call_args_list[1].args[0])

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_paginates_past_ten_and_resumes_from_saved_cursor(self, list_messages, fetch, _hydrate):
        first_ids = [f"message-{index}" for index in range(12)]
        list_messages.side_effect = [
            {
                "messages": [{"id": message_id} for message_id in first_ids],
                "next_page_token": "page-2",
                "result_size_estimate": 13,
            },
            {
                "messages": [{"id": "message-12"}],
                "next_page_token": "",
                "result_size_estimate": 13,
            },
        ]
        fetch.side_effect = lambda _connection, message_id: self.message(
            message_id,
            subject=f"Purchase Order {message_id}",
            body=f"Please find PO {message_id}",
        )
        run = start_mailbox_po_audit(self.connection, requested_by=self.user)

        first_result = scan_mailbox_po_audit_page(run)
        self.assertEqual(first_result.status, MailboxPOAuditRun.STATUS_RUNNING)
        self.assertEqual(first_result.page_token, "page-2")
        self.assertEqual(first_result.messages_scanned, 12)

        resumed = scan_mailbox_po_audit_page(MailboxPOAuditRun.objects.get(pk=run.pk))
        self.assertEqual(resumed.status, MailboxPOAuditRun.STATUS_COMPLETED)
        self.assertTrue(resumed.exhausted)
        self.assertEqual(resumed.messages_scanned, 13)
        self.assertEqual(MailboxPOMessage.objects.count(), 13)
        self.assertEqual(list_messages.call_args_list[0].kwargs["page_token"], "")
        self.assertEqual(list_messages.call_args_list[1].kwargs["page_token"], "page-2")
        stored = MailboxPOMessage.objects.get(gmail_message_id="message-12")
        self.assertEqual(stored.full_headers[1]["name"], "X-Customer-Header")
        self.assertEqual(stored.newest_body_text, "Please find PO message-12")

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_second_run_is_idempotent_against_canonical_message(self, list_messages, fetch, _hydrate):
        list_messages.return_value = {
            "messages": [{"id": "same-message"}],
            "next_page_token": "",
            "result_size_estimate": 1,
        }
        fetch.return_value = self.message("same-message", subject="LPO 12345", body="LPO 12345 attached")

        first = run_mailbox_po_audit(start_mailbox_po_audit(self.connection, requested_by=self.user))
        second = run_mailbox_po_audit(start_mailbox_po_audit(self.connection, requested_by=self.user))

        self.assertEqual(MailboxPOMessage.objects.count(), 1)
        canonical = MailboxPOMessage.objects.get()
        self.assertEqual(canonical.first_seen_run, first)
        self.assertEqual(canonical.last_seen_run, second)
        self.assertEqual(first.messages_created, 1)
        self.assertEqual(second.messages_created, 0)

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_failed_full_body_fetch_does_not_advance_cursor_and_can_resume(self, list_messages, fetch, _hydrate):
        list_messages.return_value = {
            "messages": [{"id": "retry-message"}],
            "next_page_token": "page-2",
            "result_size_estimate": 2,
        }
        fetch.side_effect = RuntimeError("temporary Gmail failure")
        run = start_mailbox_po_audit(self.connection, requested_by=self.user)

        failed = scan_mailbox_po_audit_page(run)
        self.assertEqual(failed.status, MailboxPOAuditRun.STATUS_FAILED)
        self.assertEqual(failed.page_token, "")
        self.assertEqual(failed.messages_scanned, 0)

        fetch.side_effect = None
        fetch.return_value = self.message("retry-message")
        resumed = scan_mailbox_po_audit_page(failed)
        self.assertEqual(resumed.status, MailboxPOAuditRun.STATUS_RUNNING)
        self.assertEqual(resumed.page_token, "page-2")
        self.assertEqual(resumed.messages_scanned, 1)

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_persistent_message_failure_is_tombstoned_after_three_attempts(self, list_messages, fetch, _hydrate):
        list_messages.return_value = {
            "messages": [{"id": "permanently-unreadable"}],
            "next_page_token": "",
            "result_size_estimate": 1,
        }
        fetch.side_effect = RuntimeError("Gmail message cannot be fetched")
        run = start_mailbox_po_audit(self.connection, requested_by=self.user)

        first = scan_mailbox_po_audit_page(run)
        second = scan_mailbox_po_audit_page(first)
        completed = scan_mailbox_po_audit_page(second)

        failure = MailboxPOAuditFailure.objects.get(
            audit_run=run,
            gmail_message_id="permanently-unreadable",
        )
        self.assertEqual(first.status, MailboxPOAuditRun.STATUS_FAILED)
        self.assertEqual(second.status, MailboxPOAuditRun.STATUS_FAILED)
        self.assertEqual(completed.status, MailboxPOAuditRun.STATUS_COMPLETED)
        self.assertTrue(completed.exhausted)
        self.assertEqual(completed.incomplete_messages, 1)
        self.assertEqual(completed.messages_scanned, 1)
        self.assertEqual(failure.attempts, 3)
        self.assertEqual(failure.status, MailboxPOAuditFailure.STATUS_TOMBSTONED)
        self.assertIsNotNone(failure.tombstoned_at)

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_gmail_network_and_attachment_work_run_outside_page_lock_transaction(
        self,
        list_messages,
        fetch,
        hydrate,
    ):
        baseline_atomic_depth = len(db_connection.atomic_blocks)

        def assert_outside_extra_atomic(*_args, **_kwargs):
            self.assertEqual(len(db_connection.atomic_blocks), baseline_atomic_depth)

        list_messages.side_effect = lambda *_args, **_kwargs: (
            assert_outside_extra_atomic()
            or {"messages": [{"id": "no-long-lock"}], "next_page_token": "", "result_size_estimate": 1}
        )
        fetch.side_effect = lambda _connection, message_id: (
            assert_outside_extra_atomic() or self.message(message_id)
        )
        hydrate.side_effect = lambda *_args, **_kwargs: (assert_outside_extra_atomic() or ([], 0, 0))

        completed = scan_mailbox_po_audit_page(
            start_mailbox_po_audit(self.connection, requested_by=self.user)
        )

        self.assertEqual(completed.status, MailboxPOAuditRun.STATUS_COMPLETED)

    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_unexpired_page_lease_rejects_a_second_scanner(self, list_messages):
        run = start_mailbox_po_audit(self.connection, requested_by=self.user)
        MailboxPOAuditRun.objects.filter(pk=run.pk).update(
            scan_lease_token="owned-by-another-worker",
            scan_lease_expires_at=timezone.now() + timedelta(minutes=5),
        )

        with self.assertRaisesMessage(RuntimeError, "already being scanned"):
            scan_mailbox_po_audit_page(run)

        list_messages.assert_not_called()

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_audit_does_not_change_quote_line_outcomes_or_create_orders(self, list_messages, fetch, _hydrate):
        line = QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot="First aid kit",
            quantity=Decimal("3"),
            unit_price=Decimal("20"),
        )
        before_quote = (
            self.quote.outcome_status,
            self.quote.outcome_date,
            self.quote.outcome_notes,
            self.quote.outcome_last_updated_at,
        )
        before_line = (
            line.outcome_status,
            line.accepted_quantity,
            line.accepted_unit_price,
            line.accepted_total,
            line.lost_value,
        )
        list_messages.return_value = {
            "messages": [{"id": "read-only-message"}],
            "next_page_token": "",
            "result_size_estimate": 1,
        }
        fetch.return_value = self.message(
            "read-only-message",
            subject=f"Purchase Order for {self.quote.quotation_number}",
            body="First aid kit, quantity 3, unit price 20, total 60",
        )

        result = run_mailbox_po_audit(start_mailbox_po_audit(self.connection, requested_by=self.user))

        self.quote.refresh_from_db()
        line.refresh_from_db()
        self.assertEqual(result.status, MailboxPOAuditRun.STATUS_COMPLETED)
        self.assertEqual(
            (
                self.quote.outcome_status,
                self.quote.outcome_date,
                self.quote.outcome_notes,
                self.quote.outcome_last_updated_at,
            ),
            before_quote,
        )
        self.assertEqual(
            (
                line.outcome_status,
                line.accepted_quantity,
                line.accepted_unit_price,
                line.accepted_total,
                line.lost_value,
            ),
            before_line,
        )
        self.assertEqual(ProformaInvoice.objects.count(), 0)

    @patch("quotations.mailbox_po_audit.hydrate_plausible_attachments", return_value=([], 0, 0))
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    @patch("quotations.mailbox_po_audit.gmail_list_mailbox_messages")
    def test_completed_run_is_immutable(self, list_messages, fetch, _hydrate):
        list_messages.return_value = {"messages": [], "next_page_token": "", "result_size_estimate": 0}
        run = run_mailbox_po_audit(start_mailbox_po_audit(self.connection, requested_by=self.user))

        with self.assertRaisesMessage(ValueError, "immutable"):
            scan_mailbox_po_audit_page(run)
        run.messages_scanned = 999
        with self.assertRaisesMessage(ValidationError, "immutable"):
            run.save()
        with self.assertRaisesMessage(ValidationError, "immutable"):
            run.delete()

    def test_spam_and_trash_remain_reviewable_but_cannot_auto_link(self):
        message = self.message(
            "spam-po",
            subject=f"Purchase Order {self.quote.quotation_number}",
            body="LPO 77881 attached",
            labels=["SPAM"],
        )

        result = classify_mailbox_message(message)

        self.assertTrue(result["is_relevant"])
        self.assertFalse(result["auto_link_eligible"])
        self.assertIn("Auto-link blocked", result["relevance_reason"])
        self.assertIn(
            {"kind": "quotation", "value": self.quote.quotation_number},
            result["extracted_po_references"],
        )

    def test_po_box_punctuation_is_never_extracted_as_purchase_order_reference(self):
        signatures = [
            "P.O. Box-123979, Dubai",
            "P.O. BOX/123979, Dubai",
            "P.O. BOX.123979, Dubai",
            "PO Box-123979, Dubai",
            "POBOX123979, Dubai",
        ]
        for signature in signatures:
            with self.subTest(signature=signature):
                result = classify_mailbox_message(
                    self.message("po-box", subject="Contact details", body=signature)
                )
                self.assertEqual(result["classification"], MailboxPOMessage.CLASS_OTHER)
                self.assertFalse(
                    any(
                        reference["kind"] == "po"
                        for reference in result["extracted_po_references"]
                    )
                )

        for genuine in ["P.O. 123 attached", "PO-123 attached"]:
            with self.subTest(genuine=genuine):
                result = classify_mailbox_message(
                    self.message("real-po", subject="Order", body=genuine)
                )
                self.assertEqual(
                    result["classification"],
                    MailboxPOMessage.CLASS_PURCHASE_ORDER,
                )
                self.assertTrue(
                    any(
                        reference["kind"] == "po"
                        for reference in result["extracted_po_references"]
                    )
                )

    def test_purchase_order_heading_supplier_code_does_not_mask_order_number(self):
        references = extract_po_references(
            "Purchase Order\n"
            "ALAM004\n"
            "Al Ameen Pharmacy LLC\n"
            "Order No. :\n"
            "HM-201A26002/0029\n"
        )

        self.assertEqual(
            references,
            [{"kind": "po", "value": "HM-201A26002/0029"}],
        )

    def test_mpo_number_label_is_extracted_as_a_purchase_order_reference(self):
        self.assertEqual(
            extract_po_references("MPO No: 294676"),
            [{"kind": "po", "value": "294676"}],
        )

    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_generic_supported_attachment_is_inspected_without_po_keywords(self, _token, preview):
        message = self.message("generic-attachment", subject="Please see attached", body="Kind regards")
        attachment = {
            "filename": "123.pdf",
            "mime_type": "application/pdf",
            "size": 100,
            "attachment_id": "generic-attachment-1",
            "part_id": "1",
        }
        message["attachment_manifest"] = [attachment]
        message["_attachment_refs"] = [attachment]
        preview.return_value = (
            {**attachment, "candidate": True, "content_fetched": True, "status": "parsed"},
            100,
        )

        classification = classify_mailbox_message(message)
        manifest, candidate_count, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=classification["is_relevant"],
        )

        self.assertEqual(classification["classification"], MailboxPOMessage.CLASS_POSSIBLE_PO)
        self.assertTrue(classification["is_relevant"])
        preview.assert_called_once()
        self.assertEqual(manifest[0]["status"], "parsed")
        self.assertEqual((candidate_count, fetched_bytes), (1, 100))

        oversized = self.message("oversized-generic", subject="Please see attached", body="Kind regards")
        oversized["attachment_manifest"] = [{**attachment, "size": 11 * 1024 * 1024}]
        unsupported = self.message("active-generic", subject="Please see attached", body="Kind regards")
        unsupported["attachment_manifest"] = [{**attachment, "filename": "123.svg"}]
        self.assertEqual(classify_mailbox_message(oversized)["classification"], MailboxPOMessage.CLASS_OTHER)
        self.assertEqual(classify_mailbox_message(unsupported)["classification"], MailboxPOMessage.CLASS_OTHER)

    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_neutral_attached_email_is_inspected_before_exclusion(self, _token, preview):
        message = self.message(
            "neutral-attached-email",
            subject="Forwarded message",
            body="For your records.",
        )
        attachment = {
            "filename": "forwarded-message.eml",
            "mime_type": "message/rfc822",
            "size": 100,
            "attachment_id": "neutral-eml-1",
            "part_id": "2",
        }
        message["attachment_manifest"] = [attachment]
        message["_attachment_refs"] = [attachment]
        preview.return_value = (
            {**attachment, "candidate": True, "content_fetched": True, "status": "parsed"},
            100,
        )

        classification = classify_mailbox_message(message)
        manifest, candidate_count, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=classification["is_relevant"],
        )

        self.assertEqual(
            classification["classification"],
            MailboxPOMessage.CLASS_POSSIBLE_PO,
        )
        preview.assert_called_once()
        self.assertEqual(manifest[0]["status"], "parsed")
        self.assertEqual((candidate_count, fetched_bytes), (1, 100))

    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_mailbox_attachment_parse_is_no_store_and_preserves_warnings(self, parse_preview):
        parse_preview.return_value = {
            "source_sha256": "a" * 64,
            "source_file_ref": "",
            "source_mime_type": "application/pdf",
            "parse_method": "test",
            "original_text": "Purchase Order",
            "meta": {},
            "totals": {},
            "lines": [],
            "warnings": ["OCR confidence is low; review the source."],
        }
        inline_data = base64.urlsafe_b64encode(b"%PDF-1.4\nsource").decode("ascii").rstrip("=")

        result, byte_count = _preview_attachment(
            self.connection,
            "gmail-message",
            {
                "filename": "123.pdf",
                "mime_type": "application/pdf",
                "size": 15,
                "part_id": "1",
                "_inline_data": inline_data,
            },
            "token",
        )

        self.assertEqual(
            parse_preview.call_args.kwargs,
            {
                "store_source": False,
                "max_bytes": 10 * 1024 * 1024,
                "max_pdf_pages_override": 25,
            },
        )
        self.assertEqual(result["warnings"], ["OCR confidence is low; review the source."])
        self.assertEqual(result["source_file_ref"], "")
        self.assertGreater(byte_count, 0)

    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_attached_email_parses_one_nested_pdf_and_preserves_container_identity(self, parse_preview):
        nested_pdf = b"%PDF-1.4\nnested purchase order"
        attached_email = EmailMessage()
        attached_email["Subject"] = "Implemented purchase order"
        attached_email.set_content("Please find the implemented PO attached.")
        attached_email.add_attachment(
            nested_pdf,
            maintype="application",
            subtype="octet-stream",
            filename="PO_PO26IMD32175_0.pdf",
        )
        wrapper_bytes = attached_email.as_bytes()
        parse_preview.return_value = {
            "source_sha256": "c" * 64,
            "source_file_ref": "",
            "source_mime_type": "application/pdf",
            "parse_method": "test_nested_pdf",
            "original_text": "PURCHASE ORDER\nQuotation No: QT-20260623-0010",
            "meta": {},
            "totals": {"grand_total": "331.80"},
            "lines": [{"raw_name": "First Aid Cream", "quantity": "1"}],
            "warnings": [],
        }
        inline_data = base64.urlsafe_b64encode(wrapper_bytes).decode("ascii").rstrip("=")
        outer = {
            "filename": "Implemented Purchase Order.eml",
            "mime_type": "message/rfc822",
            "size": 0,
            "part_id": "2",
            "attachment_id": "gmail-eml-token",
            "_inline_data": inline_data,
        }

        result, byte_count = _preview_attachment(
            self.connection,
            "gmail-message",
            outer,
            "token",
        )

        uploaded = parse_preview.call_args.args[0]
        self.assertEqual(uploaded.name, "PO_PO26IMD32175_0.pdf")
        self.assertEqual(uploaded.read(), nested_pdf)
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["filename"], "PO_PO26IMD32175_0.pdf")
        self.assertEqual(result["mime_type"], "application/pdf")
        self.assertEqual(result["container_filename"], "Implemented Purchase Order.eml")
        self.assertEqual(result["container_mime_type"], "message/rfc822")
        self.assertEqual(result["container_size"], 0)
        self.assertEqual(result["attachment_id"], "gmail-eml-token")
        self.assertEqual(result["part_id"], "2")
        self.assertEqual(byte_count, len(wrapper_bytes))

        rotated_fresh_ref = {
            **outer,
            "attachment_id": "rotated-gmail-token",
        }
        rotated_fresh_ref.pop("_inline_data")
        self.assertEqual(_attachment_identity(result), _attachment_identity(rotated_fresh_ref))

    @patch("quotations.import_parsers.store_import_source")
    @patch("quotations.import_parsers.parse_pdf_preview")
    def test_no_store_parser_option_never_writes_private_source(self, parse_pdf, store_source):
        parse_pdf.return_value = {"meta": {}, "warnings": [], "lines": []}

        preview = parse_file_preview(
            SimpleUploadedFile("123.pdf", b"%PDF-1.4\nsource", content_type="application/pdf"),
            store_source=False,
        )

        store_source.assert_not_called()
        self.assertEqual(preview["source_file_ref"], "")
        self.assertEqual(preview["meta"]["source_file_ref"], "")

    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_attachment_bytes_are_only_requested_for_plausible_messages(self, _token, preview):
        message = self.message("attachment-policy")
        attachment = {
            "filename": "PO-77881.pdf",
            "mime_type": "application/pdf",
            "size": 100,
            "attachment_id": "attachment-1",
            "gmail_message_id": "attachment-policy",
            "part_id": "1",
        }
        message["attachment_manifest"] = [attachment]
        message["_attachment_refs"] = [attachment]

        irrelevant_manifest, irrelevant_candidates, irrelevant_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=False,
        )

        preview.assert_not_called()
        self.assertFalse(irrelevant_manifest[0]["content_fetched"])
        self.assertEqual((irrelevant_candidates, irrelevant_bytes), (0, 0))

        preview.return_value = (
            {**attachment, "candidate": True, "content_fetched": True, "status": "parsed"},
            100,
        )
        relevant_manifest, relevant_candidates, relevant_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=True,
        )

        preview.assert_called_once()
        self.assertTrue(relevant_manifest[0]["content_fetched"])
        self.assertEqual((relevant_candidates, relevant_bytes), (1, 100))

    @patch("quotations.mailbox_po_audit.MAX_TOTAL_ATTACHMENT_BYTES", 10)
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_actual_attachment_bytes_enforce_total_when_gmail_size_is_missing(
        self,
        _token,
        parse_preview,
    ):
        parse_preview.return_value = {
            "source_sha256": "b" * 64,
            "source_file_ref": "",
            "source_mime_type": "application/pdf",
            "parse_method": "test",
            "original_text": "Purchase Order",
            "meta": {},
            "totals": {},
            "lines": [{"item_name": "Test item", "quantity": "1"}],
            "warnings": [],
        }
        encoded = base64.urlsafe_b64encode(b"123456").decode("ascii").rstrip("=")
        message = self.message("missing-declared-sizes", subject="Purchase Order")
        attachments = [
            {
                "filename": f"PO-{index}.pdf",
                "mime_type": "application/pdf",
                "size": 0,
                "part_id": str(index),
                "_inline_data": encoded,
            }
            for index in (1, 2)
        ]
        message["attachment_manifest"] = attachments
        message["_attachment_refs"] = attachments

        manifest, candidate_count, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=True,
        )

        self.assertEqual(candidate_count, 2)
        # Gmail omitted both sizes, so the second decoded response crossed the
        # 10-byte processing budget. The ledger records all 12 bytes actually
        # downloaded, discards the second parse, and stops fetching.
        self.assertEqual(fetched_bytes, 12)
        self.assertEqual(parse_preview.call_count, 1)
        self.assertEqual(manifest[0]["status"], "parsed")
        self.assertEqual(manifest[0]["fetched_bytes"], 6)
        self.assertEqual(manifest[1]["status"], "skipped")
        self.assertTrue(manifest[1]["content_fetched"])
        self.assertEqual(manifest[1]["fetched_bytes"], 6)
        self.assertIn("processing byte limit", manifest[1]["reason"])
        self.assertNotIn("original_text", manifest[1])
        self.assertNotIn("lines", manifest[1])
