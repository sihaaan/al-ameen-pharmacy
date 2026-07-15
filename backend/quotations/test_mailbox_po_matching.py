from datetime import datetime, timedelta, timezone
from dataclasses import replace
from decimal import Decimal
from unittest import TestCase

from .mailbox_po_matching import (
    AMBIGUOUS,
    AUTOMATIC,
    UNMATCHED,
    CanonicalMailboxMessage,
    EligibleQuotation,
    EligibleQuoteLine,
    MailboxPOLine,
    canonicalize_message,
    rank_message_to_quotations,
)


D = Decimal
BASE = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)


def quote(
    quote_id,
    number,
    lines,
    *,
    sent_at=BASE,
    company="Acme Medical",
    emails=("buyer@acme.example",),
):
    return EligibleQuotation(
        quote_id=quote_id,
        quotation_number=number,
        sent_at=sent_at,
        company_name=company,
        customer_emails=emails,
        lines=tuple(lines),
    )


def qline(line_id, name, quantity, price, *, description="", unit="PCS"):
    quantity = D(str(quantity)) if quantity is not None else None
    price = D(str(price)) if price is not None else None
    return EligibleQuoteLine(
        line_id=line_id,
        name=name,
        description=description,
        quantity=quantity,
        unit_price=price,
        line_total=quantity * price if quantity is not None and price is not None else None,
        unit=unit,
    )


def pline(line_id, name, quantity, price, *, description="", unit="PCS", total=True):
    quantity = D(str(quantity)) if quantity is not None else None
    price = D(str(price)) if price is not None else None
    return MailboxPOLine(
        line_id=line_id,
        name=name,
        description=description,
        quantity=quantity,
        unit_price=price,
        line_total=(quantity * price if total and quantity is not None and price is not None else None),
        unit=unit,
    )


def message(
    rows,
    *,
    received_at=BASE + timedelta(days=4),
    refs=(),
    total=None,
    subject="LPO attached",
    parser_warnings=(),
    material_warnings=(),
):
    return CanonicalMailboxMessage(
        message_id="gmail-1",
        sender="Acme Buyer <buyer@acme.example>",
        recipients=("orders@pharmacy.example",),
        subject=subject,
        body="Please proceed with the attached local purchase order.",
        received_at=received_at,
        parsed_rows=tuple(rows),
        lpo_references=("LPO-1001",),
        quotation_references=tuple(refs),
        company_name="Acme Medical",
        document_total=D(str(total)) if total is not None else None,
        parser_warnings=tuple(parser_warnings),
        material_warnings=tuple(material_warnings),
    )


