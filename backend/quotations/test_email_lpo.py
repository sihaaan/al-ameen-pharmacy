import base64
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from api.models import Product

from .contract_intelligence import (
    analyze_contract_run,
    exchange_gmail_code,
    gmail_fetch_message,
    gmail_fetch_message_metadata,
    resolve_gmail_connection,
)
from .models import (
    Company,
    ContractIntelligenceItem,
    ContractIntelligenceRun,
    ContractIntelligenceSource,
    GmailOAuthConnection,
    Quotation,
    QuotationLPO,
    QuotationLine,
    QuotationOutcomePOImport,
    QuotationPOEvidence,
)
from .quote_po_intelligence import (
    _candidate_score,
    build_quote_gmail_queries,
    find_quote_po_evidence,
    parse_quote_po_evidence,
)
from .services import build_po_outcome_suggestions, recalculate_line_outcome


def gmail_data(value):
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


class SharedMailboxEvidenceTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("mailbox-owner", is_staff=True)
        self.reviewer = User.objects.create_user("reviewer", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            email="orders@pharmacy.example",
            status=GmailOAuthConnection.STATUS_CONNECTED,
            is_shared=True,
        )
        self.company = Company.objects.create(
            name="Acme Medical",
            email="buyer@acme.example",
        )
        self.sent_at = timezone.now() - timedelta(hours=2)
        self.quotation = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0042",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.reviewer,
        )

    def test_all_staff_resolve_the_designated_shared_connection(self):
        self.assertEqual(resolve_gmail_connection(self.reviewer), self.connection)

    def test_query_uses_exact_epoch_excludes_outbound_and_does_not_use_public_domain_identity(self):
        self.company.email = "buyer@gmail.com"
        self.company.save(update_fields=["email", "updated_at"])

        queries = build_quote_gmail_queries(self.quotation)

        expected_after = f"after:{int(self.sent_at.timestamp())}"
        self.assertTrue(all(expected_after in query for query in queries))
        self.assertTrue(all("-from:me" in query for query in queries))
        self.assertFalse(any("from:gmail.com" in query for query in queries))
        self.assertTrue(any("from:buyer@gmail.com" in query for query in queries))

    def test_candidate_requires_document_and_identity_and_rejects_wrong_quote_reference(self):
        base = {
            "sender": "buyer@acme.example",
            "recipients": "orders@pharmacy.example",
            "sent_at": self.sent_at + timedelta(minutes=10),
            "attachments": [],
        }
        confidence, reason = _candidate_score(
            self.quotation,
            {**base, "subject": "Acme Medical meeting", "snippet": "General account update"},
            "test",
            mailbox_email=self.connection.email,
        )
        self.assertEqual(confidence, 0)
        self.assertIn("no PO/LPO", reason)

        confidence, reason = _candidate_score(
            self.quotation,
            {
                **base,
                "subject": "Purchase order for quotation QT-20260713-9999",
                "snippet": "LPO attached",
            },
            "test",
            mailbox_email=self.connection.email,
        )
        self.assertEqual(confidence, 0)
        self.assertIn("another quotation", reason)

        self.company.name = "Gmail Services"
        self.company.email = ""
        self.company.save(update_fields=["name", "email", "updated_at"])
        confidence, reason = _candidate_score(
            self.quotation,
            {
                **base,
                "sender": "unrelated@gmail.com",
                "subject": "LPO attached",
                "snippet": "Purchase order",
            },
            "test",
            mailbox_email=self.connection.email,
        )
        self.assertEqual(confidence, 0)
        self.assertIn("no quotation or customer identity", reason)

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_discovery_uses_shared_mailbox_provenance_and_preserves_review_statuses(self, search, fetch):
        search.return_value = {"messages": [{"id": "gmail-1"}]}
        fetch.return_value = {
            "gmail_message_id": "gmail-1",
            "gmail_thread_id": "thread-1",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "Purchase order attached",
            "attachments": [{"filename": "LPO-77.pdf", "size": 100}],
        }
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_message_id="gmail-1",
            status=QuotationPOEvidence.STATUS_NOT_RELEVANT,
        )

        result = find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        evidence.refresh_from_db()
        self.assertEqual(result["count"], 0)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_NOT_RELEVANT)
        self.assertEqual(evidence.gmail_connection, self.connection)
        self.assertEqual(evidence.mailbox_email, self.connection.email)
        search.assert_called()
        self.assertEqual(search.call_args.args[0], self.connection)

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_rescan_does_not_replace_parsed_attachment_provenance_with_metadata_stubs(self, search, fetch):
        search.return_value = {"messages": [{"id": "gmail-parsed"}]}
        fetch.return_value = {
            "gmail_message_id": "gmail-parsed",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "Purchase order attached",
            "attachments": [{"filename": "LPO-77.pdf", "attachment_id": "stub"}],
        }
        rich_attachments = [
            {
                "filename": "LPO-77.pdf",
                "attachment_id": "full",
                "status": "parsed",
                "source_file_ref": "private:full",
                "lines": [{"requested_item_name": "Bandage Pack"}],
            }
        ]
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-parsed",
            status=QuotationPOEvidence.STATUS_PARSED,
            attachments=rich_attachments,
            source_sha256="f" * 64,
        )

        result = find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        evidence.refresh_from_db()
        self.assertEqual(result["count"], 1)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(evidence.attachments, rich_attachments)
        self.assertEqual(evidence.source_sha256, "f" * 64)


class GmailMimeParsingTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mime-owner", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.user,
            email="orders@pharmacy.example",
            is_shared=True,
        )

    @patch("quotations.contract_intelligence.get_valid_access_token", return_value="token")
    @patch("quotations.contract_intelligence._json_request")
    def test_full_message_prefers_plain_alternative_trims_reply_and_keeps_attachment_provenance(
        self,
        request_json,
        _token,
    ):
        request_json.return_value = {
            "id": "message-1",
            "threadId": "thread-1",
            "internalDate": str(int(timezone.now().timestamp() * 1000)),
            "payload": {
                "mimeType": "multipart/mixed",
                "headers": [
                    {"name": "Subject", "value": "LPO attached"},
                    {"name": "From", "value": "Buyer <buyer@acme.example>"},
                ],
                "parts": [
                    {
                        "mimeType": "multipart/alternative",
                        "parts": [
                            {
                                "partId": "0.0",
                                "mimeType": "text/plain",
                                "body": {"data": gmail_data("New order line\nOn Monday Buyer wrote:\n> old order line")},
                            },
                            {
                                "partId": "0.1",
                                "mimeType": "text/html",
                                "body": {"data": gmail_data("<p>HTML duplicate order line</p>")},
                            },
                        ],
                    },
                    {
                        "partId": "1",
                        "mimeType": "application/pdf",
                        "filename": "LPO-77.pdf",
                        "body": {"data": gmail_data("fake-pdf"), "size": 8},
                    },
                ],
            },
            "snippet": "New order line",
        }
        parsed_preview = {
            "source_file_ref": "private:po-1",
            "source_sha256": "a" * 64,
            "lines": [{"raw_name": "Bandage Pack", "quantity": "2"}],
            "warnings": [],
        }
        with patch("quotations.contract_intelligence.parse_file_preview", return_value=parsed_preview):
            result = gmail_fetch_message(self.connection, "message-1", include_attachments=True)

        self.assertEqual(result["body_text"], "New order line")
        self.assertNotIn("HTML duplicate", result["body_text"])
        attachment = result["attachments"][0]
        self.assertEqual(attachment["gmail_message_id"], "message-1")
        self.assertEqual(attachment["part_id"], "1")
        self.assertEqual(attachment["source_file_ref"], "private:po-1")
        self.assertEqual(attachment["lines"][0]["source_gmail_message_id"], "message-1")

    @patch("quotations.contract_intelligence.get_valid_access_token", return_value="token")
    @patch("quotations.contract_intelligence._json_request")
    def test_metadata_uses_full_mime_tree_for_attachment_discovery(self, request_json, _token):
        request_json.return_value = {
            "id": "message-2",
            "payload": {
                "headers": [{"name": "Subject", "value": "PO"}],
                "parts": [
                    {
                        "partId": "2",
                        "mimeType": "application/pdf",
                        "filename": "po.pdf",
                        "body": {"attachmentId": "attachment-2", "size": 12},
                    }
                ],
            },
        }

        result = gmail_fetch_message_metadata(self.connection, "message-2")

        self.assertIn("format=full", request_json.call_args.args[0])
        self.assertEqual(result["attachments"][0]["attachment_id"], "attachment-2")
        self.assertEqual(result["attachments"][0]["part_path"], "0.0")


class GmailEvidenceReviewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("evidence-owner", is_staff=True)
        self.reviewer = User.objects.create_user("evidence-reviewer", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            email="orders@pharmacy.example",
            is_shared=True,
        )
        self.company = Company.objects.create(name="Acme Medical", email="buyer@acme.example")
        self.product = Product.objects.create(name="Bandage Pack", price=Decimal("1.00"), status="draft")
        self.sent_at = timezone.now() - timedelta(hours=1)
        self.quotation = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0066",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.reviewer,
        )
        self.line = QuotationLine.objects.create(
            quotation=self.quotation,
            product=self.product,
            item_name_snapshot="Bandage Pack",
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.000"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        self.evidence = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-review-1",
            sender="buyer@acme.example",
            subject=f"LPO for {self.quotation.quotation_number}",
            sent_at=self.sent_at + timedelta(minutes=10),
            confidence=90,
            created_by=self.reviewer,
        )

    @patch("quotations.quote_po_intelligence.gmail_fetch_message")
    def test_staff_parse_is_idempotent_creates_one_review_lpo_and_never_applies_outcome(self, fetch):
        fetch.return_value = {
            "gmail_message_id": self.evidence.gmail_message_id,
            "gmail_thread_id": "thread-review-1",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=10),
            "snippet": "Purchase order attached",
            "body_text": "Purchase Order No: LPO-77\nBandage Pack 2 box 10",
            "attachments": [
                {
                    "filename": "LPO-77.pdf",
                    "attachment_id": "primary",
                    "size": 100,
                    "status": "parsed",
                    "source_file_ref": "private:lpo-77",
                    "lines": [{"requested_item_name": "Bandage Pack", "quantity": "2", "unit_price": "10"}],
                },
                {
                    "filename": "terms.xlsx",
                    "attachment_id": "terms",
                    "size": 50,
                    "status": "parsed",
                    "source_file_ref": "private:terms",
                    "lines": [{"requested_item_name": "Unrelated Terms Row", "quantity": "1"}],
                },
            ],
        }

        first = parse_quote_po_evidence(self.evidence, self.reviewer, use_ai=False, link_approved=True)
        second = parse_quote_po_evidence(self.evidence, self.reviewer, use_ai=False, link_approved=True)

        self.assertEqual(first.id, second.id)
        self.assertEqual(QuotationOutcomePOImport.objects.filter(gmail_evidence=self.evidence).count(), 1)
        self.assertEqual(QuotationLPO.objects.filter(gmail_evidence=self.evidence).count(), 1)
        lpo = QuotationLPO.objects.get(gmail_evidence=self.evidence)
        self.assertEqual(lpo.source_type, QuotationLPO.SOURCE_GMAIL)
        self.assertEqual(lpo.status, QuotationLPO.STATUS_NEEDS_REVIEW)
        self.assertEqual(lpo.lpo_number, "LPO-77")
        self.assertEqual(lpo.mailbox_email, self.connection.email)
        self.assertEqual([row["requested_item_name"] for row in lpo.parsed_rows], ["Bandage Pack"])
        self.assertTrue(any("not merged" in warning for warning in lpo.warnings))
        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.link_approved_by, self.reviewer)
        self.assertIsNotNone(self.evidence.link_approved_at)
        self.line.refresh_from_db()
        self.assertEqual(self.line.outcome_status, QuotationLine.OUTCOME_PENDING)
        self.assertEqual(fetch.call_args.args[0], self.connection)

    @patch("quotations.quote_po_intelligence.gmail_fetch_message")
    def test_parse_endpoint_rejects_candidate_without_explicit_link_approval(self, fetch):
        client = APIClient()
        client.force_authenticate(self.reviewer)

        response = client.post(
            reverse("quotation-parse-po-evidence", args=[self.quotation.id]),
            {"evidence_id": self.evidence.id, "use_ai": "false"},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Explicit staff approval", " ".join(response.json()["detail"]))
        self.assertFalse(QuotationOutcomePOImport.objects.filter(gmail_evidence=self.evidence).exists())
        self.assertFalse(QuotationLPO.objects.filter(gmail_evidence=self.evidence).exists())
        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertIsNone(self.evidence.link_approved_at)
        fetch.assert_not_called()


class POAssignmentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("assignment-user", is_staff=True)
        self.company = Company.objects.create(name="Assignment Customer")
        self.quotation = Quotation.objects.create(company=self.company, created_by=self.user)

    def add_line(self, name, quantity="2", sort_order=0):
        return QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot=name,
            quantity=Decimal(quantity),
            unit_price=Decimal("10"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=sort_order,
        )

    def test_one_quote_line_is_never_assigned_to_two_po_rows_and_higher_quantity_is_changed(self):
        line = self.add_line("Bandage Pack", quantity="2")
        suggestions, unmatched, _missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"requested_item_name": "Bandage Pack", "quantity": "5"},
                    {"requested_item_name": "Bandage Pack", "quantity": "1"},
                ]
            },
        )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["quotation_line_id"], line.id)
        self.assertEqual(suggestions[0]["suggested_outcome_status"], QuotationLine.OUTCOME_QUANTITY_CHANGED)
        self.assertEqual(unmatched[0]["reason_code"], "quotation_line_already_assigned")

        line.outcome_status = QuotationLine.OUTCOME_ACCEPTED
        line.accepted_quantity = Decimal("5")
        line.accepted_unit_price = line.unit_price
        recalculate_line_outcome(line)
        self.assertEqual(line.outcome_status, QuotationLine.OUTCOME_QUANTITY_CHANGED)

    def test_ambiguous_and_conflicting_specs_are_left_for_staff(self):
        first = self.add_line("Nitrile Gloves Medium Blue", sort_order=0)
        second = self.add_line("Nitrile Gloves Medium Black", sort_order=1)
        self.add_line("Sterile Gauze Bandage 5 cm x 5 m", sort_order=2)

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"requested_item_name": "Nitrile Gloves Medium"},
                    {"requested_item_name": "Sterile Gauze Bandage 10 cm x 5 m"},
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["ambiguous_match", "specification_conflict"])
        self.assertIn(first.id, missing)
        self.assertIn(second.id, missing)


