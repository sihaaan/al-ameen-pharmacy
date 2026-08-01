from django.test import SimpleTestCase

from .import_rules import HeaderDetection, parse_structured_row
from .models import InquiryLine


class StructuredRowFidelityTests(SimpleTestCase):
    def parse_row(self, row, columns):
        header = HeaderDetection(
            row_offset=0,
            row_number=1,
            columns=columns,
            score=5.0,
            labels={},
            data_score=2.0,
        )
        line, error = parse_structured_row(row, header, source_row=2)
        self.assertIsNone(error)
        self.assertIsNotNone(line)
        return line

    def test_grouped_quantity_preserves_compatibility_value_but_needs_review(self):
        line = self.parse_row(
            ["Bandages", "1,000", "PCS"],
            {"requested_item_name": 0, "quantity": 1, "unit": 2},
        )

        self.assertEqual(line["quantity"], "1")
        self.assertEqual(line["unit"], "PCS")
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn(
            "Quantity uses grouped numeric text and requires review: 1,000",
            line["notes"],
        )

    def test_signed_quantity_preserves_compatibility_value_but_needs_review(self):
        line = self.parse_row(
            ["Bandages", "-5", "PCS"],
            {"requested_item_name": 0, "quantity": 1, "unit": 2},
        )

        self.assertEqual(line["quantity"], "5")
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn(
            "Quantity uses signed numeric text and requires review: -5",
            line["notes"],
        )

    def test_missing_quantity_needs_review_with_explicit_note(self):
        line = self.parse_row(
            ["Bandages", "", "PCS"],
            {"requested_item_name": 0, "quantity": 1, "unit": 2},
        )

        self.assertIsNone(line["quantity"])
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn("Quantity is missing and requires review.", line["notes"])

    def test_missing_unit_needs_review_with_explicit_note(self):
        line = self.parse_row(
            ["Bandages", "5", ""],
            {"requested_item_name": 0, "quantity": 1, "unit": 2},
        )

        self.assertEqual(line["quantity"], "5")
        self.assertEqual(line["unit"], "")
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn("Unit is missing and requires review.", line["notes"])

    def test_normal_decimal_values_remain_parsed(self):
        line = self.parse_row(
            ["Liquid", "12.5", "bottles", "19.95", "239.40"],
            {
                "requested_item_name": 0,
                "quantity": 1,
                "unit": 2,
                "unit_price": 3,
                "line_total": 4,
            },
        )

        self.assertEqual(line["quantity"], "12.5")
        self.assertEqual(line["unit_price"], "19.95")
        self.assertEqual(line["line_total"], "239.4")
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_PARSED)
        self.assertNotIn("requires review", line["notes"])

    def test_grouped_price_and_signed_grouped_total_need_review(self):
        line = self.parse_row(
            ["Bandages", "2", "PCS", "1,000", "-2,000"],
            {
                "requested_item_name": 0,
                "quantity": 1,
                "unit": 2,
                "unit_price": 3,
                "line_total": 4,
            },
        )

        self.assertEqual(line["unit_price"], "1")
        self.assertEqual(line["line_total"], "2")
        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn(
            "Unit price uses grouped numeric text and requires review: 1,000",
            line["notes"],
        )
        self.assertIn(
            "Total uses signed and grouped numeric text and requires review: -2,000",
            line["notes"],
        )

    def test_signed_grouped_vat_rate_preserves_raw_value_for_review(self):
        line = self.parse_row(
            ["Bandages", "2", "PCS", "-5,000"],
            {
                "requested_item_name": 0,
                "quantity": 1,
                "unit": 2,
                "vat_rate": 3,
            },
        )

        self.assertEqual(line["parse_status"], InquiryLine.PARSE_NEEDS_REVIEW)
        self.assertIn(
            "VAT rate uses signed and grouped numeric text and requires review: -5,000",
            line["notes"],
        )

    def test_review_source_excerpt_is_bounded(self):
        quantity_source = f"1,000 {'x' * 200}"
        line = self.parse_row(
            ["Bandages", quantity_source, "PCS"],
            {"requested_item_name": 0, "quantity": 1, "unit": 2},
        )

        review_note = next(
            note
            for note in line["notes"].split("; ")
            if note.startswith("Quantity uses grouped numeric text")
        )
        self.assertTrue(review_note.endswith("..."))
        self.assertLessEqual(len(review_note), 143)