class MailboxQuotationRankingTests(TestCase):
    def test_exact_quote_reference_is_strongest_and_rejects_other_quotes(self):
        first = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        second = quote(2, "QT-20260701-0002", [qline(21, "Nitrile Gloves", 10, 5)])

        result = rank_message_to_quotations(
            message([pline(1, "Nitrile Glove", 10, 5)], refs=(first.quotation_number,), total=50),
            [first, second],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, first.quote_id)
        self.assertEqual([candidate.quote_id for candidate in result.candidates], [first.quote_id])
        self.assertEqual(result.rejected_count, 1)
        self.assertTrue(result.candidates[0].exact_quote_reference)

    def test_same_customer_quotes_are_resolved_by_item_overlap(self):
        gloves = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        sanitizer = quote(2, "QT-20260701-0002", [qline(21, "Hand Sanitizer Pouch", 6, 47.53)])

        result = rank_message_to_quotations(
            message([pline(1, "Hand Sanitizer Pouch", 6, 47.53)], total="285.18"),
            [gloves, sanitizer],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, sanitizer.quote_id)
        self.assertEqual(result.automatic_winner.item_coverage, 1.0)

    def test_po_may_remove_quote_items_without_a_missing_item_penalty(self):
        quotation = quote(
            1,
            "QT-20260701-0001",
            [
                qline(11, "Nitrile Gloves", 10, 5),
                qline(12, "Hand Sanitizer 500 ml", 4, 20),
                qline(13, "First Aid Box", 2, 30),
            ],
        )

        result = rank_message_to_quotations(
            message(
                [
                    pline(1, "Nitrile Gloves", 10, 5),
                    pline(2, "Hand Sanitizer 500ml", 4, 20),
                ],
                total=130,
            ),
            [quotation],
        )

        self.assertEqual(result.status, AUTOMATIC)
        winner = result.automatic_winner
        self.assertEqual(winner.item_coverage, 1.0)
        self.assertAlmostEqual(winner.quote_coverage, 2 / 3)
        self.assertEqual(len(winner.matched_lines), 2)

    def test_a_reduced_order_quantity_is_safe_but_a_higher_quantity_is_not(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 20, 5)])

        reduced = rank_message_to_quotations(
            message([pline(1, "Nitrile Gloves", 10, 5)], total=50),
            [quotation],
        )
        increased = rank_message_to_quotations(
            message([pline(1, "Nitrile Gloves", 30, 5)], total=150),
            [quotation],
        )

        self.assertEqual(reduced.status, AUTOMATIC)
        self.assertEqual(reduced.automatic_winner.quantity_reduced_count, 1)
        self.assertEqual(increased.status, AMBIGUOUS)
        self.assertEqual(increased.candidates[0].quantity_conflict_count, 1)

    def test_identical_items_are_disambiguated_by_quantity(self):
        ten = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        twenty = quote(2, "QT-20260701-0002", [qline(21, "Nitrile Gloves", 20, 5)])

        result = rank_message_to_quotations(
            message([pline(1, "Nitrile Gloves", 20, 5)], total=100),
            [ten, twenty],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, twenty.quote_id)
        self.assertGreaterEqual(result.ambiguity_margin, 12)
        wrong = next(candidate for candidate in result.candidates if candidate.quote_id == ten.quote_id)
        self.assertEqual(wrong.quantity_conflict_count, 1)

    def test_message_without_lpo_or_order_evidence_is_unmatched(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        ordinary_email = CanonicalMailboxMessage(
            message_id="gmail-ordinary",
            sender="buyer@acme.example",
            subject="Monthly account meeting",
            body="Can we meet next week?",
            received_at=BASE + timedelta(days=2),
        )

        result = rank_message_to_quotations(ordinary_email, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
        self.assertIn("no explicit LPO/PO", result.reason)

    def test_matching_item_price_table_without_order_signal_is_not_an_lpo(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        customer_quotation = CanonicalMailboxMessage(
            message_id="gmail-customer-quotation",
            sender="Acme Buyer <buyer@acme.example>",
            subject="Revised quotation document",
            body="SALES QUOTATION\nPlease review the attached pricing.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "Nitrile Gloves", 10, 5),),
        )

        result = rank_message_to_quotations(customer_quotation, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_strong_non_order_document_types_are_rejected_before_ranking(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        base_message = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
        )
        documents = {
            "supplier quotation": (
                "vendor-quotation.pdf",
                "AL MAQAM MEDICAL\nQUOTATION\nQuotation Date: 02/07/2026\nNitrile Gloves",
            ),
            "supplier quotation with generic filename": (
                "QT-20260701-0001.pdf",
                "AL AMEEN PHARMACY\nQuotation No: QT-20260701-0001\n"
                "Quotation Date: 01/07/2026\nNitrile Gloves",
            ),
            "delivery note": (
                "delivery-note.pdf",
                "DELIVERY NOTE\nPO No: PO-7788\nNitrile Gloves",
            ),
            "delivery order copy without OCR": (
                "DO COPY.pdf",
                "",
            ),
            "goods receipt": (
                "GRN-7788.pdf",
                "INVENTORY GOODS RECEIPT NOTE\nPurchase Order PO-7788\nNitrile Gloves",
            ),
            "requisition": (
                "material-requisition.pdf",
                "MATERIAL REQUISITION\nPurchase Order requested\nNitrile Gloves",
            ),
            "numeric material requisition filename": (
                "MR-326 TO 332.pdf",
                "PO: 0326\nPO: 0327\nPO: 0328",
            ),
            "requirements status sheet without OCR": (
                "FIRST AID ITEMS REQUIRED FOR CVH.xlsx",
                "",
            ),
            "invoice": (
                "tax-invoice.pdf",
                "TAX INVOICE\nPurchase Order PO-7788\nNitrile Gloves",
            ),
            "unapproved draft": (
                "LPO-draft.pdf",
                "PURCHASE ORDER\nUNAPPROVED LPO DRAFT\nNitrile Gloves",
            ),
            "pending approval status": (
                "Oracle-order.pdf",
                "PURCHASE ORDER\nApproval Status: Pending Approval\nNitrile Gloves",
            ),
            "information only": (
                "LPO-information.pdf",
                "PURCHASE ORDER\nFOR INFORMATION ONLY - NOT A PURCHASE ORDER\nNitrile Gloves",
            ),
        }

        for label, (filename, document_text) in documents.items():
            with self.subTest(label=label):
                result = rank_message_to_quotations(
                    replace(
                        base_message,
                        source_kind="attachment",
                        document_text=document_text,
                        document_filename=filename,
                    ),
                    [quotation],
                )
                self.assertEqual(result.status, UNMATCHED)
                self.assertEqual(result.candidates, ())

    def test_generic_scans_with_non_order_headings_never_rank_exact_commercial_matches(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        exact_match = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
        )
        headings = (
            "COMMERCIAL OFFER",
            "PRICE OFFER",
            "REQUEST FOR QUOTATION",
            "SALES INVOICE",
            "DELIVERY ORDER",
        )

        for heading in headings:
            with self.subTest(heading=heading):
                result = rank_message_to_quotations(
                    replace(
                        exact_match,
                        source_kind="attachment",
                        document_filename="scan.pdf",
                        document_text=(
                            f"{heading}\nPO No: PO-7788\n"
                            f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
                        ),
                    ),
                    [quotation],
                )

                self.assertEqual(result.status, UNMATCHED)
                self.assertEqual(result.candidates, ())

    def test_explicit_non_order_parser_warnings_gate_ocr_less_scans(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        base_message = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
        )
        for warning in (
            "Document appears to be a delivery note, not a purchase order or quotation.",
            "Detailed receipt page shows 18 items and requires manual verification.",
        ):
            with self.subTest(warning=warning):
                result = rank_message_to_quotations(
                    replace(
                        base_message,
                        source_kind="attachment",
                        document_filename="scan.pdf",
                        document_text="",
                        parser_warnings=(warning,),
                    ),
                    [quotation],
                )
                self.assertEqual(result.status, UNMATCHED)
                self.assertIn("parser explicitly identified", result.reason)

    def test_po_quotation_metadata_does_not_make_a_purchase_order_a_supplier_quote(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text=(
                "ACME MEDICAL\nPURCHASE ORDER\nPO No: 7788\n"
                f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)

    def test_leading_labelled_quotation_heading_beats_later_purchase_order_metadata(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        supplier_quote = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="scan.pdf",
            document_text=(
                "VENDOR MEDICAL SUPPLIES\nQUOTATION NO: Q-123\n"
                "Purchase Order No: PO-7788\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(supplier_quote, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertIn("supplier quotation", result.reason)

    def test_purchase_order_number_metadata_does_not_outrank_bare_quotation_title(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        supplier_quote = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="scan.pdf",
            document_text=(
                "Purchase Order No: PO-7788\nQUOTATION\n"
                "Quotation Date: 02/07/2026\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(supplier_quote, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
        self.assertIn("quotation", result.reason.lower())

    def test_uppercase_mixed_po_and_quotation_metadata_remains_reviewable(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="scan.pdf",
            document_text=(
                "Purchase Order No: PO-7788\nQUOTATION NO: Q-123\n"
                "Quotation Date: 02/07/2026\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertEqual([candidate.quote_id for candidate in result.candidates], [quotation.quote_id])
        self.assertTrue(any("mixed" in blocker for blocker in result.automatic_blockers))

    def test_compact_po_with_mixed_quotation_metadata_remains_reviewable(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        for quote_label, date_label in (
            ("Quotation No", "Quotation Date"),
            ("quotation no", "quotation date"),
        ):
            with self.subTest(quote_label=quote_label):
                purchase_order = replace(
                    message(
                        [pline(1, "Nitrile Gloves", 10, 5)],
                        refs=(quotation.quotation_number,),
                        total=50,
                    ),
                    source_kind="attachment",
                    document_filename="scan.pdf",
                    document_text=(
                        "ACME MEDICAL\nPURCHASE ORDER NO: PO-7788\n"
                        f"{quote_label}: {quotation.quotation_number}\n"
                        f"{date_label}: 02/07/2026\nNitrile Gloves"
                    ),
                )

                result = rank_message_to_quotations(purchase_order, [quotation])

                self.assertEqual(result.status, AMBIGUOUS)
                self.assertEqual(
                    [candidate.quote_id for candidate in result.candidates],
                    [quotation.quote_id],
                )
                self.assertTrue(
                    any("mixed" in blocker for blocker in result.automatic_blockers)
                )

    def test_quotation_thread_subject_does_not_hide_explicit_body_acceptance(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        body = "Please proceed with the quoted 10 boxes. Total AED 50."
        acceptance = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="email_body",
            subject=f"Quotation No: {quotation.quotation_number}",
            body=body,
            document_text=body,
            document_filename="",
        )

        result = rank_message_to_quotations(acceptance, [quotation])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, quotation.quote_id)

    def test_quantity_only_quotation_thread_acceptance_remains_reviewable(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        body = "Please proceed with the quoted 10 boxes."
        acceptance = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, None)],
                refs=(quotation.quotation_number,),
            ),
            source_kind="email_body",
            subject=f"Quotation No: {quotation.quotation_number}",
            body=body,
            document_text=body,
            document_filename="",
        )

        result = rank_message_to_quotations(acceptance, [quotation])

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertEqual([candidate.quote_id for candidate in result.candidates], [quotation.quote_id])
        self.assertTrue(
            any("commercial corroboration" in blocker for blocker in result.automatic_blockers)
        )

    def test_purchase_order_hash_header_outranks_later_quotation_number_metadata(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text=(
                "PURCHASE ORDER # PO-7788\n"
                f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, quotation.quote_id)

    def test_purchase_order_no_header_outranks_later_quotation_number_metadata(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text=(
                "PURCHASE ORDER NO: PO-7788\n"
                f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, quotation.quote_id)

    def test_po_colon_header_outranks_later_quotation_number_metadata(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text=(
                "PO: PO-7788\n"
                f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, quotation.quote_id)

    def test_lpo_no_header_outranks_later_quotation_number_metadata(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="LPO-7788.pdf",
            document_text=(
                "LPO NO. LPO-7788\n"
                f"Quotation No: {quotation.quotation_number}\nNitrile Gloves"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, quotation.quote_id)

    def test_document_type_gate_respects_attachment_vs_body_provenance(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        base_message = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
            subject="INVOICE",
        )
        attachment = replace(
            base_message,
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text="",
        )
        email_body = replace(
            base_message,
            source_kind="email_body",
            document_filename="",
            document_text=base_message.body,
        )

        attachment_result = rank_message_to_quotations(attachment, [quotation])
        body_result = rank_message_to_quotations(email_body, [quotation])

        self.assertEqual(attachment_result.status, AUTOMATIC)
        self.assertEqual(body_result.status, UNMATCHED)
        self.assertIn("invoice", body_result.reason)

    def test_normal_po_terms_saying_changes_are_not_approved_do_not_trigger_draft_gate(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-FA-143490.pdf",
            document_text=(
                "PURCHASE ORDER\nPO No: FA-143490\nNitrile Gloves\n"
                "Terms: Changes made by the supplier are not approved by us unless "
                "confirmed in writing."
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)

    def test_zero_quantity_zero_total_calloff_contract_is_not_an_order(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        calloff = replace(
            message(
                [pline(1, "Nitrile Gloves", 0, 5)],
                refs=(quotation.quotation_number,),
                total=0,
            ),
            source_kind="attachment",
            document_filename="call-off-rate-contract.pdf",
            document_text="CALL-OFF / RATE CONTRACT\nPURCHASE ORDER\nQuantity 0\nTotal 0.00",
        )

        result = rank_message_to_quotations(calloff, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertIn("no positive ordered quantity", result.reason)

    def test_pending_delivery_and_open_balance_bodies_are_not_new_orders(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        base_message = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
        )
        for body in (
            "Delivery reminder for PO-7788: the order is still pending delivery.",
            "Please advise the open order balance and outstanding quantity for PO-7788.",
        ):
            with self.subTest(body=body):
                result = rank_message_to_quotations(
                    replace(
                        base_message,
                        source_kind="email_body",
                        subject="RE: Purchase Order PO-7788",
                        body=body,
                        document_text=body,
                    ),
                    [quotation],
                )
                self.assertEqual(result.status, UNMATCHED)
                self.assertIn("pending-delivery", result.reason)

    def test_sap_ariba_new_order_body_survives_information_boilerplate(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        body = (
            "SAP Ariba Network\nAcme Medical has sent you a new order PO-7788.\n"
            "This notification is for your information only.\n"
            f"Quotation: {quotation.quotation_number}\nNitrile Gloves"
        )
        ariba_message = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="email_body",
            subject="New order PO-7788 from Acme Medical - SAP Ariba",
            body=body,
            document_text=body,
        )

        result = rank_message_to_quotations(ariba_message, [quotation])

        self.assertEqual(result.status, AUTOMATIC)

    def test_predating_printed_date_for_exact_ref_stays_manual_and_never_automatic(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        base_message = message(
            [pline(1, "Nitrile Gloves", 10, 5)],
            refs=(quotation.quotation_number,),
            total=50,
        )
        documents = (
            "PURCHASE ORDER\nOrder Date: 30/06/2026\nNitrile Gloves",
            "PURCHASE ORDER\nOrig. Order Date:\n23/06/26\nNitrile Gloves",
            "PURCHASE ORDER\nPO Date\n30-Jun-2026\nNitrile Gloves",
            (
                "PURCHASE ORDER\nSupplier Quotation Ref: QT-OLD-100 "
                "Date: 30-Jun-2026\nNitrile Gloves"
            ),
        )
        for document_text in documents:
            with self.subTest(document_text=document_text):
                result = rank_message_to_quotations(
                    replace(
                        base_message,
                        source_kind="attachment",
                        document_filename="PO-7788.pdf",
                        document_text=document_text,
                    ),
                    [quotation],
                )
                self.assertEqual(result.status, AMBIGUOUS)
                self.assertEqual(result.candidates[0].document_date_result, "predates_quote")
                self.assertTrue(
                    any("predates" in blocker for blocker in result.automatic_blockers)
                )

    def test_predating_po_date_keeps_strong_full_quote_match_but_rejects_weak_coverage(self):
        lines = [
            qline(index, f"Medical Supply Item {index:02d}", 1, 5)
            for index in range(1, 30)
        ]
        quotation = quote(1, "QT-20260701-0001", lines)
        strong_rows = [
            pline(index, f"Medical Supply Item {index:02d}", 1, 5)
            for index in range(1, 7)
        ]
        strong_rows.extend(
            pline(100 + index, f"Metadata label {index:02d}", None, None, total=False)
            for index in range(1, 17)
        )
        strong_quote = replace(quotation, lines=tuple(lines[:6]))
        document_text = "PURCHASE ORDER\nPO Date\n23-Jun-2026\nMedical supplies"
        strong = replace(
            message(strong_rows, refs=(), total=30),
            source_kind="attachment",
            document_filename="MPO-0142.pdf",
            document_text=document_text,
        )
        weak = replace(
            message(strong_rows[:2], refs=(), total=10),
            source_kind="attachment",
            document_filename="PO-old.pdf",
            document_text=document_text,
        )

        strong_result = rank_message_to_quotations(strong, [strong_quote])
        weak_result = rank_message_to_quotations(weak, [quotation])

        self.assertEqual(strong_result.status, AMBIGUOUS)
        self.assertEqual(strong_result.candidates[0].quote_coverage, 1.0)
        self.assertLess(strong_result.candidates[0].item_coverage, 0.3)
        self.assertIn("predates", " ".join(strong_result.automatic_blockers))
        self.assertEqual(weak_result.status, UNMATCHED)
        self.assertTrue(
            any("coverage is too weak" in reason for reason, _count in weak_result.rejection_summary)
        )

    def test_strong_predating_match_stays_reviewable_below_normal_score_floor(self):
        quote_lines = [
            qline(index, f"Medical Supply Item {index:02d}", 1, 5)
            for index in range(1, 7)
        ]
        quotation = quote(
            1,
            "QT-20260701-0001",
            quote_lines,
            company="Sobha Constructions LLC",
            emails=(),
        )
        rows = [
            pline(index, f"Medical Supply Item {index:02d}", 1, 5, unit="BOT")
            for index in range(1, 7)
        ]
        rows.extend(
            pline(100 + index, f"Metadata label {index:02d}", None, None, total=False)
            for index in range(1, 17)
        )
        purchase_order = replace(
            message(
                rows,
                received_at=BASE + timedelta(days=400),
                refs=(),
                total=5,
            ),
            sender="Quantity Surveyor <surveyor@sobhaconst.com>",
            company_name="Sobha Constructions LLC",
            source_kind="attachment",
            document_filename="MPO-0142.pdf",
            document_text=(
                "SOBHA CONSTRUCTIONS LLC\nMATERIAL PURCHASE ORDER\n"
                "PO Date\n23-Jun-2026\nMedical supplies"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertLess(result.candidates[0].score, 20.0)
        self.assertEqual(result.candidates[0].quote_coverage, 1.0)
        self.assertIn("predates", " ".join(result.automatic_blockers))

    def test_terms_page_dates_do_not_trigger_the_printed_order_date_gate(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        purchase_order = replace(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=(quotation.quotation_number,),
                total=50,
            ),
            source_kind="attachment",
            document_filename="PO-7788.pdf",
            document_text=(
                "PURCHASE ORDER\nPO No: 7788\nNitrile Gloves\n"
                "TERMS AND CONDITIONS\nRevision Date: 30/06/2025\n"
                "Requested Delivery Date: 30/06/2026"
            ),
        )

        result = rank_message_to_quotations(purchase_order, [quotation])

        self.assertEqual(result.status, AUTOMATIC)

    def test_quote_created_after_the_message_is_never_a_candidate(self):
        old = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        later = quote(
            2,
            "QT-20260706-0001",
            [qline(21, "Nitrile Gloves", 10, 5)],
            sent_at=BASE + timedelta(days=6),
        )

        result = rank_message_to_quotations(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                received_at=BASE + timedelta(days=4),
                total=50,
            ),
            [later, old],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, old.quote_id)
        self.assertNotIn(later.quote_id, [candidate.quote_id for candidate in result.candidates])
        self.assertTrue(any("not after" in reason for reason, _count in result.rejection_summary))

    def test_wrong_explicit_reference_rejects_an_otherwise_identical_quote(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])

        result = rank_message_to_quotations(
            message(
                [pline(1, "Nitrile Gloves", 10, 5)],
                refs=("QT-20260701-9999",),
                total=50,
            ),
            [quotation],
        )

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
        self.assertTrue(any("points elsewhere" in reason for reason, _count in result.rejection_summary))

    def test_unit_price_line_total_and_document_total_break_an_item_tie(self):
        cheap = quote(1, "QT-20260701-0001", [qline(11, "First Aid Box", 2, 10)])
        exact = quote(2, "QT-20260701-0002", [qline(21, "First Aid Box", 2, 12)])

        result = rank_message_to_quotations(
            message([pline(1, "First Aid Box", 2, 12)], total=24),
            [cheap, exact],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, exact.quote_id)
        wrong = next(candidate for candidate in result.candidates if candidate.quote_id == cheap.quote_id)
        self.assertEqual(wrong.price_conflict_count, 1)
        self.assertEqual(wrong.total_conflict_count, 1)
        self.assertEqual(wrong.document_total_result, "conflict")

    def test_duplicate_rows_are_matched_one_to_one_and_not_reused(self):
        two_lines = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "Pickup Forceps", 1, 15), qline(12, "Pickup Forceps", 1, 15)],
        )
        one_line = quote(2, "QT-20260701-0002", [qline(21, "Pickup Forceps", 1, 15)])
        po_rows = [pline(1, "Pickup Forceps", 1, 15), pline(2, "Pickup Forceps", 1, 15)]

        result = rank_message_to_quotations(message(po_rows, total=30), [one_line, two_lines])

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, two_lines.quote_id)
        self.assertEqual(len(result.automatic_winner.matched_lines), 2)
        other = next(candidate for candidate in result.candidates if candidate.quote_id == one_line.quote_id)
        self.assertEqual(len(other.matched_lines), 1)
        self.assertEqual(other.item_coverage, 0.5)

    def test_same_customer_identical_quotes_remain_ambiguous(self):
        first = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        second = quote(2, "QT-20260701-0002", [qline(21, "Nitrile Gloves", 10, 5)])

        result = rank_message_to_quotations(
            message([pline(1, "Nitrile Gloves", 10, 5)], total=50),
            [first, second],
        )

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertIsNone(result.automatic_winner)
        self.assertEqual(result.ambiguity_margin, 0)

    def test_wrong_customer_item_overlap_is_not_even_an_ambiguous_candidate(self):
        emrill_quote = quote(
            1,
            "QT-20260701-0001",
            [
                qline(11, "Fire Warden Jacket", 20, 15),
                qline(12, "Drinking Water 500ml", 30, 1),
            ],
            company="Emrill Services LLC",
            emails=("buyer@emrill.example",),
        )
        wrong_customer_po = CanonicalMailboxMessage(
            message_id="gmail-vcm-order",
            sender="VCM Buyer <buyer@vcm.example>",
            recipients=("orders@pharmacy.example",),
            subject="Purchase Order PO-118924",
            body="VCM Facilities has issued purchase order PO-118924.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(
                pline(1, "Fire Warden Jacket", 20, 15),
                pline(2, "Drinking Water 500ml", 30, 1),
            ),
            lpo_references=("PO-118924",),
            document_total=D("330"),
        )

        result = rank_message_to_quotations(wrong_customer_po, [emrill_quote])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
        self.assertTrue(
            any("customer identity" in reason for reason, _count in result.rejection_summary)
        )

    def test_company_acronym_can_identify_a_customer_when_contact_email_is_missing(self):
        sabc_quote = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "First Aid Kit", 8, 25)],
            company="Structure Advanced Building Contracting LLC",
            emails=(),
        )
        sabc_po = CanonicalMailboxMessage(
            message_id="gmail-sabc-order",
            sender="Nagi <nagi@sabcuae.com>",
            recipients=("orders@pharmacy.example",),
            subject="Purchase Order PO-00139",
            body="Please process attached purchase order.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "First Aid Kit", 8, 25),),
            lpo_references=("PO-00139",),
            document_total=D("200"),
        )

        result = rank_message_to_quotations(sabc_po, [sabc_quote])

        self.assertEqual(result.status, AUTOMATIC)
        identity = next(
            component
            for component in result.automatic_winner.components
            if component.signal == "customer_identity"
        )
        self.assertIn("acronym", identity.detail)

    def test_compact_company_name_can_identify_real_customer_sender_domains(self):
        cases = (
            ("Dubai Scholars Private School", "clinic@dubaischolars.com"),
            ("Mohammed Abdulmohsin Al Kharafi", "buyer@ae.malkharafi.com"),
            ("Al Naboodah Contracting LLC", "buyer@alnaboodah.com"),
            ("Al Rakha General Contracting LLC", "bijialrakha@gmail.com"),
            ("Buhaleeba Contracting LLC", "buyer@buhaleeba.ae"),
            ("ECC", "buyer@eccgroup.ae"),
        )
        for index, (company, sender) in enumerate(cases, start=1):
            with self.subTest(company=company, sender=sender):
                quotation = quote(
                    index,
                    f"QT-20260701-{index:04d}",
                    [qline(index, "First Aid Kit", 2, 25)],
                    company=company,
                    emails=(),
                )
                po = CanonicalMailboxMessage(
                    message_id=f"gmail-company-{index}",
                    sender=sender,
                    recipients=("orders@pharmacy.example",),
                    subject=f"Purchase Order PO-{index:05d}",
                    body="Please process the attached purchase order.",
                    received_at=BASE + timedelta(days=2),
                    parsed_rows=(pline(1, "First Aid Kit", 2, 25),),
                    lpo_references=(f"PO-{index:05d}",),
                    document_total=D("50"),
                )

                result = rank_message_to_quotations(po, [quotation])

                self.assertEqual(result.status, AUTOMATIC)

        ecc_quote = quote(
            99,
            "QT-20260701-0099",
            [qline(99, "First Aid Kit", 2, 25)],
            company="ECC",
            emails=(),
        )
        unrelated = CanonicalMailboxMessage(
            message_id="gmail-eccentric",
            sender="buyer@eccentric.com",
            subject="Purchase Order PO-99999",
            body="Please process the purchase order.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "First Aid Kit", 2, 25),),
            lpo_references=("PO-99999",),
            document_total=D("50"),
        )
        self.assertEqual(
            rank_message_to_quotations(unrelated, [ecc_quote]).status,
            UNMATCHED,
        )

    def test_company_name_is_word_bounded_and_accor_does_not_match_accordance(self):
        accor_quote = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "Fire Warden Jacket", 20, 15)],
            company="ACCOR",
            emails=(),
        )
        emrill_po = CanonicalMailboxMessage(
            message_id="gmail-emrill-order",
            sender="Emrill Buyer <buyer@emrill.example>",
            recipients=("orders@pharmacy.example",),
            subject="Purchase Order PO-184363",
            body=(
                "EMRILL SERVICES LLC PURCHASE ORDER. All supply must be in accordance "
                "with the attached terms."
            ),
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "Fire Warden Jacket", 20, 15),),
            lpo_references=("PO-184363",),
            document_total=D("300"),
        )

        result = rank_message_to_quotations(emrill_po, [accor_quote])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_company_acronym_never_uses_public_mail_or_tld_labels(self):
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "Nitrile Gloves", 10, 5)],
            company="Central Operations Medical",
            emails=(),
        )
        attacker = CanonicalMailboxMessage(
            message_id="gmail-public-acronym",
            sender="attacker@anything.com",
            subject="Purchase Order PO-7788",
            body="Please process this purchase order.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "Nitrile Gloves", 10, 5),),
            lpo_references=("PO-7788",),
            document_total=D("50"),
        )

        result = rank_message_to_quotations(attacker, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_generic_company_word_cannot_match_a_domain_prefix(self):
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "Nitrile Gloves", 10, 5)],
            company="National Health Medical Supplies",
            emails=(),
        )
        unrelated = CanonicalMailboxMessage(
            message_id="gmail-generic-domain-prefix",
            sender="buyer@national.com",
            subject="Purchase Order PO-7788",
            body="Please process this purchase order.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "Nitrile Gloves", 10, 5),),
            lpo_references=("PO-7788",),
            document_total=D("50"),
        )

        result = rank_message_to_quotations(unrelated, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_multiword_company_token_is_not_a_short_domain_abbreviation(self):
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "First Aid Kit", 2, 25)],
            company="Dubai Scholars Private School",
            emails=(),
        )
        unrelated = CanonicalMailboxMessage(
            message_id="gmail-dubai-token",
            sender="clinic@dubai.com",
            subject="Purchase Order PO-7788",
            body="Please process this purchase order.",
            received_at=BASE + timedelta(days=2),
            parsed_rows=(pline(1, "First Aid Kit", 2, 25),),
            lpo_references=("PO-7788",),
            document_total=D("50"),
        )

        result = rank_message_to_quotations(unrelated, [quotation])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_candidates_are_hard_capped_at_three(self):
        quotations = [
            quote(index, f"QT-20260701-{index:04d}", [qline(index, "Nitrile Gloves", 10, 5)])
            for index in range(1, 7)
        ]

        result = rank_message_to_quotations(
            message([pline(1, "Nitrile Gloves", 10, 5)], total=50),
            quotations,
            max_candidates=99,
        )

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertEqual(len(result.candidates), 3)

    def test_missing_quantity_or_commercial_value_never_auto_links_even_with_exact_ref(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        missing_quantity = message(
            [pline(1, "Nitrile Gloves", None, 5)],
            refs=(quotation.quotation_number,),
        )
        missing_price_and_total = message(
            [pline(1, "Nitrile Gloves", 10, None, total=False)],
            refs=(quotation.quotation_number,),
        )

        quantity_result = rank_message_to_quotations(missing_quantity, [quotation])
        commercial_result = rank_message_to_quotations(missing_price_and_total, [quotation])

        self.assertEqual(quantity_result.status, AMBIGUOUS)
        self.assertEqual(commercial_result.status, AMBIGUOUS)
        self.assertIsNone(quantity_result.automatic_winner)
        self.assertIsNone(commercial_result.automatic_winner)

    def test_one_exact_price_does_not_corroborate_a_mostly_priceless_po(self):
        names = [
            "Nitrile Gloves",
            "Sterile Gauze",
            "Insulin Syringe",
            "Elastic Bandage",
            "Digital Thermometer",
        ]
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(index, name, 1, 10) for index, name in enumerate(names, start=1)],
        )
        po_rows = [pline(1, names[0], 1, 10)] + [
            pline(index, name, 1, None, total=False)
            for index, name in enumerate(names[1:], start=2)
        ]

        result = rank_message_to_quotations(
            message(po_rows, refs=(quotation.quotation_number,)),
            [quotation],
        )

        self.assertEqual(result.status, AMBIGUOUS)
        candidate = result.candidates[0]
        self.assertEqual(candidate.commercial_exact_row_count, 1)
        self.assertEqual(candidate.commercial_row_coverage, 0.2)
        self.assertEqual(candidate.commercial_corroboration_result, "insufficient")
        self.assertTrue(
            any("at least 80%" in blocker for blocker in result.automatic_blockers)
        )

    def test_exact_commercial_values_on_four_of_five_rows_pass_the_row_gate(self):
        names = [
            "Nitrile Gloves",
            "Sterile Gauze",
            "Insulin Syringe",
            "Elastic Bandage",
            "Digital Thermometer",
        ]
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(index, name, 1, 10) for index, name in enumerate(names, start=1)],
        )
        po_rows = [
            pline(index, name, 1, 10 if index <= 4 else None, total=index <= 4)
            for index, name in enumerate(names, start=1)
        ]

        result = rank_message_to_quotations(
            message(po_rows, refs=(quotation.quotation_number,)),
            [quotation],
        )

        self.assertEqual(result.status, AUTOMATIC)
        winner = result.automatic_winner
        self.assertEqual(winner.commercial_exact_row_count, 4)
        self.assertEqual(winner.commercial_row_coverage, 0.8)
        self.assertEqual(winner.commercial_corroboration_result, "row_coverage_exact")

    def test_exact_document_total_can_corroborate_rows_without_parsed_prices(self):
        quotation = quote(
            1,
            "QT-20260701-0001",
            [qline(11, "Nitrile Gloves", 2, 10), qline(12, "Sterile Gauze", 3, 10)],
        )
        po_rows = [
            pline(1, "Nitrile Gloves", 2, None, total=False),
            pline(2, "Sterile Gauze", 3, None, total=False),
        ]

        result = rank_message_to_quotations(
            message(po_rows, refs=(quotation.quotation_number,), total=50),
            [quotation],
        )

        self.assertEqual(result.status, AUTOMATIC)
        winner = result.automatic_winner
        self.assertEqual(winner.commercial_row_coverage, 0)
        self.assertEqual(winner.document_total_result, "exact")
        self.assertEqual(winner.commercial_corroboration_result, "document_total_exact")

    def test_provided_document_total_mismatch_blocks_even_when_every_row_price_is_exact(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 10)])

        result = rank_message_to_quotations(
            message(
                [pline(1, "Nitrile Gloves", 10, 10)],
                refs=(quotation.quotation_number,),
                total=90,
            ),
            [quotation],
        )

        self.assertEqual(result.status, AMBIGUOUS)
        candidate = result.candidates[0]
        self.assertTrue(candidate.document_total_provided)
        self.assertEqual(candidate.document_total_result, "conflict")
        self.assertEqual(candidate.commercial_corroboration_result, "document_total_conflict")
        self.assertTrue(
            any("document total conflicts" in blocker for blocker in result.automatic_blockers)
        )

    def test_a_near_price_is_a_conflict_not_exact_commercial_corroboration(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 10)])

        result = rank_message_to_quotations(
            message(
                [pline(1, "Nitrile Gloves", 10, "9.95")],
                refs=(quotation.quotation_number,),
            ),
            [quotation],
        )

        self.assertEqual(result.status, AMBIGUOUS)
        candidate = result.candidates[0]
        self.assertEqual(candidate.price_exact_count, 0)
        self.assertEqual(candidate.price_conflict_count, 1)
        self.assertEqual(candidate.total_conflict_count, 1)

    def test_parser_or_material_warning_forces_staff_review(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Nitrile Gloves", 10, 5)])
        rows = [pline(1, "Nitrile Gloves", 10, 5)]

        parser_result = rank_message_to_quotations(
            message(
                rows,
                refs=(quotation.quotation_number,),
                total=50,
                parser_warnings=("OCR confidence was low on the quantity column.",),
            ),
            [quotation],
        )
        material_result = rank_message_to_quotations(
            message(
                rows,
                refs=(quotation.quotation_number,),
                total=50,
                material_warnings=("Two possible grand totals were detected.",),
            ),
            [quotation],
        )

        self.assertEqual(parser_result.status, AMBIGUOUS)
        self.assertEqual(material_result.status, AMBIGUOUS)
        self.assertEqual(
            parser_result.candidates[0].parser_warnings,
            ("OCR confidence was low on the quantity column.",),
        )
        self.assertEqual(
            material_result.candidates[0].material_warnings,
            ("Two possible grand totals were detected.",),
        )
        self.assertIn("parser reported 1 warning", parser_result.reason)
        self.assertIn("material warning", material_result.reason)

    def test_conflicting_strength_is_not_treated_as_item_overlap(self):
        quotation = quote(1, "QT-20260701-0001", [qline(11, "Paracetamol 500 mg Tablet", 10, 5)])

        result = rank_message_to_quotations(
            message([pline(1, "Paracetamol 250 mg Tablet", 10, 5)], total=50),
            [quotation],
        )

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_mapping_inputs_are_canonicalized_without_django_models(self):
        result = rank_message_to_quotations(
            {
                "gmail_message_id": "gmail-map",
                "sender": "buyer@acme.example",
                "subject": "LPO LPO-55",
                "body_text": "Please proceed",
                "sent_at": (BASE + timedelta(days=1)).isoformat(),
                "parsed_attachment_rows": [
                    {"id": "p1", "item_name": "Waterproof Bandage", "qty": "5", "price": "2"}
                ],
                "lpo_refs": ["LPO-55"],
                "total": "10",
            },
            [
                {
                    "id": 7,
                    "quotation_number": "QT-20260701-0007",
                    "sent_at": BASE.isoformat(),
                    "company_name": "Acme Medical",
                    "customer_emails": ["buyer@acme.example"],
                    "lines": [
                        {
                            "id": "q1",
                            "name": "Water Proof Bandage",
                            "quoted_quantity": "5",
                            "quoted_unit_price": "2",
                            "quoted_total": "10",
                        }
                    ],
                }
            ],
        )

        self.assertEqual(result.status, AUTOMATIC)
        self.assertEqual(result.automatic_winner.quote_id, 7)
        self.assertEqual(result.as_dict()["candidates"][0]["matched_lines"][0]["po_line_id"], "p1")

    def test_mapping_warning_aliases_are_normalized_for_the_caller(self):
        canonical = canonicalize_message(
            {
                "warnings": [
                    {"message": "OCR confidence is low."},
                    "OCR confidence is low.",
                ],
                "blocking_warnings": [{"detail": "Grand total is ambiguous."}],
            }
        )

        self.assertEqual(canonical.parser_warnings, ("OCR confidence is low.",))
        self.assertEqual(canonical.material_warnings, ("Grand total is ambiguous.",))
