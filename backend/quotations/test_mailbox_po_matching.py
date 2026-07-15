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
