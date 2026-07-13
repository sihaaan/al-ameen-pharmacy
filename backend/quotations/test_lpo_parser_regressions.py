import hashlib
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from reportlab.pdfgen import canvas

from .import_parsers import parse_pdf_preview, parse_text_preview
from .import_rules import parse_text_lines
from .models import Company, Quotation, QuotationLine
from .services import build_po_outcome_suggestions


class LPOTextParserRegressionTests(SimpleTestCase):
    def test_description_quantity_blocks_parse_as_individual_items(self):
        lines, skipped = parse_text_lines(
            "\n".join(
                [
                    "Description: COVERALL JACKET",
                    "• Quantity: 20 nos.",
                    "Description: Small drinking water 500ml",
                    "- Quantity: 30 nos.",
                ]
            )
        )

        self.assertEqual(skipped, 0)
        self.assertEqual(
            [(line["raw_name"], line["quantity"], line["unit"].lower()) for line in lines],
            [
                ("Coverall Jacket", "20", "nos"),
                ("Small Drinking Water 500ml", "30", "nos"),
            ],
        )

    def test_single_description_block_does_not_turn_numeric_metadata_into_an_item(self):
        lines, skipped = parse_text_lines(
            "\n".join(
                [
                    "Description: COVERALL JACKET",
                    "Quantity: 20 nos",
                    "20.00",
                    "Order total",
                ]
            )
        )

        self.assertEqual([(line["raw_name"], line["quantity"]) for line in lines], [("Coverall Jacket", "20")])
        self.assertLessEqual(skipped, 1)

    def test_description_blocks_retain_other_plausible_item_rows(self):
        lines, skipped = parse_text_lines(
            "\n".join(
                [
                    "Description: COVERALL JACKET",
                    "Quantity: 20 nos",
                    "Bandage pack - 4 boxes",
                    "Sent from customer portal",
                ]
            )
        )

        self.assertEqual(
            {(line["raw_name"], line["quantity"]) for line in lines},
            {("Coverall Jacket", "20"), ("Bandage Pack", "4")},
        )
        self.assertLessEqual(skipped, 1)

    def test_pdf_header_only_table_falls_back_to_word_layout_for_dettol_row(self):
        buffer = BytesIO()
        pdf = canvas.Canvas(buffer)
        for x, value in [(72, "Description"), (300, "Quantity"), (390, "Unit"), (465, "Price")]:
            pdf.drawString(x, 730, value)
        for x, value in [(72, "DETTOL ANTISEPTIC LIQUID 60ML"), (300, "20"), (390, "NO"), (465, "5")]:
            pdf.drawString(x, 710, value)
        pdf.save()
        data = buffer.getvalue()
        header_only = {
            "raw_line": "Ln | Req/Quote No | Item Number | Description | Quantity | Unit | Unit Price",
            "raw_name": "Req/Quote No",
            "requested_item_name": "Req/Quote No",
            "quantity": "Quantity",
            "unit": "Unit",
        }

        with patch(
            "quotations.import_parsers._parse_pdfplumber_tables",
            return_value=([header_only], [{"page_number": 1, "tables_seen": 1}], 1, 0),
        ):
            preview = parse_pdf_preview(
                data,
                "dettol-lpo.pdf",
                "application/pdf",
                hashlib.sha256(data).hexdigest(),
            )

        self.assertEqual(preview["parse_method"], "pymupdf_word_layout_v1")
        self.assertEqual(len(preview["lines"]), 1)
        self.assertEqual(preview["lines"][0]["raw_name"], "Dettol Antiseptic Liquid 60ml")
        self.assertEqual(preview["lines"][0]["quantity"], "20")
        self.assertEqual(preview["lines"][0]["unit"], "NO")
        self.assertEqual(preview["lines"][0]["unit_price"], "5")
        self.assertTrue(any("no plausible item rows" in warning for warning in preview["warnings"]))


class LPOOutcomeGuardRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lpo-parser-regression", is_staff=True)
        self.company = Company.objects.create(name="LPO Parser Regression Customer")
        self.quotation = Quotation.objects.create(company=self.company, created_by=self.user)

    def add_line(self, name, *, sort_order=0):
        return QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot=name,
            quantity=Decimal("1"),
            unit="No",
            unit_price=Decimal("10"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=sort_order,
        )

    def test_aggregate_po_summary_never_creates_an_automatic_line_outcome(self):
        quote_line = self.add_line("Clinic Supplies")
        preview = parse_text_preview("Clinic Supplies - 38 items")

        suggestions, unmatched, missing = build_po_outcome_suggestions(self.quotation, preview)

        self.assertEqual(suggestions, [])
        self.assertEqual(unmatched[0]["reason_code"], "aggregate_summary")
        self.assertIn("staff", unmatched[0]["reason"].lower())
        self.assertEqual(missing, [quote_line.id])
        self.assertTrue(any("Aggregate PO item summary" in warning for warning in preview["warnings"]))
        self.assertTrue(preview["meta"]["aggregate_po_summary_detected"])

    def test_ai_rewrite_cannot_remove_aggregate_po_outcome_guard(self):
        quote_line = self.add_line("Clinic Supplies")
        deterministic_preview = parse_text_preview("Clinic Supplies - 38 items")
        ai_preview = {
            "lines": [{"raw_line": "Clinic Supplies", "raw_name": "Clinic Supplies"}],
            "warnings": [],
            "meta": deterministic_preview["meta"],
        }

        suggestions, unmatched, missing = build_po_outcome_suggestions(self.quotation, ai_preview)

        self.assertEqual(suggestions, [])
        self.assertEqual(unmatched[0]["reason_code"], "aggregate_summary")
        self.assertEqual(missing, [quote_line.id])

    def test_numeric_only_row_is_rejected_before_item_matching(self):
        water_line = self.add_line("Small drinking water 500ml")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_line": "20.00", "raw_name": "20.00", "requested_item_name": "20.00"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(unmatched[0]["reason_code"], "non_item_metadata")
        self.assertEqual(missing, [water_line.id])

    def test_description_quantity_item_can_reach_outcome_suggestion(self):
        jacket_line = self.add_line("Coverall Jacket")
        parsed_lines, _skipped = parse_text_lines(
            "Description: COVERALL JACKET\nQuantity: 20 nos."
        )

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": parsed_lines},
        )

        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0]["quotation_line_id"], jacket_line.id)
        self.assertEqual(suggestions[0]["suggested_accepted_quantity"], "20")
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])
