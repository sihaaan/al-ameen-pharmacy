from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    rank_message_to_quotations,
)


D = Decimal
BASE = datetime(2026, 6, 22, 14, 0, tzinfo=timezone.utc)


def qline(line_id, name, quantity, price):
    quantity = D(str(quantity))
    price = D(str(price))
    return EligibleQuoteLine(
        line_id=line_id,
        name=name,
        quantity=quantity,
        unit_price=price,
        line_total=quantity * price,
        unit="NOS",
    )


def pline(line_id, name, quantity, price):
    quantity = D(str(quantity))
    price = D(str(price))
    return MailboxPOLine(
        line_id=line_id,
        name=name,
        quantity=quantity,
        unit_price=price,
        line_total=quantity * price,
        unit="NOS",
    )


def quotation(
    quote_id,
    number,
    lines,
    *,
    company="Al Rakha Contracting",
    emails=("buyer@alrakha.ae",),
):
    lines = tuple(lines)
    return EligibleQuotation(
        quote_id=quote_id,
        quotation_number=number,
        sent_at=BASE,
        company_name=company,
        customer_emails=tuple(emails),
        lines=lines,
        grand_total=sum((line.line_total for line in lines), D("0")),
    )


def purchase_order(
    rows,
    *,
    sender="Al Rakha Buyer <buyer@alrakha.ae>",
    company="Al Rakha Contracting",
    total=None,
    source_kind="attachment",
    document_text="PURCHASE ORDER\nAl Rakha Contracting",
    body="Please process the attached purchase order.",
    parser_warnings=(),
    material_warnings=(),
):
    return CanonicalMailboxMessage(
        message_id="gmail-candidate-precision",
        sender=sender,
        recipients=("orders@pharmacy.example",),
        subject="Purchase Order PO-1001",
        body=body,
        received_at=BASE + timedelta(days=3),
        parsed_rows=tuple(rows),
        lpo_references=("PO-1001",),
        company_name=company,
        document_total=D(str(total)) if total is not None else None,
        source_kind=source_kind,
        document_text=document_text,
        document_filename="PO-1001.pdf" if source_kind == "attachment" else "",
        parser_warnings=tuple(parser_warnings),
        material_warnings=tuple(material_warnings),
    )