class ContractIntelligenceFallbackTests(TestCase):
    def test_irrelevant_empty_ai_result_does_not_fall_back_or_inflate_confidence(self):
        user = User.objects.create_user("contract-user", is_staff=True)
        company = Company.objects.create(name="Contract Customer")
        run = ContractIntelligenceRun.objects.create(
            company=company,
            target_company_name=company.name,
            created_by=user,
        )
        source = ContractIntelligenceSource.objects.create(
            run=run,
            subject="Newsletter",
            body_text="Bandage Pack 100 box",
            status="candidate",
        )
        ai_payload = {"classification": "irrelevant", "confidence": 80, "items": [], "warnings": []}

        with patch("quotations.contract_intelligence._ai_items_for_source", return_value=ai_payload), patch(
            "quotations.contract_intelligence._deterministic_items_from_text",
            return_value=[{"item_name": "Should Not Exist"}],
        ) as fallback:
            result = analyze_contract_run(run, user, use_ai=True, source_limit=1)

        source.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(result["items_created"], 0)
        self.assertFalse(ContractIntelligenceItem.objects.filter(run=run).exists())
        self.assertEqual(source.classification, ContractIntelligenceSource.CLASS_IRRELEVANT)
        self.assertEqual(source.confidence, 0.8)
        self.assertEqual(run.status, ContractIntelligenceRun.STATUS_READY)
        fallback.assert_not_called()


