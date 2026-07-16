from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import (
    Company,
    GmailOAuthConnection,
    MailboxPOMessage,
    Quotation,
    QuotationLine,
    QuotationOutcomePOImport,
    QuotationPOEvidence,
)
from .services import update_quotation_outcome


class POEvidenceCommercialComparisonTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("commercial-reviewer", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(name="Complete Buyer LLC")
        self.quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260716-0901",
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(days=1),
            subtotal=Decimal("320.00"),
            vat_total=Decimal("16.00"),
            total=Decimal("336.00"),
            created_by=self.staff,
        )
        self.line_exact = self._line(
            "Nitrile Gloves Blue Size M Box 100", 10, 10, "box", 0
        )
        self.line_reduced = self._line(
            "Sterile Gauze Swab 10 x 10 cm", 5, 20, "box", 1
        )
        self.line_repriced = self._line(
            "Digital Thermometer DT-100", 2, 30, "each", 2
        )
        self.line_not_ordered = self._line(
            "Hand Sanitizer 500 ml", 4, 15, "bottle", 3
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def _line(self, name, quantity, unit_price, unit, order):
        return QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot=name,
            quantity=Decimal(str(quantity)),
            unit_price=Decimal(str(unit_price)),
            vat_rate=Decimal("5.00"),
            unit=unit,
            sort_order=order,
        )

    def _evidence(
        self,
        rows,
        *,
        message_id="commercial-source",
        attachment_id="stable-part-1",
        source_hash="a" * 64,
        warnings=None,
        totals=None,
        status=QuotationPOEvidence.STATUS_CANDIDATE,
    ):
        attachment = {
            "attachment_id": "gmail-token-1",
            "part_id": attachment_id,
            "filename": "LPO-7788.pdf",
            "mime_type": "application/pdf",
            "status": "parsed",
            "source_sha256": source_hash,
            "original_text": "Purchase Order\nPO No: LPO-7788",
            "lines": rows,
            "line_count": len(rows),
            "warnings": warnings or [],
            "totals": totals or {},
        }
        message = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=message_id,
            mailbox_email=self.connection.email,
            label_ids=["INBOX"],
            subject="Purchase order attached",
            sender="Buyer <buyer@complete.example>",
            recipients=self.connection.email,
            sent_at=timezone.now(),
            newest_body_text="Please process the attached purchase order.",
            attachment_manifest=[attachment],
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=True,
        )
        return QuotationPOEvidence.objects.create(
            quotation=self.quote,
            mailbox_message=message,
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_message_id=message_id,
            selected_attachment_id=attachment_id,
            selected_attachment_filename="LPO-7788.pdf",
            source_sha256=source_hash,
            attachments=[{**attachment, "is_selected": True}],
            match_signals={
                "source": {
                    "kind": "attachment",
                    "attachment_id": attachment_id,
                    "filename": "LPO-7788.pdf",
                },
                "lpo_references": ["LPO-7788"],
                "candidate": {"document_total_result": "conflict"},
            },
            status=status,
            created_by=self.staff,
        )

    def _source(self, evidence):
        return self.client.get(
            reverse("quotation-po-evidence-source", args=[evidence.id])
        )

    def test_existing_scanned_evidence_returns_self_contained_commercial_comparison(self):
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "boxes",
                    "unit_price": "10",
                    "line_total": "100",
                },
                {
                    "raw_name": self.line_reduced.item_name_snapshot,
                    "quantity": "3",
                    "unit": "box",
                    "unit_price": "20",
                    "line_total": "60",
                },
                {
                    "raw_name": self.line_repriced.item_name_snapshot,
                    "quantity": "2",
                    "unit": "each",
                    "unit_price": "28",
                    "line_total": "56",
                },
                {
                    "raw_name": "216.00",
                    "line_total": "216.00",
                },
            ],
            totals={"grand_total": "216.00"},
        )

        response = self._source(evidence)

        self.assertEqual(response.status_code, 200)
        comparison = response.data["commercial_comparison"]
        self.assertEqual(comparison["company_name"], "Complete Buyer LLC")
        self.assertEqual(comparison["quotation_number"], "QT-20260716-0901")
        self.assertEqual(comparison["quotation_subtotal"], "320.00")
        self.assertEqual(comparison["quotation_vat_total"], "16.00")
        self.assertEqual(comparison["quotation_total"], "336.00")
        self.assertEqual(comparison["lpo_number"], "LPO-7788")
        self.assertEqual(comparison["lpo_total"], "216.00")
        self.assertEqual(comparison["total_result"], "conflict")
        self.assertEqual(comparison["total_basis"], "conflict")
        self.assertEqual(comparison["parse_source"], "mailbox_deterministic")
        self.assertTrue(comparison["complete_for_missing_lines"])

        lines = {line["quotation_line_id"]: line for line in comparison["lines"]}
        self.assertEqual(lines[self.line_exact.id]["status"], "accepted")
        self.assertEqual(lines[self.line_exact.id]["accepted_unit_price"], "10")
        self.assertEqual(lines[self.line_reduced.id]["status"], "reduced")
        self.assertEqual(lines[self.line_reduced.id]["accepted_quantity"], "3")
        self.assertEqual(lines[self.line_repriced.id]["status"], "repriced")
        self.assertEqual(lines[self.line_repriced.id]["accepted_unit_price"], "28")
        self.assertEqual(lines[self.line_not_ordered.id]["status"], "not_ordered")
        self.assertTrue(lines[self.line_not_ordered.id]["not_on_lpo"])
        self.assertIn("not an explicit customer rejection", lines[self.line_not_ordered.id]["reason"])

    def test_source_returns_latest_parsed_import_for_reload(self):
        evidence = self._evidence(
            [],
            message_id="reload-latest-import",
            attachment_id="stable-reload-import",
            status=QuotationPOEvidence.STATUS_PARSED,
        )
        older = QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="older.pdf",
            suggestions=[
                {
                    "quotation_line_id": self.line_exact.id,
                    "suggested_accepted_quantity": "10",
                    "suggested_accepted_unit_price": "9",
                }
            ],
            missing_quote_line_ids=[self.line_reduced.id],
            created_by=self.staff,
        )
        latest_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "8",
            "line_total": "80",
        }
        latest = QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="latest.pdf",
            parsed_rows=[latest_row],
            suggestions=[
                {
                    "po_row_number": 1,
                    "po_row": latest_row,
                    "po_item_name": self.line_exact.item_name_snapshot,
                    "quotation_line_id": self.line_exact.id,
                    "suggested_accepted_quantity": "10",
                    "suggested_accepted_unit_price": "8",
                    "confidence": 99,
                }
            ],
            missing_quote_line_ids=[
                self.line_reduced.id,
                self.line_repriced.id,
                self.line_not_ordered.id,
            ],
            created_by=self.staff,
        )

        with CaptureQueriesContext(connection) as captured_queries:
            response = self._source(evidence)

        self.assertEqual(response.status_code, 200)
        payload = response.data["latest_po_import"]
        self.assertNotEqual(payload["id"], older.id)
        self.assertEqual(payload["id"], latest.id)
        self.assertEqual(payload["quotation"], self.quote.id)
        self.assertEqual(payload["gmail_evidence"], evidence.id)
        self.assertEqual(payload["suggestions"], latest.suggestions)
        self.assertEqual(
            payload["missing_quote_line_ids"],
            latest.missing_quote_line_ids,
        )
        self.assertIsNone(payload["canonical_lpo"])
        self.assertNotIn("commercial_comparison", payload)
        import_queries = [
            query["sql"]
            for query in captured_queries.captured_queries
            if "quotations_quotationoutcomepoimport" in query["sql"].lower()
        ]
        self.assertEqual(len(import_queries), 1)
        self.assertIn("LIMIT 1", import_queries[0].upper())
        comparison = response.data["commercial_comparison"]
        self.assertEqual(
            comparison["parse_source"],
            "approved_po_import",
        )
        compared_line = next(
            line
            for line in comparison["lines"]
            if line["quotation_line_id"] == self.line_exact.id
        )
        self.assertEqual(compared_line["accepted_unit_price"], "8")

    def test_source_import_is_scoped_to_exact_evidence_and_quotation(self):
        evidence = self._evidence(
            [],
            message_id="reload-import-scope",
            attachment_id="stable-import-scope",
            status=QuotationPOEvidence.STATUS_PARSED,
        )
        expected = QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="expected.pdf",
            suggestions=[{"quotation_line_id": self.line_exact.id}],
            created_by=self.staff,
        )
        other_quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260716-0902",
            status=Quotation.STATUS_SENT,
            created_by=self.staff,
        )
        QuotationOutcomePOImport.objects.create(
            quotation=other_quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="wrong-quotation.pdf",
            suggestions=[{"quotation_line_id": self.line_reduced.id}],
            created_by=self.staff,
        )
        other_evidence = self._evidence(
            [],
            message_id="reload-other-evidence",
            attachment_id="stable-other-evidence",
            status=QuotationPOEvidence.STATUS_PARSED,
        )
        QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=other_evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="wrong-evidence.pdf",
            suggestions=[{"quotation_line_id": self.line_repriced.id}],
            created_by=self.staff,
        )
        QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="failed.pdf",
            status=QuotationOutcomePOImport.STATUS_FAILED,
            suggestions=[{"quotation_line_id": self.line_not_ordered.id}],
            created_by=self.staff,
        )

        response = self._source(evidence)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["latest_po_import"]["id"], expected.id)
        self.assertEqual(
            response.data["latest_po_import"]["suggestions"],
            expected.suggestions,
        )

    def test_source_returns_null_latest_import_when_evidence_has_no_import(self):
        evidence = self._evidence(
            [],
            message_id="reload-no-import",
            attachment_id="stable-no-import",
            status=QuotationPOEvidence.STATUS_PARSED,
        )

        response = self._source(evidence)

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["latest_po_import"])

    def test_missing_po_quantity_and_price_never_use_quotation_fallbacks(self):
        line_without_quote_price = QuotationLine.objects.create(
            quotation=self.quote,
            item_name_snapshot="Disposable Apron Blue",
            quantity=Decimal("6"),
            unit_price=None,
            unit="piece",
            sort_order=4,
        )
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                },
                {
                    "raw_name": self.line_reduced.item_name_snapshot,
                    "unit": "box",
                    "unit_price": "20",
                },
                {
                    "raw_name": line_without_quote_price.item_name_snapshot,
                    "quantity": "6",
                    "unit": "piece",
                    "unit_price": "12",
                },
            ],
            message_id="missing-values",
            attachment_id="stable-part-missing",
            source_hash="b" * 64,
        )

        comparison = self._source(evidence).data["commercial_comparison"]
        lines = {line["quotation_line_id"]: line for line in comparison["lines"]}

        self.assertIsNone(comparison["lpo_total"])
        self.assertEqual(comparison["total_result"], "unknown")
        self.assertEqual(comparison["total_basis"], "unknown")

        price_missing = lines[self.line_exact.id]
        self.assertEqual(price_missing["status"], "accepted_price_not_stated")
        self.assertEqual(price_missing["accepted_quantity"], "10")
        self.assertIsNone(price_missing["accepted_unit_price"])
        self.assertTrue(price_missing["review_required"])

        quantity_missing = lines[self.line_reduced.id]
        self.assertEqual(quantity_missing["status"], "uncertain")
        self.assertIsNone(quantity_missing["accepted_quantity"])
        self.assertEqual(quantity_missing["accepted_unit_price"], "20")
        self.assertTrue(quantity_missing["review_required"])

        quotation_price_missing = lines[line_without_quote_price.id]
        self.assertEqual(quotation_price_missing["status"], "uncertain")
        self.assertEqual(quotation_price_missing["accepted_unit_price"], "12")
        self.assertIn("quotation does not contain", quotation_price_missing["reason"].lower())

    def test_lpo_total_reports_whether_it_matches_net_or_vat_inclusive_quote_total(self):
        row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "10",
            "line_total": "100",
        }
        cases = (
            ("gross-total", "f" * 64, "336.00", "quotation_total_incl_vat"),
            ("net-total", "1" * 64, "320.00", "quotation_subtotal_ex_vat"),
        )
        for message_id, source_hash, total, expected_basis in cases:
            with self.subTest(total=total):
                evidence = self._evidence(
                    [row],
                    message_id=message_id,
                    attachment_id=f"part-{message_id}",
                    source_hash=source_hash,
                    totals={"grand_total": total},
                )
                comparison = self._source(evidence).data["commercial_comparison"]
                self.assertEqual(comparison["total_result"], "exact")
                self.assertEqual(comparison["total_basis"], expected_basis)

    def test_unmatched_commercial_row_keeps_absent_quote_lines_uncertain(self):
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "100",
                },
                {
                    "raw_name": "Completely different customer item ZX-999",
                    "quantity": "7",
                    "unit": "each",
                    "unit_price": "4.50",
                    "line_total": "31.50",
                },
            ],
            message_id="unmatched-row",
            attachment_id="stable-part-unmatched",
            source_hash="c" * 64,
        )

        comparison = self._source(evidence).data["commercial_comparison"]
        lines = {line["quotation_line_id"]: line for line in comparison["lines"]}

        self.assertFalse(comparison["complete_for_missing_lines"])
        self.assertEqual(lines[self.line_not_ordered.id]["status"], "uncertain")
        self.assertFalse(lines[self.line_not_ordered.id]["not_on_lpo"])
        self.assertEqual(len(comparison["unmatched_lpo_rows"]), 1)
        unmatched = comparison["unmatched_lpo_rows"][0]
        self.assertEqual(unmatched["lpo_item_name"], "Completely different customer item ZX-999")
        self.assertEqual(unmatched["accepted_quantity"], "7")
        self.assertEqual(unmatched["accepted_unit_price"], "4.50")

    def test_latest_import_is_authoritative_even_if_evidence_status_is_stale(self):
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            message_id="parsed-source",
            attachment_id="stable-part-parsed",
            source_hash="d" * 64,
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )
        parsed_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "8",
            "line_total": "80",
        }
        po_import = QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename="LPO-7788.pdf",
            parsed_rows=[parsed_row],
            suggestions=[
                {
                    "po_row_number": 1,
                    "po_row": parsed_row,
                    "po_item_name": self.line_exact.item_name_snapshot,
                    "quotation_line_id": self.line_exact.id,
                    "suggested_accepted_quantity": "10",
                    "suggested_accepted_unit_price": "8",
                    "confidence": 99,
                }
            ],
            missing_quote_line_ids=[
                self.line_reduced.id,
                self.line_repriced.id,
                self.line_not_ordered.id,
            ],
            created_by=self.staff,
        )

        source_comparison = self._source(evidence).data["commercial_comparison"]
        from .serializers import QuotationOutcomePOImportSerializer

        import_comparison = QuotationOutcomePOImportSerializer(po_import).data[
            "commercial_comparison"
        ]

        for comparison in (source_comparison, import_comparison):
            self.assertEqual(comparison["parse_source"], "approved_po_import")
            line = next(
                line
                for line in comparison["lines"]
                if line["quotation_line_id"] == self.line_exact.id
            )
            self.assertEqual(line["status"], "repriced")
            self.assertEqual(line["accepted_unit_price"], "8")

    def test_tied_source_identity_fails_closed_instead_of_borrowing_first_attachment(self):
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit_price": "10",
                }
            ],
            message_id="tied-source",
            attachment_id="",
            source_hash="",
        )
        second = {
            **evidence.mailbox_message.attachment_manifest[0],
            "attachment_id": "gmail-token-2",
            "part_id": "stable-part-2",
            "filename": "LPO-other.pdf",
            "source_sha256": "e" * 64,
            "lines": [
                {
                    "raw_name": self.line_reduced.item_name_snapshot,
                    "quantity": "5",
                    "unit_price": "20",
                }
            ],
        }
        evidence.mailbox_message.attachment_manifest = [
            evidence.mailbox_message.attachment_manifest[0],
            second,
        ]
        evidence.mailbox_message.save(update_fields=["attachment_manifest", "updated_at"])
        evidence.attachments = []
        evidence.selected_attachment_filename = ""
        evidence.match_signals = {"source": {"kind": "attachment"}}
        evidence.save(
            update_fields=[
                "attachments",
                "selected_attachment_filename",
                "match_signals",
                "updated_at",
            ]
        )

        comparison = self._source(evidence).data["commercial_comparison"]

        self.assertEqual(comparison["parse_source"], "unavailable")
        self.assertFalse(comparison["complete_for_missing_lines"])
        self.assertTrue(all(line["status"] == "uncertain" for line in comparison["lines"]))

    def test_stored_attachment_fallback_requires_unique_identity_but_unique_hash_wins(self):
        exact_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "10",
            "line_total": "100",
        }
        other_row = {
            "raw_name": self.line_reduced.item_name_snapshot,
            "quantity": "5",
            "unit": "box",
            "unit_price": "20",
            "line_total": "100",
        }

        duplicate_filename = self._evidence(
            [],
            message_id="stored-duplicate-filename",
            attachment_id="",
            source_hash="",
        )
        duplicate_filename.mailbox_message = None
        duplicate_filename.selected_attachment_filename = "duplicate.pdf"
        duplicate_filename.attachments = [
            {
                "filename": "duplicate.pdf",
                "source_sha256": "1" * 64,
                "status": "parsed",
                "lines": [exact_row],
            },
            {
                "filename": "duplicate.pdf",
                "source_sha256": "2" * 64,
                "status": "parsed",
                "lines": [other_row],
            },
        ]
        duplicate_filename.match_signals = {
            "source": {"kind": "attachment", "filename": "duplicate.pdf"}
        }
        duplicate_filename.save(
            update_fields=[
                "mailbox_message",
                "selected_attachment_filename",
                "attachments",
                "match_signals",
                "updated_at",
            ]
        )

        duplicate_flags = self._evidence(
            [],
            message_id="stored-duplicate-flags",
            attachment_id="",
            source_hash="",
        )
        duplicate_flags.mailbox_message = None
        duplicate_flags.selected_attachment_filename = ""
        duplicate_flags.attachments = [
            {
                "filename": "first.pdf",
                "source_sha256": "3" * 64,
                "is_selected": True,
                "status": "parsed",
                "lines": [exact_row],
            },
            {
                "filename": "second.pdf",
                "source_sha256": "4" * 64,
                "is_selected": True,
                "status": "parsed",
                "lines": [other_row],
            },
        ]
        duplicate_flags.match_signals = {"source": {"kind": "attachment"}}
        duplicate_flags.save(
            update_fields=[
                "mailbox_message",
                "selected_attachment_filename",
                "attachments",
                "match_signals",
                "updated_at",
            ]
        )

        unique_hash = self._evidence(
            [],
            message_id="stored-unique-hash",
            attachment_id="",
            source_hash="",
        )
        unique_hash.mailbox_message = None
        unique_hash.source_sha256 = "5" * 64
        unique_hash.selected_attachment_filename = "duplicate.pdf"
        unique_hash.attachments = [
            {
                "filename": "duplicate.pdf",
                "source_sha256": "5" * 64,
                "is_selected": True,
                "status": "parsed",
                "lines": [exact_row],
            },
            {
                "filename": "duplicate.pdf",
                "source_sha256": "6" * 64,
                "is_selected": True,
                "status": "parsed",
                "lines": [other_row],
            },
        ]
        unique_hash.match_signals = {
            "source": {"kind": "attachment", "filename": "duplicate.pdf"}
        }
        unique_hash.save(
            update_fields=[
                "mailbox_message",
                "source_sha256",
                "selected_attachment_filename",
                "attachments",
                "match_signals",
                "updated_at",
            ]
        )

        for ambiguous in (duplicate_filename, duplicate_flags):
            comparison = self._source(ambiguous).data["commercial_comparison"]
            self.assertEqual(comparison["parse_source"], "unavailable")
            self.assertFalse(comparison["complete_for_missing_lines"])
            self.assertTrue(
                all(line["status"] == "uncertain" for line in comparison["lines"])
            )

        comparison = self._source(unique_hash).data["commercial_comparison"]
        self.assertEqual(comparison["parse_source"], "stored_attachment")
        selected_line = next(
            line
            for line in comparison["lines"]
            if line["quotation_line_id"] == self.line_exact.id
        )
        self.assertEqual(selected_line["status"], "accepted")

    def test_material_parser_warnings_are_visible_and_block_omitted_line_inference(self):
        evidence = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            message_id="material-warning",
            attachment_id="part-material-warning",
            source_hash="7" * 64,
            warnings=[
                "OCR fallback stopped reading: no clear header and total arithmetic is incomplete."
            ],
        )

        comparison = self._source(evidence).data["commercial_comparison"]

        self.assertFalse(comparison["complete_for_missing_lines"])
        self.assertTrue(any("stopped reading" in warning for warning in comparison["warnings"]))
        missing_line = next(
            line
            for line in comparison["lines"]
            if line["quotation_line_id"] == self.line_not_ordered.id
        )
        self.assertEqual(missing_line["status"], "uncertain")

        material_only = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            message_id="material-warning-meta-only",
            attachment_id="part-material-meta",
            source_hash="d" * 64,
        )
        manifest = list(material_only.mailbox_message.attachment_manifest)
        manifest[0] = {
            **manifest[0],
            "meta": {"aggregate_po_summary_detected": True},
        }
        material_only.mailbox_message.attachment_manifest = manifest
        material_only.mailbox_message.save(
            update_fields=["attachment_manifest", "updated_at"]
        )

        material_only_comparison = self._source(material_only).data[
            "commercial_comparison"
        ]

        self.assertTrue(
            any(
                "Aggregate PO summary rows" in warning
                for warning in material_only_comparison["warnings"]
            )
        )
        self.assertFalse(material_only_comparison["complete_for_missing_lines"])

    def test_contradictory_lpo_line_arithmetic_is_uncertain_with_one_cent_tolerance(self):
        contradictory = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "999",
                }
            ],
            message_id="arithmetic-conflict",
            attachment_id="part-arithmetic-conflict",
            source_hash="8" * 64,
        )
        tolerated = self._evidence(
            [
                {
                    "raw_name": self.line_exact.item_name_snapshot,
                    "quantity": "10",
                    "unit": "box",
                    "unit_price": "10",
                    "line_total": "100.01",
                }
            ],
            message_id="arithmetic-tolerance",
            attachment_id="part-arithmetic-tolerance",
            source_hash="9" * 64,
        )

        conflict_line = next(
            line
            for line in self._source(contradictory).data["commercial_comparison"]["lines"]
            if line["quotation_line_id"] == self.line_exact.id
        )
        tolerated_line = next(
            line
            for line in self._source(tolerated).data["commercial_comparison"]["lines"]
            if line["quotation_line_id"] == self.line_exact.id
        )

        self.assertEqual(conflict_line["status"], "uncertain")
        self.assertEqual(conflict_line["accepted_arithmetic_result"], "conflict")
        self.assertIn("arithmetic conflicts", conflict_line["reason"])
        self.assertEqual(tolerated_line["status"], "accepted")
        self.assertEqual(tolerated_line["accepted_arithmetic_result"], "exact")

    def test_missing_line_completeness_rejects_stale_suggestion_ids_and_bad_coverage(self):
        parsed_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "10",
            "line_total": "100",
        }
        cases = (
            (
                "stale-suggestion-id",
                999999,
                [
                    self.line_exact.id,
                    self.line_reduced.id,
                    self.line_repriced.id,
                    self.line_not_ordered.id,
                ],
            ),
            ("bad-missing-coverage", self.line_exact.id, []),
        )
        for message_id, suggestion_line_id, missing_ids in cases:
            with self.subTest(message_id=message_id):
                evidence = self._evidence(
                    [parsed_row],
                    message_id=message_id,
                    attachment_id=f"part-{message_id}",
                    source_hash=("a" if message_id.startswith("stale") else "b") * 64,
                )
                QuotationOutcomePOImport.objects.create(
                    quotation=self.quote,
                    gmail_evidence=evidence,
                    source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
                    parsed_rows=[parsed_row],
                    suggestions=[
                        {
                            "po_row_number": 1,
                            "po_row": parsed_row,
                            "po_item_name": self.line_exact.item_name_snapshot,
                            "quotation_line_id": suggestion_line_id,
                            "confidence": 99,
                        }
                    ],
                    missing_quote_line_ids=missing_ids,
                    created_by=self.staff,
                )

                comparison = self._source(evidence).data["commercial_comparison"]

                self.assertFalse(comparison["complete_for_missing_lines"])
                self.assertFalse(
                    any(line["status"] == "not_ordered" for line in comparison["lines"])
                )

    def test_parsed_rows_override_disagreeing_embedded_suggestion_values_and_fail_closed(self):
        parsed_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "10",
            "unit": "box",
            "unit_price": "10",
            "line_total": "100",
        }
        stale_row = {
            "raw_name": self.line_exact.item_name_snapshot,
            "quantity": "999",
            "unit": "box",
            "unit_price": "777",
            "line_total": "1",
        }
        evidence = self._evidence(
            [parsed_row],
            message_id="authoritative-parsed-row",
            attachment_id="part-authoritative-row",
            source_hash="c" * 64,
        )
        QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            parsed_rows=[parsed_row],
            suggestions=[
                {
                    "po_row_number": 1,
                    "po_row": stale_row,
                    "po_item_name": self.line_exact.item_name_snapshot,
                    "po_quantity": "999",
                    "po_unit_price": "777",
                    "quotation_line_id": self.line_exact.id,
                    "confidence": 99,
                }
            ],
            missing_quote_line_ids=[
                self.line_reduced.id,
                self.line_repriced.id,
                self.line_not_ordered.id,
            ],
            created_by=self.staff,
        )

        comparison = self._source(evidence).data["commercial_comparison"]
        line = next(
            line
            for line in comparison["lines"]
            if line["quotation_line_id"] == self.line_exact.id
        )

        self.assertEqual(line["accepted_quantity"], "10")
        self.assertEqual(line["accepted_unit_price"], "10")
        self.assertEqual(line["accepted_line_total"], "100")
        self.assertEqual(line["status"], "uncertain")
        self.assertIn("authoritative parsed LPO row", line["reason"])
        self.assertFalse(comparison["complete_for_missing_lines"])

    def test_applying_matched_and_explicitly_omitted_lines_uses_valid_po_provenance(self):
        po_import = QuotationOutcomePOImport.objects.create(
            quotation=self.quote,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            suggestions=[
                {
                    "quotation_line_id": self.line_exact.id,
                    "po_row_number": 1,
                    "po_quantity": "10",
                    "po_unit_price": "10",
                }
            ],
            missing_quote_line_ids=[self.line_not_ordered.id],
            created_by=self.staff,
        )

        # The omitted line is an explicit staff outcome, not a parsed PO
        # suggestion. Only the actual matched line receives PO provenance.
        update_quotation_outcome(
            self.quote,
            {
                "line_updates": [
                    {
                        "id": self.line_exact.id,
                        "outcome_status": QuotationLine.OUTCOME_ACCEPTED,
                        "accepted_quantity": "10",
                        "accepted_unit_price": "10",
                    },
                    {
                        "id": self.line_not_ordered.id,
                        "outcome_status": QuotationLine.OUTCOME_REJECTED,
                        "outcome_notes": "Not ordered on the reviewed LPO.",
                    },
                ],
                "po_import_id": po_import.id,
                "applied_po_line_ids": [self.line_exact.id],
            },
            self.staff,
        )

        self.line_exact.refresh_from_db()
        self.line_not_ordered.refresh_from_db()
        po_import.refresh_from_db()
        self.assertEqual(self.line_exact.outcome_status, QuotationLine.OUTCOME_ACCEPTED)
        self.assertEqual(
            self.line_not_ordered.outcome_status,
            QuotationLine.OUTCOME_REJECTED,
        )
        self.assertTrue(po_import.suggestions[0]["outcome_applied"])
        self.assertEqual(
            {row["quotation_line_id"] for row in po_import.suggestions},
            {self.line_exact.id},
        )

    @patch(
        "quotations.views.safe_build_po_evidence_commercial_comparison",
        side_effect=RuntimeError("legacy malformed row"),
    )
    def test_source_text_remains_available_if_comparison_builder_fails(self, _mock_comparison):
        evidence = self._evidence([], message_id="fail-soft", attachment_id="stable-fail")
        evidence.extracted_text = "Original evidence text remains viewable"
        evidence.save(update_fields=["extracted_text", "updated_at"])

        with self.assertLogs("quotations.views", level="ERROR") as captured:
            response = self._source(evidence)

        # The source view normally calls the safe builder. This direct patch
        # verifies the endpoint itself must not trust a comparison failure.
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["extracted_text"], "Original evidence text remains viewable")
        self.assertEqual(
            response.data["commercial_comparison"]["parse_source"],
            "unavailable",
        )
        self.assertTrue(any("Commercial comparison failed" in row for row in captured.output))
