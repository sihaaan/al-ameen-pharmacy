import base64
import importlib
from datetime import timedelta
from decimal import Decimal
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.apps import apps as django_apps
from django.contrib.auth.models import User
from django.core.management import call_command
from django.db import transaction
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
    MailboxPOAuditRun,
    Quotation,
    QuotationLPO,
    QuotationLine,
    QuotationOutcomePOImport,
    QuotationPOEvidence,
)
from .quote_po_intelligence import (
    EvidenceLinkConflict,
    _candidate_score,
    _extract_gmail_lpo_details,
    _has_numbered_po_reference,
    _locked_message_evidence_queryset,
    _lock_and_resolve_evidence_approval,
    _preview_from_gmail_payload,
    _search_query_with_complete_flag,
    _select_primary_po_attachment,
    build_quote_gmail_queries,
    find_quote_po_evidence,
    parse_quote_po_evidence,
)
from .services import (
    AI_QUOTE_COVERAGE_GUARD_WARNING,
    build_po_outcome_suggestions,
    recalculate_line_outcome,
)


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

    def test_inline_signature_image_does_not_strengthen_lpo_candidate(self):
        payload = {
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "sent_at": self.sent_at + timedelta(minutes=10),
            "subject": "Purchase order attached",
            "snippet": "Please proceed with the order",
            "attachments": [
                {"filename": "image001.png", "mime_type": "image/png", "size": 912},
            ],
        }

        with_image = _candidate_score(
            self.quotation,
            payload,
            "test",
            mailbox_email=self.connection.email,
        )
        without_image = _candidate_score(
            self.quotation,
            {**payload, "attachments": []},
            "test",
            mailbox_email=self.connection.email,
        )

        self.assertEqual(with_image, without_image)
        self.assertNotIn("attachment", with_image[1].lower())

    def test_quote_reference_in_document_filename_strips_only_known_extension(self):
        base = {
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "sent_at": self.sent_at + timedelta(minutes=10),
            "subject": "LPO attached",
            "snippet": "Purchase order attached",
        }

        for extension in ("pdf", "xlsx"):
            with self.subTest(extension=extension):
                confidence, reason = _candidate_score(
                    self.quotation,
                    {
                        **base,
                        "attachments": [
                            {"filename": f"{self.quotation.quotation_number}.{extension}", "size": 100}
                        ],
                    },
                    "test",
                    mailbox_email=self.connection.email,
                )
                self.assertGreaterEqual(confidence, 45)
                self.assertIn("strong match: quote number appears", reason)

        confidence, reason = _candidate_score(
            self.quotation,
            {
                **base,
                "attachments": [
                    {"filename": f"{self.quotation.quotation_number}.backup.pdf", "size": 100}
                ],
            },
            "test",
            mailbox_email=self.connection.email,
        )
        self.assertEqual(confidence, 0)
        self.assertIn("another quotation", reason)

    def test_compact_po_number_is_detected_from_document_filename_without_extension(self):
        details = _extract_gmail_lpo_details(
            {
                "source_filename": "Purchase order PO184718.pdf",
                "original_text": "",
                "lines": [],
                "meta": {},
            }
        )

        self.assertEqual(details["lpo_number"], "184718")

    def test_intermass_po_number_is_detected_from_filename_and_subject(self):
        cases = (
            ({
                "source_filename": "PO_PO111_123301_0.pdf",
                "original_text": "",
                "lines": [],
                "meta": {},
            }, "PO111_123301"),
            ({
                "source_filename": "attachment.pdf",
                "original_text": "",
                "lines": [],
                "meta": {"gmail_subject": "FW: Document Purchase Order PO112_110916"},
            }, "PO112_110916"),
        )
        for preview, expected_number in cases:
            with self.subTest(expected_number=expected_number):
                details = _extract_gmail_lpo_details(preview)
                self.assertEqual(details["lpo_number"], expected_number)

    def test_customer_po_attachment_outranks_attached_copy_of_our_quote(self):
        quote_attachment = {
            "filename": f"Customer-{self.quotation.quotation_number}.pdf",
            "status": "parsed",
            "original_text": (
                "AL AMEEN PHARMACY\nQUOTATION\n"
                f"Quotation # {self.quotation.quotation_number}\n"
                "Customer PO NUMBER: 75B0600313340"
            ),
            "lines": [
                {"raw_line": "Shampoo 500ml | 12 | bottle | 8.00"},
                {"raw_line": "Please issue a purchase order before delivery."},
            ],
        }
        po_attachment = {
            "filename": "customer-document.pdf",
            "status": "parsed",
            "original_text": "PURCHASE ORDER\nPO NUMBER : 75B0600313340",
            "lines": [{"raw_line": "PO NUMBER : 75B0600313340"}],
        }

        selected, warnings = _select_primary_po_attachment(
            {
                "subject": "A Purchase Order: 75B0600313340 has been submitted",
                "attachments": [quote_attachment, po_attachment],
            },
            self.quotation,
        )

        self.assertIs(selected, po_attachment)
        self.assertTrue(any(quote_attachment["filename"] in warning for warning in warnings))

    def test_numbered_po_labels_allow_punctuation_but_require_a_digit(self):
        for value in (
            "PO NUMBER: 778",
            "PO No.: 778",
            "Purchase Order No: ABC123",
            "LPO No: LPO-77",
        ):
            with self.subTest(value=value):
                self.assertTrue(_has_numbered_po_reference(value))
        self.assertFalse(_has_numbered_po_reference("Purchase order before delivery"))

    def test_attached_copy_of_our_quote_is_not_treated_as_the_customer_po(self):
        quote_attachment = {
            "filename": f"Customer-{self.quotation.quotation_number}.pdf",
            "status": "parsed",
            "original_text": (
                "AL AMEEN PHARMACY\nQUOTATION\n"
                f"Quotation # {self.quotation.quotation_number}\n"
                "Customer PO NUMBER: 75B0600313340"
            ),
            "lines": [
                {"raw_line": "Shampoo 500ml | 12 | bottle | 8.00"},
                {"raw_line": "Please issue a purchase order before delivery."},
            ],
        }

        selected, warnings = _select_primary_po_attachment(
            {
                "subject": "A Purchase Order has been submitted",
                "attachments": [quote_attachment],
            },
            self.quotation,
        )

        self.assertIsNone(selected)
        self.assertTrue(warnings)

    def test_scanned_generic_po_outranks_tax_invoice_with_po_reference(self):
        tax_invoice = {
            "filename": "tax-invoice.pdf",
            "status": "parsed",
            "source_file_ref": "inquiry_sources/tax-invoice.pdf",
            "original_text": "TAX INVOICE\nCustomer PO NUMBER: 778",
            "lines": [{"raw_line": "Shampoo 500ml | 12 | bottle | 8.00"}],
        }
        scanned_po = {
            "filename": "scan.pdf",
            "status": "parsed",
            "source_file_ref": "inquiry_sources/scan.pdf",
            "original_text": "",
            "lines": [],
        }

        selected, warnings = _select_primary_po_attachment(
            {
                "subject": "Purchase Order: 778",
                "attachments": [tax_invoice, scanned_po],
            },
            self.quotation,
        )

        self.assertIs(selected, scanned_po)
        self.assertTrue(any(tax_invoice["filename"] in warning for warning in warnings))

    def test_scanned_non_po_filename_is_not_selected_from_po_subject_alone(self):
        for filename in (
            "tax-invoice.pdf",
            "proforma_invoice.pdf",
            "delivery-note.pdf",
            "statement.pdf",
            "receipt.pdf",
            "quotation.pdf",
            "invoice778.pdf",
            "taxinvoice778.pdf",
            "proforma778.pdf",
            "deliverynote778.pdf",
            "statement778.pdf",
            "receipt778.pdf",
            "quotation778.pdf",
        ):
            with self.subTest(filename=filename):
                selected, warnings = _select_primary_po_attachment(
                    {
                        "subject": "Purchase Order: 778",
                        "attachments": [
                            {
                                "filename": filename,
                                "status": "parsed",
                                "source_file_ref": f"inquiry_sources/{filename}",
                                "original_text": "",
                                "lines": [],
                            }
                        ],
                    },
                    self.quotation,
                )

                self.assertIsNone(selected)
                self.assertTrue(warnings)

    def test_tied_po_attachments_fail_closed_unless_subject_identifies_one(self):
        first = {
            "filename": "PO_111.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: 111"}],
        }
        second = {
            "filename": "PO_222.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: 222"}],
        }

        selected, warnings = _select_primary_po_attachment(
            {"subject": "Purchase order documents", "attachments": [first, second]},
            self.quotation,
        )
        self.assertIsNone(selected)
        self.assertTrue(any("equally plausible" in warning for warning in warnings))

        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-ambiguous-attachments",
        )
        with self.assertRaisesMessage(EvidenceLinkConflict, "equally plausible"):
            _preview_from_gmail_payload(
                {
                    "gmail_message_id": evidence.gmail_message_id,
                    "subject": "Purchase order documents",
                    "body_text": "Please process the attached order.",
                    "attachments": [first, second],
                },
                evidence,
            )

        selected, warnings = _select_primary_po_attachment(
            {"subject": "Purchase Order: 222", "attachments": [first, second]},
            self.quotation,
        )
        self.assertIs(selected, second)
        self.assertTrue(any(first["filename"] in warning for warning in warnings))

    def test_tie_resolution_uses_exact_normalized_po_references(self):
        prefix_collision = {
            "filename": "PO-1234.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: PO-1234"}],
        }
        exact = {
            "filename": "PO_123.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: 123"}],
        }

        selected, _warnings = _select_primary_po_attachment(
            {
                "subject": "Purchase Order: PO-123",
                "attachments": [prefix_collision, exact],
            },
            self.quotation,
        )
        self.assertIs(selected, exact)

        separator_variant = {
            "filename": "PO_12-34.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: PO_12-34"}],
        }
        unrelated = {
            "filename": "PO-5678.pdf",
            "status": "parsed",
            "lines": [{"raw_line": "PO NUMBER: PO-5678"}],
        }
        selected, _warnings = _select_primary_po_attachment(
            {
                "subject": "Purchase Order: PO-12/34",
                "attachments": [unrelated, separator_variant],
            },
            self.quotation,
        )
        self.assertIs(selected, separator_variant)

    def test_selected_gmail_pdf_preserves_source_for_vision_even_without_rows(self):
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-empty-pdf-preview",
        )
        preview = _preview_from_gmail_payload(
            {
                "gmail_message_id": evidence.gmail_message_id,
                "subject": "Purchase Order: PO-77",
                "body_text": "Please process the attached PO.",
                "attachments": [
                    {
                        "filename": "PO-77.pdf",
                        "mime_type": "application/pdf",
                        "status": "parsed",
                        "source_file_ref": "inquiry_sources/po-77.pdf",
                        "source_sha256": "a" * 64,
                        "parse_method": "pymupdf_text_v1",
                        "original_text": "PURCHASE ORDER\nPO NUMBER: PO-77",
                        "meta": {"page_count": 2},
                        "lines": [],
                    }
                ],
            },
            evidence,
        )

        self.assertEqual(preview["source_filename"], "PO-77.pdf")
        self.assertEqual(preview["source_mime_type"], "application/pdf")
        self.assertEqual(preview["source_file_ref"], "inquiry_sources/po-77.pdf")
        self.assertEqual(preview["original_text"], "PURCHASE ORDER\nPO NUMBER: PO-77")
        self.assertEqual(preview["meta"]["page_count"], 2)
        self.assertEqual(preview["lines"], [])

    def test_legacy_candidate_retirement_migration_is_idempotent_and_preserves_reviewed_rows(self):
        stale = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_message_id="gmail-pre-hotfix-stale",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )
        reviewed = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_message_id="gmail-pre-hotfix-reviewed",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
            link_approved_by=self.reviewer,
            link_approved_at=timezone.now(),
        )
        migration = importlib.import_module(
            "quotations.migrations.0027_alter_quotationpoevidence_status"
        )

        migration.retire_legacy_unapproved_candidates(django_apps, None)
        migration.retire_legacy_unapproved_candidates(django_apps, None)

        stale.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(stale.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn("safe Gmail matching upgrade", stale.error)
        self.assertEqual(reviewed.status, QuotationPOEvidence.STATUS_CANDIDATE)

        runtime_superseded = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_message_id="gmail-runtime-superseded",
            status=QuotationPOEvidence.STATUS_SUPERSEDED,
            error="Superseded by explicit staff approval.",
        )
        runtime_ambiguous = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_message_id="gmail-runtime-ambiguous",
            status=QuotationPOEvidence.STATUS_AMBIGUOUS,
            error="Ambiguous across quotations.",
        )

        migration.restore_legacy_candidate_status(django_apps, None)

        stale.refresh_from_db()
        runtime_superseded.refresh_from_db()
        runtime_ambiguous.refresh_from_db()
        self.assertEqual(stale.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(runtime_superseded.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertEqual(runtime_ambiguous.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_generic_same_customer_message_is_ambiguous_across_quotes(self, search, fetch):
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0043",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at - timedelta(minutes=10),
            created_by=self.reviewer,
        )
        search.return_value = {"messages": [{"id": "gmail-shared-generic"}]}
        fetch.return_value = {
            "gmail_message_id": "gmail-shared-generic",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": "LPO attached",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "Please find the purchase order attached",
            "attachments": [{"filename": "LPO-88.pdf", "size": 100}],
        }

        find_quote_po_evidence(self.quotation, self.reviewer, limit=5)
        find_quote_po_evidence(other_quote, self.reviewer, limit=5)

        evidence = QuotationPOEvidence.objects.filter(
            gmail_message_id="gmail-shared-generic"
        ).order_by("quotation_id")
        self.assertEqual(evidence.count(), 2)
        self.assertEqual(
            set(evidence.values_list("status", flat=True)),
            {QuotationPOEvidence.STATUS_AMBIGUOUS},
        )
        self.assertTrue(all("multiple quotations" in item.error for item in evidence))

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_exact_quote_reference_wins_and_supersedes_unreviewed_peer(self, search, fetch):
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0043",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at - timedelta(minutes=10),
            created_by=self.reviewer,
        )
        peer = QuotationPOEvidence.objects.create(
            quotation=other_quote,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-exact-winner",
            status=QuotationPOEvidence.STATUS_AMBIGUOUS,
        )
        search.return_value = {"messages": [{"id": "gmail-exact-winner"}]}
        fetch.return_value = {
            "gmail_message_id": "gmail-exact-winner",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for quotation {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "Purchase order attached",
            "attachments": [{"filename": "LPO-89.pdf", "size": 100}],
        }

        result = find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        winner = QuotationPOEvidence.objects.get(
            quotation=self.quotation,
            gmail_message_id="gmail-exact-winner",
        )
        peer.refresh_from_db()
        self.assertEqual(result["count"], 1)
        self.assertEqual(winner.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(peer.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn(self.quotation.quotation_number, peer.error)

    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_complete_rescan_supersedes_stale_unreviewed_candidate(self, search):
        search.return_value = {"messages": []}
        stale = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-stale",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )

        result = find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        stale.refresh_from_db()
        self.assertEqual(result["count"], 0)
        self.assertEqual(stale.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn("latest complete Gmail scan", stale.error)

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_capped_next_page_token_skips_stale_reconciliation(self, search, fetch):
        search.return_value = {
            "messages": [{"id": f"gmail-capped-{index}"} for index in range(5)],
            "next_page_token": "more-results-remain",
        }
        fetch.return_value = {
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": "General account update",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "No purchase order in this message",
            "attachments": [],
        }
        stale = QuotationPOEvidence.objects.create(
            quotation=self.quotation,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id="gmail-missed-beyond-cap",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )

        result = find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        stale.refresh_from_db()
        self.quotation.refresh_from_db()
        self.assertFalse(result["scan_complete"])
        self.assertEqual(result["incomplete_queries"], result["queries"])
        self.assertEqual(stale.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertIn("Partial Gmail scan", self.quotation.po_evidence_last_scan_error)
        self.assertTrue(all(call.kwargs.get("page_token") == "" for call in search.call_args_list))

    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_search_paginates_within_bound_until_query_is_exhausted(self, search):
        search.side_effect = [
            {"messages": [{"id": "gmail-page-1"}], "next_page_token": "page-2"},
            {"messages": [{"id": "gmail-page-2"}], "next_page_token": ""},
        ]

        messages, complete = _search_query_with_complete_flag(
            self.connection,
            "test query",
            max_messages=5,
        )

        self.assertTrue(complete)
        self.assertEqual([message["id"] for message in messages], ["gmail-page-1", "gmail-page-2"])
        self.assertEqual(search.call_args_list[0].kwargs["max_messages"], 5)
        self.assertEqual(search.call_args_list[0].kwargs["page_token"], "")
        self.assertEqual(search.call_args_list[1].kwargs["max_messages"], 4)
        self.assertEqual(search.call_args_list[1].kwargs["page_token"], "page-2")

    @patch("quotations.quote_po_intelligence.gmail_fetch_message_metadata")
    @patch("quotations.quote_po_intelligence.gmail_search_messages")
    def test_arbitration_spans_replacement_connection_for_same_mailbox(self, search, fetch):
        old_owner = User.objects.create_user("former-mailbox-owner", is_staff=True)
        old_connection = GmailOAuthConnection.objects.create(
            user=old_owner,
            email=self.connection.email.upper(),
            status=GmailOAuthConnection.STATUS_DISCONNECTED,
        )
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0043",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at - timedelta(minutes=10),
            created_by=self.reviewer,
        )
        peer = QuotationPOEvidence.objects.create(
            quotation=other_quote,
            gmail_connection=old_connection,
            mailbox_email=old_connection.email,
            gmail_message_id="gmail-replacement-connection",
            status=QuotationPOEvidence.STATUS_AMBIGUOUS,
        )
        search.return_value = {"messages": [{"id": peer.gmail_message_id}]}
        fetch.return_value = {
            "gmail_message_id": peer.gmail_message_id,
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for quotation {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=20),
            "snippet": "Purchase order attached",
            "attachments": [{"filename": "LPO-90.pdf", "size": 100}],
        }

        find_quote_po_evidence(self.quotation, self.reviewer, limit=5)

        peer.refresh_from_db()
        self.assertEqual(peer.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn(self.quotation.quotation_number, peer.error)

    def test_message_arbitration_locks_only_evidence_rows(self):
        queryset = _locked_message_evidence_queryset(
            self.connection,
            self.connection.email,
            "gmail-postgres-lock-scope",
        )

        self.assertTrue(queryset.query.select_for_update)
        self.assertEqual(queryset.query.select_for_update_of, ("self",))

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
            "source_mime_type": "application/pdf",
            "parse_method": "pymupdf_text_v1",
            "original_text": "PURCHASE ORDER\nPO NUMBER: LPO-77",
            "meta": {"page_count": 2},
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
        self.assertEqual(attachment["source_mime_type"], "application/pdf")
        self.assertEqual(attachment["parse_method"], "pymupdf_text_v1")
        self.assertEqual(attachment["original_text"], "PURCHASE ORDER\nPO NUMBER: LPO-77")
        self.assertEqual(attachment["meta"]["page_count"], 2)
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

    @patch("quotations.quote_po_intelligence.clean_preview_with_ai")
    @patch("quotations.quote_po_intelligence.gmail_fetch_message")
    def test_staff_parse_keeps_strong_deterministic_matches_removed_by_ai(self, fetch, clean_with_ai):
        jacket = QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot="Fire Warden Jacket",
            quantity=Decimal("20"),
            unit="No",
            unit_price=Decimal("10"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=1,
        )
        water = QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot="Small Drinking Water 500ml",
            quantity=Decimal("30"),
            unit="No",
            unit_price=Decimal("1"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=2,
        )
        fetch.return_value = {
            "gmail_message_id": self.evidence.gmail_message_id,
            "gmail_thread_id": "thread-review-coverage",
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": "FW: Document Purchase Order PO112_110916",
            "sent_at": self.sent_at + timedelta(minutes=10),
            "snippet": "Purchase order attached",
            "body_text": "",
            "attachments": [
                {
                    "filename": "attachment.pdf",
                    "attachment_id": "primary",
                    "size": 100,
                    "status": "parsed",
                    "source_file_ref": "private:po-coverage",
                    "lines": [
                        {"requested_item_name": "Fire Warden Jacket", "quantity": "20"},
                        {"requested_item_name": "Small Drinking Water 500ml", "quantity": "30"},
                    ],
                }
            ],
        }
        clean_with_ai.return_value = {
            "lines": [{"requested_item_name": "Fire Warden Jacket", "quantity": "20"}],
            "warnings": [],
            "meta": {},
        }

        parse_quote_po_evidence(self.evidence, self.reviewer, use_ai=True, link_approved=True)

        po_import = QuotationOutcomePOImport.objects.get(gmail_evidence=self.evidence)
        lpo = QuotationLPO.objects.get(gmail_evidence=self.evidence)
        self.assertEqual(
            {row["quotation_line_id"] for row in po_import.suggestions},
            {jacket.id, water.id},
        )
        self.assertIn(AI_QUOTE_COVERAGE_GUARD_WARNING, lpo.warnings)
        self.assertEqual(lpo.lpo_number, "PO112_110916")
        for line in (self.line, jacket, water):
            line.refresh_from_db()
            self.assertEqual(line.outcome_status, QuotationLine.OUTCOME_PENDING)

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

    def test_approved_evidence_cannot_be_overwritten_as_not_relevant(self):
        self.evidence.status = QuotationPOEvidence.STATUS_PARSED
        self.evidence.link_approved_by = self.reviewer
        self.evidence.link_approved_at = timezone.now()
        self.evidence.save(
            update_fields=["status", "link_approved_by", "link_approved_at", "updated_at"]
        )
        client = APIClient()
        client.force_authenticate(self.reviewer)

        response = client.post(
            reverse("quotation-mark-po-evidence-not-relevant", args=[self.quotation.id]),
            {"evidence_id": self.evidence.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("cannot be marked not relevant", str(response.json()["detail"]))
        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.status, QuotationPOEvidence.STATUS_PARSED)

    def test_reviewed_evidence_cannot_be_overwritten_as_not_relevant(self):
        self.evidence.status = QuotationPOEvidence.STATUS_PARSED
        self.evidence.link_approved_by = self.reviewer
        self.evidence.link_approved_at = timezone.now()
        self.evidence.save(
            update_fields=["status", "link_approved_by", "link_approved_at", "updated_at"]
        )
        client = APIClient()
        client.force_authenticate(self.reviewer)

        response = client.post(
            reverse("quotation-mark-po-evidence-not-relevant", args=[self.quotation.id]),
            {"evidence_id": self.evidence.id},
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.status, QuotationPOEvidence.STATUS_PARSED)

    def test_outcome_api_returns_every_active_evidence_link_beyond_twelve(self):
        active_ids = {self.evidence.id}
        statuses = [
            QuotationPOEvidence.STATUS_CANDIDATE,
            QuotationPOEvidence.STATUS_AMBIGUOUS,
            QuotationPOEvidence.STATUS_PARSED,
            QuotationPOEvidence.STATUS_FAILED,
        ]
        for index in range(15):
            row = QuotationPOEvidence.objects.create(
                quotation=self.quotation,
                gmail_connection=self.connection,
                mailbox_email=self.connection.email,
                gmail_message_id=f"gmail-active-{index}",
                status=statuses[index % len(statuses)],
                confidence=index,
            )
            active_ids.add(row.id)
        client = APIClient()
        client.force_authenticate(self.reviewer)

        response = client.get(reverse("quotation-outcome", args=[self.quotation.id]))

        self.assertEqual(response.status_code, 200)
        returned_ids = {row["id"] for row in response.data["po_evidence"]}
        self.assertTrue(active_ids.issubset(returned_ids))
        self.assertGreater(len(returned_ids), 12)

    def test_approval_locks_competing_evidence_in_primary_key_order(self):
        peer_ids = []
        for index, confidence in enumerate((10, 99), start=1):
            other_quote = Quotation.objects.create(
                company=self.company,
                quotation_number=f"QT-20260713-007{index}",
                status=Quotation.STATUS_SENT,
                sent_at=self.sent_at - timedelta(minutes=index),
                created_by=self.reviewer,
            )
            peer = QuotationPOEvidence.objects.create(
                quotation=other_quote,
                gmail_connection=self.connection,
                mailbox_email=self.connection.email,
                gmail_message_id=self.evidence.gmail_message_id,
                status=QuotationPOEvidence.STATUS_AMBIGUOUS,
                confidence=confidence,
            )
            peer_ids.append(peer.id)

        with transaction.atomic():
            locked_evidence, locked_peer_ids = _lock_and_resolve_evidence_approval(
                self.evidence,
                self.connection,
                {"subject": self.evidence.subject},
            )

        self.assertEqual(locked_evidence.id, self.evidence.id)
        self.assertEqual(locked_peer_ids, sorted(peer_ids))

    @patch("quotations.quote_po_intelligence.gmail_fetch_message")
    def test_approval_fails_closed_when_same_message_is_already_reviewed_for_another_quote(self, fetch):
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0067",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at - timedelta(minutes=5),
            created_by=self.reviewer,
        )
        QuotationPOEvidence.objects.create(
            quotation=other_quote,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id=self.evidence.gmail_message_id,
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_by=self.reviewer,
            link_approved_at=timezone.now(),
        )
        fetch.return_value = {
            "gmail_message_id": self.evidence.gmail_message_id,
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=10),
            "snippet": "Purchase order attached",
            "body_text": "Purchase Order No: LPO-77",
            "attachments": [],
        }

        with self.assertRaisesMessage(Exception, "already approved or parsed"):
            parse_quote_po_evidence(
                self.evidence,
                self.reviewer,
                use_ai=False,
                link_approved=True,
            )

        self.evidence.refresh_from_db()
        self.assertEqual(self.evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertFalse(QuotationOutcomePOImport.objects.filter(gmail_evidence=self.evidence).exists())
        self.assertFalse(QuotationLPO.objects.filter(gmail_evidence=self.evidence).exists())

    @patch("quotations.quote_po_intelligence.gmail_fetch_message")
    def test_explicit_approval_resolves_unreviewed_ambiguous_peer(self, fetch):
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260713-0067",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at - timedelta(minutes=5),
            created_by=self.reviewer,
        )
        self.evidence.status = QuotationPOEvidence.STATUS_AMBIGUOUS
        self.evidence.save(update_fields=["status", "updated_at"])
        peer = QuotationPOEvidence.objects.create(
            quotation=other_quote,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id=self.evidence.gmail_message_id,
            status=QuotationPOEvidence.STATUS_AMBIGUOUS,
        )
        fetch.return_value = {
            "gmail_message_id": self.evidence.gmail_message_id,
            "sender": "buyer@acme.example",
            "recipients": self.connection.email,
            "subject": f"LPO for {self.quotation.quotation_number}",
            "sent_at": self.sent_at + timedelta(minutes=10),
            "snippet": "Purchase order attached",
            "body_text": "Purchase Order No: LPO-77\nBandage Pack 2 box 10",
            "attachments": [],
        }

        parse_quote_po_evidence(
            self.evidence,
            self.reviewer,
            use_ai=False,
            link_approved=True,
        )

        self.evidence.refresh_from_db()
        peer.refresh_from_db()
        self.assertEqual(self.evidence.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(peer.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn(self.quotation.quotation_number, peer.error)


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
    @patch("quotations.management.commands.audit_shared_mailbox_lpos.reconcile_mailbox_po_audit")
    @patch("quotations.management.commands.audit_shared_mailbox_lpos.scan_mailbox_po_audit_page")
    @patch("quotations.management.commands.audit_shared_mailbox_lpos.start_mailbox_po_audit")
    def test_legacy_command_runs_the_global_review_only_mailbox_audit(
        self,
        start_audit,
        scan_page,
        reconcile,
    ):
        owner = User.objects.create_user("command-owner", is_staff=True)
        connection = GmailOAuthConnection.objects.create(
            user=owner,
            email="orders@pharmacy.example",
            is_shared=True,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=connection,
            requested_by=owner,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=timezone.now() - timedelta(days=7),
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )
        start_audit.return_value = run
        reconcile.return_value = SimpleNamespace(
            id=17,
            status="completed",
            summary={"automatic_messages": 1, "ambiguous_messages": 2},
            errors=[],
        )
        stdout = StringIO()

        call_command("scan_shared_mailbox_lpos", stdout=stdout)

        self.assertIn("Mailbox-wide LPO audit completed", stdout.getvalue())
        start_audit.assert_called_once_with(connection, requested_by=owner)
        scan_page.assert_not_called()
        reconcile.assert_called_once_with(run, requested_by=owner)


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

    @patch("quotations.contract_intelligence._json_request")
    @patch("quotations.contract_intelligence._form_request")
    def test_superuser_reauthorizing_same_mailbox_preserves_connection_lineage(
        self,
        form_request,
        json_request,
    ):
        form_request.return_value = {
            "access_token": "replacement-access-token",
            "refresh_token": "replacement-refresh-token",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.readonly",
        }
        json_request.return_value = {"emailAddress": self.connection.email.upper()}

        refreshed = exchange_gmail_code(self.superuser, "oauth-code")

        self.assertEqual(refreshed.pk, self.connection.pk)
        self.assertEqual(refreshed.user_id, self.owner.id)
        self.assertTrue(refreshed.is_shared)
        self.assertEqual(GmailOAuthConnection.objects.count(), 1)


class SharedMailboxInitialSetupTests(TestCase):
    @patch("quotations.views.build_gmail_auth_url", return_value="https://accounts.example/auth")
    def test_staff_can_start_initial_setup_when_no_shared_mailbox_exists(self, auth_url):
        staff = User.objects.create_user("first-gmail-owner", is_staff=True)
        client = APIClient()
        client.force_authenticate(staff)

        response = client.post(reverse("quotation-gmail-connection"), {}, format="json")

        self.assertEqual(response.status_code, 200)
        auth_url.assert_called_once()