class SharedMailboxCommandTests(TestCase):
    @patch("quotations.management.commands.scan_shared_mailbox_lpos.scan_quote_po_evidence_batch")
    def test_scheduled_command_only_runs_review_candidate_discovery(self, scan):
        owner = User.objects.create_user("command-owner", is_staff=True)
        GmailOAuthConnection.objects.create(
            user=owner,
            email="orders@pharmacy.example",
            is_shared=True,
        )
        scan.return_value = {
            "processed": 2,
            "candidates_found": 1,
            "remaining": 0,
            "done": True,
            "errors": [],
            "quotes": [],
        }
        stdout = StringIO()

        call_command("scan_shared_mailbox_lpos", stdout=stdout)

        self.assertIn("review candidate", stdout.getvalue())
        self.assertTrue(scan.call_args.kwargs["rescan"])


class SharedMailboxOwnershipTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user("gmail-owner", is_staff=True)
        self.other_staff = User.objects.create_user("gmail-other", is_staff=True)
        self.superuser = User.objects.create_superuser("gmail-admin", email="admin@example.com", password="pass")
        self.connection = GmailOAuthConnection.objects.create(
            user=self.owner,
            email="orders@pharmacy.example",
            is_shared=True,
        )

    def api_client(self, user):
        client = APIClient()
        client.force_authenticate(user)
        return client

    @patch("quotations.views.build_gmail_auth_url")
    def test_non_owner_cannot_replace_or_disconnect_shared_mailbox(self, auth_url):
        client = self.api_client(self.other_staff)

        replace_response = client.post(reverse("quotation-gmail-connection"), {}, format="json")
        disconnect_response = client.delete(reverse("quotation-gmail-connection"))

        self.assertEqual(replace_response.status_code, 403)
        self.assertEqual(disconnect_response.status_code, 403)
        auth_url.assert_not_called()
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, GmailOAuthConnection.STATUS_CONNECTED)

    @patch("quotations.views.build_gmail_auth_url", return_value="https://accounts.example/auth")
    def test_owner_can_reconnect_and_status_exposes_management_capability(self, auth_url):
        client = self.api_client(self.owner)

        status_response = client.get(reverse("quotation-gmail-connection"))
        replace_response = client.post(reverse("quotation-gmail-connection"), {}, format="json")

        self.assertEqual(status_response.status_code, 200)
        self.assertTrue(status_response.data["can_manage"])
        self.assertEqual(replace_response.status_code, 200)
        auth_url.assert_called_once()

    def test_superuser_can_disconnect_shared_mailbox(self):
        response = self.api_client(self.superuser).delete(reverse("quotation-gmail-connection"))

        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.status, GmailOAuthConnection.STATUS_DISCONNECTED)

    @patch("quotations.contract_intelligence._form_request")
    def test_oauth_callback_exchange_cannot_bypass_owner_gate(self, form_request):
        with self.assertRaises(PermissionError):
            exchange_gmail_code(self.other_staff, "oauth-code")
        form_request.assert_not_called()


class SharedMailboxInitialSetupTests(TestCase):
    @patch("quotations.views.build_gmail_auth_url", return_value="https://accounts.example/auth")
    def test_staff_can_start_initial_setup_when_no_shared_mailbox_exists(self, auth_url):
        staff = User.objects.create_user("first-gmail-owner", is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)

        response = client.post(reverse("quotation-gmail-connection"), {}, format="json")

        self.assertEqual(response.status_code, 200)
        auth_url.assert_called_once()