class MailboxPOCandidatePrecisionTests(TestCase):
    def test_aed_and_oxygen_quote_outranks_and_suppresses_oxygen_only_quote(self):
        complete = quotation(
            202,
            "QT-20260622-0007",
            [
                qline(2436, "Automated External Defibrillator (AED Machine)", 1, 3400),
                qline(2437, "Medical Oxygen Cylinder Small (2.8 L)", 1, 650),
            ],
        )
        oxygen_only = quotation(
            203,
            "QT-20260622-0008",
            [qline(2438, "Medical Oxygen Cylinder Small (2.8 L)", 1, 650)],
        )
        message = purchase_order(
            [
                pline(
                    1,
                    "AED Machine with Adult and Pediatric Pads Heart Plus",
                    1,
                    3400,
                ),
                pline(2, "Oxygen Cylinder - Small 2.8 l", 1, 650),
            ],
            total=4050,
            parser_warnings=("OCR extraction requires review",),
            material_warnings=("Image-only PO requires staff review",),
        )

        result = rank_message_to_quotations(
            message,
            [oxygen_only, complete],
            # Prove the weaker 50%-coverage quote is filtered by commercial
            # relevance, not merely by the normal score-margin cutoff.
            automatic_margin=100,
        )

        self.assertEqual(result.status, AMBIGUOUS, result.reason)
        self.assertIsNone(result.automatic_winner)
        self.assertEqual(
            [candidate.quote_id for candidate in result.candidates],
            [complete.quote_id],
        )
        self.assertEqual(len(result.candidates[0].matched_lines), 2)

    def test_bare_aed_currency_token_is_not_a_defibrillator_alias(self):
        device = quotation(
            202,
            "QT-20260622-0007",
            [qline(2436, "Automated External Defibrillator (AED Machine)", 1, 3400)],
        )
        currency_artifact = purchase_order(
            [pline(1, "AED", 1, 3400)],
            total=3400,
            document_text="PURCHASE ORDER\nCurrency: AED\nTotal AED 3,400",
        )

        result = rank_message_to_quotations(currency_artifact, [device])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_one_generic_row_with_only_a_quantity_conflict_is_rejected(self):
        emrill_quote = quotation(
            1564,
            "QT-20260617-0001",
            [
                qline(1734, "FIRST AID KIT", 1, 480),
                qline(1735, "Sterile Gauze", 10, 2),
            ],
            company="Emrill Services LLC",
            emails=("buyer@emrill.com",),
        )
        generic_order = purchase_order(
            [
                pline(
                    1,
                    "FIRST AID KIT WITH MEDICINE 10-20 PERSONS PLASTIC BOX FOR VEHICLE",
                    2,
                    55,
                )
            ],
            sender="Emrill Buyer <buyer@emrill.com>",
            company="Emrill Services LLC",
            total=110,
            document_text="PURCHASE ORDER\nEmrill Services LLC\nFirst Aid Kit",
        )

        result = rank_message_to_quotations(generic_order, [emrill_quote])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
        self.assertTrue(
            any(
                "every comparable matched PO quantity conflicts" in reason
                for reason, _count in result.rejection_summary
            )
        )

    def test_all_reduced_partial_lpo_remains_a_candidate(self):
        full_quote = quotation(
            1,
            "QT-20260622-0010",
            [
                qline(1, "Nitrile Gloves", 10, 5),
                qline(2, "Sterile Gauze", 20, 2),
            ],
        )
        partial_order = purchase_order(
            [pline(1, "Nitrile Gloves", 4, 5)],
            total=20,
        )

        result = rank_message_to_quotations(partial_order, [full_quote])

        self.assertNotEqual(result.status, UNMATCHED, result.reason)
        self.assertEqual(result.candidates[0].quote_id, full_quote.quote_id)
        self.assertEqual(result.candidates[0].quantity_reduced_count, 1)
        self.assertEqual(result.candidates[0].quantity_conflict_count, 0)

    def test_near_tie_runner_up_remains_visible(self):
        first = quotation(
            1,
            "QT-20260622-0011",
            [qline(1, "Nitrile Gloves", 10, 5)],
        )
        second = quotation(
            2,
            "QT-20260622-0012",
            [qline(2, "Nitrile Gloves", 10, 5)],
        )

        result = rank_message_to_quotations(
            purchase_order([pline(1, "Nitrile Gloves", 10, 5)], total=50),
            [first, second],
        )

        self.assertEqual(result.status, AMBIGUOUS)
        self.assertEqual(result.ambiguity_margin, 0)
        self.assertEqual({candidate.quote_id for candidate in result.candidates}, {1, 2})

    def test_selected_attachment_company_name_can_supply_private_domain_identity(self):
        khansaheb_quote = quotation(
            1551,
            "QT-20260604-0006",
            [qline(1, "Large First Aid Bag", 1, 490)],
            company="Khansaheb Civil Engineering",
            emails=("buyer@khansaheb.com",),
        )
        portal_order = purchase_order(
            [pline(1, "Large First Aid Bag", 1, 490)],
            sender="Procurement Portal <orders@procurement-portal.example>",
            company="",
            total=490,
            source_kind="attachment",
            document_text=(
                "PURCHASE ORDER\nKhansaheb Civil Engineering L.L.C.\n"
                "Large First Aid Bag\nTotal AED 490"
            ),
            body="A portal order is attached.",
        )

        result = rank_message_to_quotations(portal_order, [khansaheb_quote])

        self.assertEqual(result.status, AUTOMATIC, result.reason)
        identity = next(
            component
            for component in result.candidates[0].components
            if component.signal == "customer_identity"
        )
        self.assertEqual(identity.score, 6.0)
        self.assertIn("selected attachment", identity.detail)

    def test_wrapper_body_company_name_cannot_spoof_private_domain_identity(self):
        khansaheb_quote = quotation(
            1551,
            "QT-20260604-0006",
            [qline(1, "Large First Aid Bag", 1, 490)],
            company="Khansaheb Civil Engineering",
            emails=("buyer@khansaheb.com",),
        )
        attachment_order = purchase_order(
            [pline(1, "Large First Aid Bag", 1, 490)],
            sender="Procurement Portal <orders@procurement-portal.example>",
            company="",
            total=490,
            source_kind="attachment",
            document_text="PURCHASE ORDER\nLarge First Aid Bag\nTotal AED 490",
            body="Forwarded for Khansaheb Civil Engineering. Please process this order.",
        )
        body_spoof = replace(
            attachment_order,
            source_kind="email_body",
            document_text=attachment_order.body,
            document_filename="",
        )

        result = rank_message_to_quotations(body_spoof, [khansaheb_quote])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())

    def test_generic_single_token_legal_name_cannot_supply_attachment_identity(self):
        generic_quote = quotation(
            5,
            "QT-20260622-0099",
            [qline(1, "Large First Aid Bag", 1, 490)],
            company="Engineering Contracting Co LLC",
            emails=("buyer@real-customer.example",),
        )
        unrelated_portal_order = purchase_order(
            [pline(1, "Large First Aid Bag", 1, 490)],
            sender="Portal <orders@unrelated.example>",
            company="",
            total=490,
            document_text=(
                "PURCHASE ORDER\nEngineering department\n"
                "Large First Aid Bag\nTotal AED 490"
            ),
        )

        result = rank_message_to_quotations(unrelated_portal_order, [generic_quote])

        self.assertEqual(result.status, UNMATCHED)
        self.assertEqual(result.candidates, ())
