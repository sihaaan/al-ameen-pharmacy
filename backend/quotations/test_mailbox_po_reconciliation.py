from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .mailbox_po_matching import AMBIGUOUS, AUTOMATIC, UNMATCHED, rank_message_to_quotations
from .mailbox_po_reconciliation import (
    ALGORITHM_VERSION,
    _dedupe_and_cap,
    _locked_lineage_message_evidence_queryset,
    _supersede_stale_evidence,
    document_variants,
    eligible_quotations,
    reconcile_mailbox_po_audit,
)
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


class MailboxPOReconciliationTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("mailbox-matcher", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@pharmacy.example",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.company = Company.objects.create(name="Acme Medical", email="buyer@acme.example")
        self.sent_at = timezone.now() - timedelta(days=2)
        self.quote = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0001",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            subtotal=Decimal("150.00"),
            total=Decimal("150.00"),
            created_by=self.staff,
        )
        self.line_1 = self.line(self.quote, "Nitrile Gloves Blue Size M Box 100", 10, 10, 0)
        self.line_2 = self.line(self.quote, "Sterile Gauze Swab 10 x 10 cm", 5, 10, 1)
        self.run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

    def line(self, quote, name, quantity, price, order):
        return QuotationLine.objects.create(
            quotation=quote,
            item_name_snapshot=name,
            quantity=Decimal(str(quantity)),
            unit_price=Decimal(str(price)),
            sort_order=order,
        )

    def test_rotated_mailbox_proposal_lock_targets_only_evidence_rows(self):
        queryset = _locked_lineage_message_evidence_queryset(
            self.connection,
            "gmail-postgres-lineage-lock",
        )

        self.assertTrue(queryset.query.select_for_update)
        self.assertEqual(queryset.query.select_for_update_of, ("self",))

    def message(self, message_id, *, rows=None, subject="Purchase Order attached", body="Please proceed", labels=None, attachment=True):
        manifest = []
        if attachment:
            manifest.append(
                {
                    "attachment_id": f"att-{message_id}",
                    "part_id": "1",
                    "filename": f"LPO-{message_id}.pdf",
                    "mime_type": "application/pdf",
                    "size": 1200,
                    "status": "parsed",
                    "source_sha256": (message_id.encode("utf-8").hex() + "0" * 64)[:64],
                    "original_text": body,
                    "lines": rows or [],
                    "line_count": len(rows or []),
                }
            )
        return MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=message_id,
            gmail_thread_id=f"thread-{message_id}",
            mailbox_email=self.connection.email,
            label_ids=labels or ["INBOX"],
            subject=subject,
            sender="Acme Buyer <buyer@acme.example>",
            recipients=self.connection.email,
            sent_at=self.sent_at + timedelta(hours=12),
            snippet=body,
            newest_body_text=body,
            attachment_manifest=manifest,
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            auto_link_eligible=not bool({"SPAM", "TRASH"}.intersection(labels or [])),
            first_seen_run=self.run,
            last_seen_run=self.run,
            full_message_fetched_at=timezone.now(),
            attachments_audited_at=timezone.now(),
            last_audited_at=timezone.now(),
        )

    def evidence(
        self,
        quote,
        message_id,
        po_reference,
        *,
        score,
        exact_reference=False,
        status=QuotationPOEvidence.STATUS_CANDIDATE,
        source_sha256="",
        subject="",
        sent_at=None,
        source_kind="attachment",
        filename="",
        extracted_text="",
    ):
        return QuotationPOEvidence.objects.create(
            quotation=quote,
            gmail_connection=self.connection,
            gmail_message_id=message_id,
            source_key=f"message:{message_id}",
            source_sha256=source_sha256,
            quote_reference_present=exact_reference,
            confidence=int(score),
            status=status,
            subject=subject,
            selected_attachment_filename=filename,
            extracted_text=extracted_text,
            match_signals={
                "lpo_references": [po_reference] if po_reference else [],
                "source": {"kind": source_kind},
                "candidate": {
                    "score": score,
                    "exact_quote_reference": exact_reference,
                    "item_coverage": 1.0,
                    "po_line_count": 1,
                    "quantity_exact_count": 1,
                    "quantity_reduced_count": 0,
                    "commercial_row_coverage": 1.0,
                    "components": [
                        {"signal": "customer_identity", "score": 10},
                        {"signal": "time_distance", "score": 8},
                    ],
                },
            },
            sent_at=sent_at or self.sent_at + timedelta(days=1),
            created_by=self.staff,
        )

    def test_decisive_subset_match_uses_items_quantities_prices_time_and_selected_attachment(self):
        before_quote = (self.quote.outcome_status, self.quote.outcome_last_updated_at)
        before_lines = list(
            self.quote.lines.values_list("id", "outcome_status", "accepted_quantity", "accepted_total")
        )
        message = self.message(
            "decisive",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            body="Local Purchase Order\nNitrile Gloves Blue Size M Box 100\nGrand Total: AED 100.00",
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(quotation=self.quote, gmail_message_id="decisive")
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(match_run.summary["decisive_messages"], 1)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(evidence.mailbox_message, message)
        self.assertEqual(evidence.mailbox_match_run, match_run)
        self.assertEqual(evidence.selected_attachment_id, "1")
        self.assertEqual(evidence.selected_attachment_filename, "LPO-decisive.pdf")
        self.assertTrue(evidence.attachments[0]["is_selected"])
        self.assertEqual(evidence.match_signals["candidate"]["item_coverage"], 1.0)
        self.assertEqual(evidence.match_signals["candidate"]["quote_coverage"], 0.5)
        self.quote.refresh_from_db()
        self.assertEqual((self.quote.outcome_status, self.quote.outcome_last_updated_at), before_quote)
        self.assertEqual(
            list(self.quote.lines.values_list("id", "outcome_status", "accepted_quantity", "accepted_total")),
            before_lines,
        )
        self.assertEqual(QuotationLPO.objects.count(), 0)
        self.assertEqual(ProformaInvoice.objects.count(), 0)

    def test_ariba_body_uses_displayed_line_sections_instead_of_thousands_of_layout_rows(self):
        def ariba_section(index):
            line_number = index * 10
            quantity = Decimal(index)
            unit_price = Decimal("4.00")
            return "\n".join(
                [
                    "Line #",
                    "No. Schedule Lines",
                    "Part # / Description",
                    "Customer Part #",
                    "Type",
                    "Return",
                    "Qty (Unit)",
                    "Need By",
                    "Unit Price",
                    "Subtotal",
                    "Tax",
                    str(line_number),
                    "1",
                    f"GMEDIC-{index:03d}",
                    "Material",
                    f"{quantity:.3f}",
                    "(EA)",
                    "2 Aug 2026",
                    f"{unit_price:.2f} AED",
                    f"{quantity * unit_price:.2f} AED",
                    f"Medical Supply Item {index:02d}",
                    "Control Keys",
                    "Order Confirmation:",
                    "not allowed",
                ]
            )

        body = "\n".join(
            [
                "SAP Business Network",
                "Al Futtaim sent a new order",
                "Purchase Order",
                "4518320480",
                *(ariba_section(index) for index in range(1, 26)),
                f"Quote No: {self.quote.quotation_number}",
                "Est. Grand Total:",
                "1,300.00",
                "AED",
            ]
        )
        inventory = self.message(
            "ariba-25-lines",
            subject="Al Futtaim sent a new Purchase Order 4518320480",
            body=body,
            attachment=False,
        )

        variants = document_variants(inventory)

        self.assertEqual(len(variants), 1)
        parsed = variants[0].message
        self.assertEqual(len(parsed.parsed_rows), 25)
        self.assertEqual(parsed.parsed_rows[0].name, "Medical Supply Item 01")
        self.assertEqual(parsed.parsed_rows[0].quantity, Decimal("1.000"))
        self.assertEqual(parsed.parsed_rows[0].unit_price, Decimal("4.00"))
        self.assertEqual(parsed.parsed_rows[0].line_total, Decimal("4.00"))
        self.assertEqual(parsed.parsed_rows[-1].line_total, Decimal("100.00"))
        self.assertEqual(parsed.document_total, Decimal("1300.00"))
        self.assertEqual(parsed.quotation_references, (self.quote.quotation_number,))

    @patch("quotations.mailbox_po_reconciliation.parse_text_preview")
    def test_recognized_but_unparsed_portal_order_never_falls_back_to_generic_rows(
        self,
        parse_text_preview,
    ):
        body = "\n".join(
            [
                "SAP Business Network",
                "Purchase Order",
                "4518320480",
                "Line #",
                "Malformed Item | 5 | 10.00 | 50.00",
            ]
        )
        inventory = self.message(
            "ariba-malformed",
            subject="SAP purchase order",
            body=body,
            attachment=False,
        )

        parsed = document_variants(inventory)[0].message

        parse_text_preview.assert_not_called()
        self.assertEqual(parsed.parsed_rows, ())
        self.assertTrue(
            any("parsed 0 of 1" in warning for warning in parsed.parser_warnings),
            parsed.parser_warnings,
        )

    def test_imdaad_partial_table_retains_safe_rows_and_adds_material_warning(self):
        body = "\n".join(
            [
                "Purchase Order",
                "PO No: PO26IMD32175",
                "IMDAAD Contact Name",
                "Buyer",
                "Description",
                "UOM",
                "Qty",
                "Unit Price",
                "Discount",
                "Net Amount",
                "VAT Amount",
                "TOTAL",
                "AMOUNT",
                "1",
                "Surgical Scissors",
                "NOS",
                "1",
                "25.00",
                "0.00",
                "25.00",
                "1.25",
                "26.25",
                "2",
                "Calamine Lotion",
                "NOS",
                "5",
            ]
        )
        inventory = self.message(
            "imdaad-partial",
            subject="Purchase Order PO26IMD32175",
            body=body,
            attachment=False,
        )

        parsed = document_variants(inventory)[0].message

        self.assertEqual(len(parsed.parsed_rows), 1)
        self.assertEqual(parsed.parsed_rows[0].name, "Surgical Scissors")
        self.assertTrue(
            any("incomplete" in warning.lower() for warning in parsed.parser_warnings),
            parsed.parser_warnings,
        )
        self.assertTrue(parsed.material_warnings)

    def test_emrill_repacked_po_parses_commercial_rows_and_remains_reviewable(self):
        def emrill_row(index, name, quantity, price):
            amount = Decimal(str(quantity)) * Decimal(str(price))
            return "\n".join(
                [
                    str(index),
                    f"10115{index:03d}",
                    "5637144598",
                    "3102006",
                    "Variable works",
                    "EA",
                    f"{Decimal(str(quantity)):.2f}",
                    f"{Decimal(str(price)):.2f}",
                    "0.00",
                    "5.00%",
                    "0.50",
                    f"{amount:.2f} 17/06/2026",
                    f"Description: {name}",
                    "WareHouse : 089",
                ]
            )

        text = "\n".join(
            [
                "Emrill Services LLC",
                "Purchase Order",
                "PO183619-1",
                "Line number",
                "Item number",
                "Purchase Requisition",
                "ProjectID",
                "Project Name",
                "Unit",
                "Quantity",
                "Unit price",
                "Discount",
                "VAT %",
                "VAT Amount",
                "Amount Delivery",
                emrill_row(1, self.line_1.item_name_snapshot, 10, 9),
                emrill_row(2, self.line_2.item_name_snapshot, 5, 9),
                "Gross total",
                "135.00",
            ]
        )
        inventory = self.message("emrill-repacked", body="Please proceed")
        manifest = inventory.attachment_manifest
        manifest[0].update(
            {
                "filename": "Purchase order PO183619.pdf",
                "original_text": text,
                "lines": [
                    {"raw_name": "Warehouse", "quantity": "89"},
                    {"raw_name": "Purchase Requisition"},
                ],
                "line_count": 2,
            }
        )
        inventory.attachment_manifest = manifest
        inventory.save(update_fields=["attachment_manifest", "updated_at"])

        attachment = next(
            variant for variant in document_variants(inventory) if variant.source_kind == "attachment"
        )
        result = rank_message_to_quotations(attachment.message, eligible_quotations())

        self.assertEqual(len(attachment.message.parsed_rows), 2)
        self.assertEqual(
            [row.name for row in attachment.message.parsed_rows],
            [self.line_1.item_name_snapshot, self.line_2.item_name_snapshot],
        )
        self.assertEqual(attachment.message.parsed_rows[0].quantity, Decimal("10.00"))
        self.assertEqual(attachment.message.parsed_rows[0].unit_price, Decimal("9.00"))
        self.assertEqual(attachment.message.parsed_rows[0].line_total, Decimal("90.00"))
        self.assertEqual(result.status, AMBIGUOUS, result.reason)
        self.assertEqual(result.candidates[0].item_coverage, 1.0)

    def test_mirrored_rows_do_not_hide_body_only_extra_quotation_reference(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0002",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.staff,
        )
        body = (
            f"Purchase Order for {self.quote.quotation_number} and {other.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        inventory = self.message(
            "body-extra-quote-ref",
            body=body,
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = inventory.attachment_manifest
        manifest[0]["original_text"] = (
            f"PURCHASE ORDER\nQuotation No: {self.quote.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        inventory.attachment_manifest = manifest

        variants = document_variants(inventory)

        self.assertEqual(
            [variant.source_kind for variant in variants],
            ["attachment", "email_body"],
        )
        self.assertEqual(
            variants[0].quotation_references,
            (self.quote.quotation_number,),
        )
        self.assertEqual(
            set(variants[1].quotation_references),
            {self.quote.quotation_number, other.quotation_number},
        )

    def test_body_order_confirmation_is_not_hidden_by_attached_original_quotation(self):
        body = (
            f"Please proceed with PO No: PO-123 for {self.quote.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        inventory = self.message(
            "body-order-with-original-quote",
            subject="Order confirmation",
            body=body,
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = inventory.attachment_manifest
        manifest[0]["filename"] = "original-quotation.pdf"
        manifest[0]["original_text"] = (
            f"SUPPLIER QUOTATION\nQuotation No: {self.quote.quotation_number}\n"
            "Item | Qty | Unit Price | Total\n"
            "Nitrile Gloves Blue Size M Box 100 | 10 | 10 | 100\n"
            "Grand Total: 100"
        )
        inventory.attachment_manifest = manifest
        inventory.save(update_fields=["attachment_manifest", "updated_at"])

        variants = document_variants(inventory)

        self.assertEqual(
            [variant.source_kind for variant in variants],
            ["attachment", "email_body"],
        )
        attachment_result = rank_message_to_quotations(
            variants[0].message,
            eligible_quotations(),
        )
        body_result = rank_message_to_quotations(
            variants[1].message,
            eligible_quotations(),
        )
        self.assertEqual(attachment_result.status, UNMATCHED)
        self.assertEqual(body_result.status, AUTOMATIC)
        self.assertEqual(body_result.automatic_winner.quote_id, self.quote.id)

    def test_long_gmail_attachment_identity_is_stored_losslessly(self):
        long_attachment_id = "ANGjdJ_" + ("a" * 395) + "terminal-token"
        message = self.message(
            "long-attachment-identity",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = message.attachment_manifest
        manifest[0]["attachment_id"] = long_attachment_id
        manifest[0]["part_id"] = ""
        message.attachment_manifest = manifest
        message.save(update_fields=["attachment_manifest", "updated_at"])

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(
            quotation=self.quote,
            gmail_message_id="long-attachment-identity",
        )
        self.assertEqual(evidence.selected_attachment_id, long_attachment_id)
        evidence.full_clean()

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)
        self.assertEqual(
            QuotationPOEvidence.objects.filter(
                quotation=self.quote,
                gmail_message_id="long-attachment-identity",
            ).count(),
            1,
        )

    def test_stable_part_id_is_preferred_over_a_long_gmail_download_token(self):
        long_attachment_id = "ANGjdJ_" + ("b" * 420)
        message = self.message(
            "stable-part-identity",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = message.attachment_manifest
        manifest[0]["attachment_id"] = long_attachment_id
        manifest[0]["part_id"] = "2.1"
        message.attachment_manifest = manifest
        message.save(update_fields=["attachment_manifest", "updated_at"])

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(
            quotation=self.quote,
            gmail_message_id="stable-part-identity",
        )
        self.assertEqual(evidence.selected_attachment_id, "2.1")
        self.assertTrue(evidence.attachments[0]["is_selected"])

    def test_pre_upgrade_token_evidence_is_upgraded_without_duplication(self):
        old_token = "legacy-gmail-download-token"
        message = self.message(
            "pre-upgrade-identity",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = message.attachment_manifest
        manifest[0]["attachment_id"] = old_token
        manifest[0]["part_id"] = "3.2"
        manifest[0]["source_sha256"] = ""
        message.attachment_manifest = manifest
        message.save(update_fields=["attachment_manifest", "updated_at"])
        reviewed_at = timezone.now()
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=message.gmail_message_id,
            selected_attachment_id=old_token,
            selected_attachment_filename=manifest[0]["filename"],
            source_key=f"attachment:{old_token}",
            attachments=[{**manifest[0], "is_selected": True}],
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_by=self.staff,
            link_approved_at=reviewed_at,
            created_by=self.staff,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence.refresh_from_db()
        self.assertEqual(
            QuotationPOEvidence.objects.filter(
                quotation=self.quote,
                gmail_message_id=message.gmail_message_id,
            ).count(),
            1,
        )
        self.assertEqual(evidence.selected_attachment_id, "3.2")
        self.assertEqual(evidence.source_key, "attachment:3.2")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(evidence.link_approved_at, reviewed_at)
        self.assertEqual(evidence.link_approved_by, self.staff)

    def test_reviewed_legacy_identity_wins_over_unreviewed_part_id_collision(self):
        old_token = "reviewed-legacy-token"
        message = self.message(
            "reviewed-identity-collision",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = message.attachment_manifest
        manifest[0]["attachment_id"] = old_token
        manifest[0]["part_id"] = "5.4"
        manifest[0]["source_sha256"] = ""
        message.attachment_manifest = manifest
        message.save(update_fields=["attachment_manifest", "updated_at"])
        unreviewed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=message.gmail_message_id,
            selected_attachment_id="5.4",
            source_key="attachment:5.4",
            attachments=[{**manifest[0], "is_selected": True}],
            status=QuotationPOEvidence.STATUS_CANDIDATE,
            created_by=self.staff,
        )
        dismissed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=message.gmail_message_id,
            selected_attachment_id=old_token,
            source_key=f"attachment:{old_token}",
            attachments=[{**manifest[0], "is_selected": True}],
            status=QuotationPOEvidence.STATUS_NOT_RELEVANT,
            created_by=self.staff,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        unreviewed.refresh_from_db()
        dismissed.refresh_from_db()
        self.assertEqual(unreviewed.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertTrue(unreviewed.source_key.startswith(f"superseded:{unreviewed.pk}:"))
        self.assertEqual(dismissed.status, QuotationPOEvidence.STATUS_NOT_RELEVANT)
        self.assertEqual(dismissed.selected_attachment_id, "5.4")
        self.assertEqual(dismissed.source_key, "attachment:5.4")
        self.assertFalse(
            QuotationPOEvidence.objects.filter(
                quotation=self.quote,
                gmail_message_id=message.gmail_message_id,
                status__in=[
                    QuotationPOEvidence.STATUS_CANDIDATE,
                    QuotationPOEvidence.STATUS_AMBIGUOUS,
                ],
            ).exists()
        )

    def test_approved_legacy_identity_vacates_lower_priority_reviewed_collision(self):
        old_token = "approved-legacy-token"
        message = self.message(
            "mixed-reviewed-identity-collision",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )
        manifest = message.attachment_manifest
        manifest[0]["attachment_id"] = old_token
        manifest[0]["part_id"] = "6.3"
        manifest[0]["source_sha256"] = ""
        message.attachment_manifest = manifest
        message.save(update_fields=["attachment_manifest", "updated_at"])
        dismissed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=message.gmail_message_id,
            selected_attachment_id="6.3",
            source_key="attachment:6.3",
            attachments=[{**manifest[0], "is_selected": True}],
            status=QuotationPOEvidence.STATUS_NOT_RELEVANT,
            created_by=self.staff,
        )
        approved = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=message.gmail_message_id,
            selected_attachment_id=old_token,
            source_key=f"attachment:{old_token}",
            attachments=[{**manifest[0], "is_selected": True}],
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_by=self.staff,
            link_approved_at=timezone.now(),
            created_by=self.staff,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        dismissed.refresh_from_db()
        approved.refresh_from_db()
        self.assertEqual(dismissed.status, QuotationPOEvidence.STATUS_NOT_RELEVANT)
        self.assertTrue(dismissed.source_key.startswith(f"superseded:{dismissed.pk}:"))
        self.assertEqual(approved.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(approved.selected_attachment_id, "6.3")
        self.assertEqual(approved.source_key, "attachment:6.3")

    def test_long_attachment_source_keys_hash_the_complete_identity(self):
        shared_prefix = "ANGjdJ_" + ("x" * 400)
        first = QuotationPOEvidence.build_source_key(
            selected_attachment_id=f"{shared_prefix}-first"
        )
        second = QuotationPOEvidence.build_source_key(
            selected_attachment_id=f"{shared_prefix}-second"
        )

        self.assertTrue(first.startswith("attachment-sha256:"))
        self.assertLessEqual(len(first), 255)
        self.assertNotEqual(first, second)
        self.assertEqual(
            QuotationPOEvidence.build_source_key(selected_attachment_id="short-id"),
            "attachment:short-id",
        )

    def test_semantic_dedupe_collapses_resends_of_one_po_on_one_quote(self):
        weaker = self.evidence(
            self.quote,
            "po-resend-weak",
            "PO No. 111_123579",
            score=72,
            source_sha256="a" * 64,
        )
        stronger = self.evidence(
            self.quote,
            "po-resend-strong",
            "PO111-123579",
            score=91,
            source_sha256="a" * 64,
        )

        kept, superseded = _dedupe_and_cap(
            {weaker.id, stronger.id},
            max_per_quote=3,
        )

        weaker.refresh_from_db()
        stronger.refresh_from_db()
        self.assertEqual(kept, {stronger.id})
        self.assertEqual(superseded, [weaker.id])
        self.assertEqual(weaker.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn("same source bytes", weaker.error)
        self.assertEqual(stronger.status, QuotationPOEvidence.STATUS_CANDIDATE)

    def test_same_po_across_quotes_has_one_clear_canonical_owner(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0002",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.staff,
        )
        weaker = self.evidence(
            other,
            "cross-quote-weak",
            "LPO-25-0113",
            score=88,
            exact_reference=False,
        )
        exact = self.evidence(
            self.quote,
            "cross-quote-exact",
            "LPO 25/0113",
            score=84,
            exact_reference=True,
        )

        kept, superseded = _dedupe_and_cap(
            {weaker.id, exact.id},
            max_per_quote=3,
        )

        weaker.refresh_from_db()
        self.assertEqual(kept, {exact.id})
        self.assertEqual(superseded, [weaker.id])
        self.assertEqual(weaker.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertIn("exact-reference", weaker.error)

    def test_supplier_code_below_po_heading_does_not_merge_distinct_orders(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0002",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.staff,
        )

        def attachment_reference(message_id, order_number):
            inventory = self.message(
                message_id,
                rows=[
                    {
                        "raw_name": "Nitrile Gloves Blue Size M Box 100",
                        "quantity": "10",
                        "unit_price": "10",
                        "line_total": "100",
                    }
                ],
                body="Please proceed",
            )
            manifest = inventory.attachment_manifest
            manifest[0]["filename"] = f"{order_number.replace('/', '-')}.pdf"
            manifest[0]["original_text"] = (
                "Purchase Order\n"
                "ALAM004\n"
                "Al Ameen Pharmacy LLC\n"
                "Order No. :\n"
                f"{order_number}\n"
            )
            inventory.attachment_manifest = manifest
            inventory.save(update_fields=["attachment_manifest", "updated_at"])
            attachment = next(
                variant
                for variant in document_variants(inventory)
                if variant.source_kind == "attachment"
            )
            self.assertEqual(attachment.lpo_references, (order_number,))
            return attachment.lpo_references[0]

        weaker = self.evidence(
            self.quote,
            "supplier-code-order-a",
            attachment_reference("supplier-code-a", "HM-201A26002/0029"),
            score=54,
        )
        stronger = self.evidence(
            other,
            "supplier-code-order-b",
            attachment_reference("supplier-code-b", "HM-201HDVOHP/1153"),
            score=100,
            exact_reference=True,
        )

        kept, superseded = _dedupe_and_cap(
            {weaker.id, stronger.id},
            max_per_quote=3,
        )

        self.assertEqual(kept, {weaker.id, stronger.id})
        self.assertEqual(superseded, [])

    def test_near_tied_cross_quote_po_ownership_stays_visible_for_staff(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0003",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.staff,
        )
        first = self.evidence(
            self.quote,
            "cross-quote-tie-a",
            "PO-50009",
            score=82,
        )
        second = self.evidence(
            other,
            "cross-quote-tie-b",
            "PO 50009",
            score=79,
        )

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=3,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])
        self.assertEqual(first.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(second.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertIn("near-equal", first.error)

    def test_distinct_po_references_on_one_quote_are_not_lost_to_candidate_cap(self):
        first = self.evidence(self.quote, "distinct-po-a", "PO-118923", score=80)
        second = self.evidence(self.quote, "distinct-po-b", "PO-118924", score=79)

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=1,
        )

        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])

    def test_four_distinct_short_lpo_references_are_not_lost_to_candidate_cap(self):
        evidence = [
            self.evidence(
                self.quote,
                f"short-lpo-{reference}",
                f"LPO-{reference}",
                score=90 - reference,
            )
            for reference in range(77, 81)
        ]

        kept, superseded = _dedupe_and_cap(
            {row.id for row in evidence},
            max_per_quote=1,
        )

        self.assertEqual(kept, {row.id for row in evidence})
        self.assertEqual(superseded, [])
        self.assertEqual(
            set(
                QuotationPOEvidence.objects.filter(id__in=kept).values_list(
                    "status", flat=True
                )
            ),
            {QuotationPOEvidence.STATUS_CANDIDATE},
        )

    def test_same_po_number_for_different_customers_is_not_globally_deduped(self):
        other_company = Company.objects.create(
            name="Different Customer LLC",
            email="buyer@different.example",
        )
        other_quote = Quotation.objects.create(
            company=other_company,
            quotation_number="QT-20260710-0004",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at,
            created_by=self.staff,
        )
        first = self.evidence(self.quote, "customer-a-po", "PO-50009", score=90)
        second = self.evidence(other_quote, "customer-b-po", "PO 50009", score=70)

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=3,
        )

        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])

    def test_explicitly_revised_newer_copy_supersedes_older_same_company_po(self):
        older = self.evidence(
            self.quote,
            "amendment-old",
            "PO-70001",
            score=92,
            source_sha256="c" * 64,
            sent_at=self.sent_at + timedelta(days=1),
        )
        revised = self.evidence(
            self.quote,
            "amendment-revised",
            "PO 70001",
            score=80,
            source_sha256="d" * 64,
            subject="Purchase order documents",
            filename="Revised Purchase Order PO-70001.pdf",
            sent_at=self.sent_at + timedelta(days=2),
        )

        kept, superseded = _dedupe_and_cap(
            {older.id, revised.id},
            max_per_quote=3,
        )

        older.refresh_from_db()
        self.assertEqual(kept, {revised.id})
        self.assertEqual(superseded, [older.id])
        self.assertIn("revised/amended", older.error)

    def test_different_hashes_with_same_po_stay_ambiguous_without_revision_proof(self):
        first = self.evidence(
            self.quote,
            "possible-amendment-a",
            "PO-70002",
            score=90,
            source_sha256="e" * 64,
        )
        second = self.evidence(
            self.quote,
            "possible-amendment-b",
            "PO 70002",
            score=82,
            source_sha256="f" * 64,
        )

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=3,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])
        self.assertEqual(first.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(second.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    def test_shared_revised_subject_does_not_mark_each_attachment_as_a_revision(self):
        first = self.evidence(
            self.quote,
            "shared-subject-a",
            "PO-70003",
            score=90,
            source_sha256="1" * 64,
            subject="Revised Purchase Order documents",
            filename="PO-70003-a.pdf",
            sent_at=self.sent_at + timedelta(days=1),
        )
        second = self.evidence(
            self.quote,
            "shared-subject-b",
            "PO 70003",
            score=80,
            source_sha256="2" * 64,
            subject="Revised Purchase Order documents",
            filename="PO-70003-b.pdf",
            sent_at=self.sent_at + timedelta(days=2),
        )

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=3,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])
        self.assertEqual(first.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(second.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    def test_indistinguishable_revision_rank_and_time_keeps_both_versions_ambiguous(self):
        shared_time = self.sent_at + timedelta(days=2)
        first = self.evidence(
            self.quote,
            "same-revision-a",
            "PO-70004",
            score=90,
            source_sha256="3" * 64,
            filename="PO-70004 Rev 2.pdf",
            sent_at=shared_time,
        )
        second = self.evidence(
            self.quote,
            "same-revision-b",
            "PO 70004",
            score=80,
            source_sha256="4" * 64,
            filename="PO-70004 Rev 2.pdf",
            sent_at=shared_time,
        )

        kept, superseded = _dedupe_and_cap(
            {first.id, second.id},
            max_per_quote=3,
        )

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(kept, {first.id, second.id})
        self.assertEqual(superseded, [])
        self.assertEqual(first.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertEqual(second.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    def test_semantic_dedupe_never_rewrites_reviewed_parsed_or_not_relevant_rows(self):
        parsed = self.evidence(
            self.quote,
            "reviewed-parsed-po",
            "PO-90001",
            score=70,
            status=QuotationPOEvidence.STATUS_PARSED,
            source_sha256="b" * 64,
        )
        dismissed = self.evidence(
            self.quote,
            "reviewed-dismissed-po",
            "PO 90001",
            score=60,
            status=QuotationPOEvidence.STATUS_NOT_RELEVANT,
            source_sha256="b" * 64,
        )
        unreviewed = self.evidence(
            self.quote,
            "unreviewed-duplicate-po",
            "PO/90001",
            score=99,
            source_sha256="b" * 64,
        )

        kept, superseded = _dedupe_and_cap(
            {parsed.id, dismissed.id, unreviewed.id},
            max_per_quote=3,
        )

        parsed.refresh_from_db()
        dismissed.refresh_from_db()
        unreviewed.refresh_from_db()
        self.assertEqual(kept, {parsed.id, dismissed.id})
        self.assertEqual(superseded, [unreviewed.id])
        self.assertEqual(parsed.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(dismissed.status, QuotationPOEvidence.STATUS_NOT_RELEVANT)
        self.assertEqual(unreviewed.status, QuotationPOEvidence.STATUS_SUPERSEDED)

    def test_stale_supersede_rechecks_staff_review_predicates_at_update_time(self):
        parsed_after_selection = self.evidence(
            self.quote,
            "stale-became-parsed",
            "PO-91001",
            score=70,
        )
        approved_after_selection = self.evidence(
            self.quote,
            "stale-became-approved",
            "PO-91002",
            score=70,
        )
        still_stale = self.evidence(
            self.quote,
            "stale-unchanged",
            "PO-91003",
            score=70,
        )
        stale_ids_selected_before_review = [
            parsed_after_selection.id,
            approved_after_selection.id,
            still_stale.id,
        ]
        QuotationPOEvidence.objects.filter(id=parsed_after_selection.id).update(
            status=QuotationPOEvidence.STATUS_PARSED,
        )
        QuotationPOEvidence.objects.filter(id=approved_after_selection.id).update(
            link_approved_at=timezone.now(),
            link_approved_by=self.staff,
        )

        updated = _supersede_stale_evidence(stale_ids_selected_before_review)

        parsed_after_selection.refresh_from_db()
        approved_after_selection.refresh_from_db()
        still_stale.refresh_from_db()
        self.assertEqual(updated, 1)
        self.assertEqual(parsed_after_selection.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(approved_after_selection.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertIsNotNone(approved_after_selection.link_approved_at)
        self.assertEqual(still_stale.status, QuotationPOEvidence.STATUS_SUPERSEDED)

    def test_missing_quantity_or_commercial_value_stays_ambiguous_even_with_exact_quote_reference(self):
        self.message(
            "missing-commercial",
            subject=f"LPO for {self.quote.quotation_number}",
            body=f"Purchase Order for {self.quote.quotation_number}",
            rows=[{"raw_name": "Nitrile Gloves Blue Size M Box 100"}],
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="missing-commercial")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertTrue(evidence.quote_reference_present)

    def test_explicit_reference_to_another_quote_blocks_item_based_link_to_this_quote(self):
        other = Quotation.objects.create(
            company=self.company,
            quotation_number="QT-20260710-0002",
            status=Quotation.STATUS_SENT,
            sent_at=self.sent_at + timedelta(hours=1),
            created_by=self.staff,
        )
        self.line(other, "Surgical Mask 3 Ply", 10, 2, 0)
        self.message(
            "wrong-ref",
            subject=f"Purchase Order for {other.quotation_number}",
            body=f"LPO for quotation {other.quotation_number}",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        self.assertFalse(
            QuotationPOEvidence.objects.filter(quotation=self.quote, gmail_message_id="wrong-ref").exists()
        )
        other_evidence = QuotationPOEvidence.objects.get(quotation=other, gmail_message_id="wrong-ref")
        self.assertEqual(other_evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)

    def test_spam_message_is_inventory_complete_but_never_decisive(self):
        self.message(
            "spam-match",
            labels=["SPAM"],
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence = QuotationPOEvidence.objects.get(gmail_message_id="spam-match")
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_AMBIGUOUS)
        self.assertIn("Spam or Trash", evidence.error)
        self.assertEqual(match_run.summary["spam_or_trash_messages"], 1)

    def test_complete_reconciliation_supersedes_old_unreviewed_noise_but_preserves_parsed(self):
        irrelevant = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="old-noise",
            mailbox_email=self.connection.email,
            subject="Invoice reminder",
            sender="buyer@acme.example",
            sent_at=self.sent_at + timedelta(hours=2),
            classification=MailboxPOMessage.CLASS_OTHER,
            is_relevant=False,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )
        old = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="old-noise",
            subject="Old broad candidate",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )
        parsed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id="approved-source",
            subject="Approved source",
            status=QuotationPOEvidence.STATUS_PARSED,
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        old.refresh_from_db()
        parsed.refresh_from_db()
        self.assertEqual(old.mailbox_message, irrelevant)
        self.assertEqual(old.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertEqual(parsed.status, QuotationPOEvidence.STATUS_PARSED)

    def test_oauth_rotation_reconciles_same_mailbox_stale_evidence_and_preserves_review(self):
        previous_owner = User.objects.create_user("previous-mailbox-owner", is_staff=True)
        previous_connection = GmailOAuthConnection.objects.create(
            user=previous_owner,
            email=self.connection.email,
            status=GmailOAuthConnection.STATUS_DISCONNECTED,
        )
        previous_run = MailboxPOAuditRun.objects.create(
            gmail_connection=previous_connection,
            requested_by=previous_owner,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

        def inventory(connection, run, message_id):
            return MailboxPOMessage.objects.create(
                gmail_connection=connection,
                gmail_message_id=message_id,
                mailbox_email=connection.email,
                subject="Old broad candidate",
                sender="buyer@acme.example",
                sent_at=self.sent_at + timedelta(hours=2),
                classification=MailboxPOMessage.CLASS_OTHER,
                is_relevant=False,
                first_seen_run=run,
                last_seen_run=run,
            )

        stale_id = "rotated-stale-candidate"
        reviewed_id = "rotated-reviewed-candidate"
        old_stale_inventory = inventory(previous_connection, previous_run, stale_id)
        old_reviewed_inventory = inventory(previous_connection, previous_run, reviewed_id)
        current_stale_inventory = inventory(self.connection, self.run, stale_id)
        current_reviewed_inventory = inventory(self.connection, self.run, reviewed_id)
        stale = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            mailbox_message=old_stale_inventory,
            gmail_connection=previous_connection,
            gmail_message_id=stale_id,
            subject="Unreviewed source from previous OAuth connection",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
        )
        reviewed_at = timezone.now()
        reviewed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            mailbox_message=old_reviewed_inventory,
            gmail_connection=previous_connection,
            gmail_message_id=reviewed_id,
            subject="Reviewed source from previous OAuth connection",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
            link_approved_by=self.staff,
            link_approved_at=reviewed_at,
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        stale.refresh_from_db()
        reviewed.refresh_from_db()
        self.assertEqual(match_run.summary["existing_evidence_linked"], 2)
        self.assertEqual(stale.mailbox_message, current_stale_inventory)
        self.assertEqual(stale.status, QuotationPOEvidence.STATUS_SUPERSEDED)
        self.assertEqual(reviewed.mailbox_message, current_reviewed_inventory)
        self.assertEqual(reviewed.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertEqual(reviewed.link_approved_at, reviewed_at)

    def test_oauth_rotation_reuses_reviewed_source_for_a_fresh_decisive_proposal(self):
        previous_owner = User.objects.create_user("previous-reviewed-owner", is_staff=True)
        previous_connection = GmailOAuthConnection.objects.create(
            user=previous_owner,
            email=self.connection.email,
            status=GmailOAuthConnection.STATUS_DISCONNECTED,
        )
        previous_run = MailboxPOAuditRun.objects.create(
            gmail_connection=previous_connection,
            requested_by=previous_owner,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )
        message_id = "rotated-reviewed-decisive"
        current_inventory = self.message(
            message_id,
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
            body=(
                "LOCAL PURCHASE ORDER\nPO No: PO-7788\n"
                "Nitrile Gloves Blue Size M Box 100\nGrand Total: 100"
            ),
        )
        selected = current_inventory.attachment_manifest[0]
        previous_inventory = MailboxPOMessage.objects.create(
            gmail_connection=previous_connection,
            gmail_message_id=message_id,
            gmail_thread_id=f"thread-{message_id}",
            mailbox_email=previous_connection.email,
            subject=current_inventory.subject,
            sender=current_inventory.sender,
            recipients=previous_connection.email,
            sent_at=current_inventory.sent_at,
            newest_body_text=current_inventory.newest_body_text,
            attachment_manifest=current_inventory.attachment_manifest,
            classification=MailboxPOMessage.CLASS_PURCHASE_ORDER,
            is_relevant=True,
            first_seen_run=previous_run,
            last_seen_run=previous_run,
        )
        reviewed_at = timezone.now()
        reviewed = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            mailbox_message=previous_inventory,
            gmail_connection=previous_connection,
            mailbox_email=previous_connection.email,
            gmail_message_id=message_id,
            selected_attachment_id="1",
            selected_attachment_filename=selected["filename"],
            source_sha256=selected["source_sha256"],
            source_key=QuotationPOEvidence.build_source_key(
                source_sha256=selected["source_sha256"]
            ),
            attachments=[{**selected, "is_selected": True}],
            status=QuotationPOEvidence.STATUS_PARSED,
            link_approved_by=self.staff,
            link_approved_at=reviewed_at,
            created_by=self.staff,
        )

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        reviewed.refresh_from_db()
        self.assertEqual(
            QuotationPOEvidence.objects.filter(gmail_message_id=message_id).count(),
            1,
        )
        self.assertEqual(reviewed.gmail_connection, previous_connection)
        self.assertEqual(reviewed.mailbox_message, current_inventory)
        self.assertEqual(reviewed.mailbox_match_run, match_run)
        self.assertEqual(reviewed.status, QuotationPOEvidence.STATUS_PARSED)
        self.assertEqual(reviewed.link_approved_at, reviewed_at)
        self.assertEqual(match_run.summary["evidence_created"], 0)

    def test_stale_sweep_never_supersedes_a_manually_approved_link(self):
        inventory = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id="approved-but-not-parsed",
            mailbox_email=self.connection.email,
            subject="Reviewed source",
            sender="buyer@acme.example",
            sent_at=self.sent_at + timedelta(hours=2),
            classification=MailboxPOMessage.CLASS_OTHER,
            is_relevant=False,
            first_seen_run=self.run,
            last_seen_run=self.run,
        )
        evidence = QuotationPOEvidence.objects.create(
            quotation=self.quote,
            gmail_connection=self.connection,
            gmail_message_id=inventory.gmail_message_id,
            subject="Staff-approved source",
            status=QuotationPOEvidence.STATUS_CANDIDATE,
            link_approved_by=self.staff,
            link_approved_at=timezone.now(),
        )

        reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        evidence.refresh_from_db()
        self.assertEqual(evidence.mailbox_message, inventory)
        self.assertEqual(evidence.status, QuotationPOEvidence.STATUS_CANDIDATE)
        self.assertIsNotNone(evidence.link_approved_at)

    def test_repeated_reconciliation_is_idempotent_for_evidence_and_outcomes(self):
        self.message(
            "repeat",
            rows=[
                {
                    "raw_name": "Nitrile Gloves Blue Size M Box 100",
                    "quantity": "10",
                    "unit_price": "10",
                    "line_total": "100",
                }
            ],
        )

        first = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)
        second = reconcile_mailbox_po_audit(self.run, requested_by=self.staff)

        self.assertEqual(QuotationPOEvidence.objects.filter(gmail_message_id="repeat").count(), 1)
        evidence = QuotationPOEvidence.objects.get(gmail_message_id="repeat")
        self.assertEqual(evidence.mailbox_match_run, second)
        self.assertNotEqual(first, second)
        self.assertEqual(self.quote.lines.filter(outcome_status=QuotationLine.OUTCOME_PENDING).count(), 2)


class MailboxPOAuditAPITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("audit-api", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="orders@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        company = Company.objects.create(name="API Audit Company", email="buyer@example.test")
        self.quote = Quotation.objects.create(
            company=company,
            status=Quotation.STATUS_SENT,
            sent_at=timezone.now() - timedelta(hours=2),
            created_by=self.staff,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.staff)

    def test_start_endpoint_creates_then_resumes_one_incomplete_run(self):
        first = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")
        second = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")

        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["run"]["id"], second.data["run"]["id"])
        self.assertFalse(first.data["inventory_done"])

    @patch("quotations.views.scan_mailbox_po_audit_page")
    def test_scan_page_is_bounded_for_web_requests(self, scan_page):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
        )
        scan_page.return_value = run

        response = self.client.post(
            reverse("quotation-mailbox-po-audit-scan-page", args=[run.id]),
            {"page_size": 999},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        scan_page.assert_called_once_with(run, page_size=25)

    def test_completed_inventory_can_be_reconciled_and_latest_reports_done(self):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )

        response = self.client.post(
            reverse("quotation-mailbox-po-audit-reconcile", args=[run.id]),
            {},
            format="json",
        )
        latest = self.client.get(reverse("quotation-mailbox-po-audit-latest"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["done"])
        self.assertEqual(response.data["match_run"]["status"], MailboxPOMatchRun.STATUS_COMPLETED)
        self.assertEqual(latest.data["run"]["id"], run.id)
        self.assertTrue(latest.data["done"])

    @patch("quotations.views.mailbox_po_audit_repair_remaining", return_value=4)
    def test_completed_legacy_match_does_not_stop_current_algorithm_rollover(self, repair_remaining):
        self.assertNotEqual(ALGORITHM_VERSION, "mailbox_match_v2")
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 -from:me",
            exhausted=True,
            completed_at=timezone.now(),
        )
        legacy = MailboxPOMatchRun.objects.create(
            audit_run=run,
            requested_by=self.staff,
            algorithm_version="mailbox_match_v2",
            status=MailboxPOMatchRun.STATUS_COMPLETED,
            completed_at=timezone.now(),
        )

        awaiting = self.client.post(
            reverse("quotation-mailbox-po-audit-list"),
            {},
            format="json",
        )

        self.assertEqual(awaiting.status_code, 200)
        self.assertEqual(awaiting.data["run"]["id"], run.id)
        self.assertIsNone(awaiting.data["match_run"])
        self.assertFalse(awaiting.data["done"])
        self.assertEqual(awaiting.data["repair_remaining"], 0)
        repair_remaining.assert_not_called()

        reconciled = self.client.post(
            reverse("quotation-mailbox-po-audit-reconcile", args=[run.id]),
            {},
            format="json",
        )

        self.assertEqual(reconciled.status_code, 200)
        self.assertTrue(reconciled.data["done"])
        self.assertEqual(reconciled.data["match_run"]["algorithm_version"], ALGORITHM_VERSION)
        self.assertNotEqual(reconciled.data["match_run"]["id"], legacy.id)

    def test_latest_reports_exhausted_but_incomplete_tombstone_inventory(self):
        run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.staff,
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            earliest_quote_at=self.quote.created_at,
            gmail_query="in:anywhere after:1 before:2 -from:me",
            exhausted=True,
            incomplete_messages=2,
            completed_at=timezone.now(),
        )

        latest = self.client.get(reverse("quotation-mailbox-po-audit-latest"))

        self.assertEqual(latest.data["run"]["id"], run.id)
        self.assertEqual(latest.data["run"]["incomplete_messages"], 2)
        self.assertTrue(latest.data["inventory_done"])
        self.assertFalse(latest.data["inventory_complete"])

    def test_anonymous_user_cannot_start_a_mailbox_audit(self):
        self.client.force_authenticate(user=None)

        response = self.client.post(reverse("quotation-mailbox-po-audit-list"), {}, format="json")

        self.assertIn(response.status_code, {401, 403})
