import hashlib
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from reportlab.pdfgen import canvas

from api.models import Product

from .ai_parsing import (
    AI_DETERMINISTIC_GUARD_WARNING,
    AIParseError,
    _select_mode,
    clean_preview_with_ai,
    prefer_safe_ai_preview,
)
from .import_parsers import _parse_pdf_word_layout_item_rows, parse_pdf_preview, parse_text_preview
from .import_rules import parse_text_lines
from .models import AIParseCache, Company, Quotation, QuotationLPO, QuotationLine
from .services import (
    AI_QUOTE_COVERAGE_GUARD_WARNING,
    build_guarded_po_outcome_suggestions,
    build_po_outcome_suggestions,
)
from .views import _extract_lpo_details


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
                    "• Description: COVERALL JACKET",
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

    def test_portal_description_blocks_skip_layout_and_legal_metadata(self):
        lines, skipped = parse_text_lines(
            "\n".join(
                [
                    "Line number | Item number | Name | Unit | Quantity | Unit price",
                    "1 | 10111782 | University of | LS | 20.00 | 0.00",
                    "Description: Supply of fire warden jackets",
                    "• Location: University of Birmingham, Dubai",
                    "• Frequency: One time Service",
                    "• Quantity: 20 nos.",
                    "• Service Provider: Al Ameen Pharmacy",
                    "Description: Supply of small drinking water 500ml",
                    "• Location: University of Birmingham, Dubai",
                    "• Frequency: One time Service",
                    "• Quantity: 30 nos.",
                    "• Service Provider: Al Ameen Pharmacy",
                    "The supplier shall comply with all terms and conditions.",
                ]
            )
        )

        self.assertEqual(
            [(line["raw_name"], line["quantity"], line["unit"].lower()) for line in lines],
            [
                ("Supply of Fire Warden Jackets", "20", "nos"),
                ("Supply of Small Drinking Water 500ml", "30", "nos"),
            ],
        )
        self.assertGreater(skipped, 0)

    def test_word_layout_item_row_preserves_quantity_and_unit_price_columns(self):
        rows = _parse_pdf_word_layout_item_rows(
            "\n".join(
                [
                    "Ln | Req/Quote | Item Number | Description | Quantity | Unit | Unit Price | Total Price",
                    "1 | 69706 81.14.15.01.000966 | DETTOL ANTISEPTIC LIQUID 60ML | 20.00 NO | 5.00 | 100.00",
                ]
            )
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["raw_name"], "Dettol Antiseptic Liquid 60ml")
        self.assertEqual(rows[0]["quantity"], "20")
        self.assertEqual(rows[0]["unit"], "NO")
        self.assertEqual(rows[0]["unit_price"], "5")
        self.assertEqual(rows[0]["line_total"], "100")

    def test_ai_cannot_change_strong_deterministic_quantity_or_price(self):
        deterministic = {
            "lines": [
                {
                    "raw_name": "Dettol Antiseptic Liquid 60ml",
                    "quantity": "20",
                    "unit": "NO",
                    "unit_price": "5",
                    "line_total": "100",
                    "parse_confidence": 0.92,
                }
            ],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {
            "lines": [
                {
                    "raw_name": "Dettol Antiseptic Liquid 60ml",
                    "quantity": "1",
                    "unit": "No",
                    "unit_price": "100",
                    "line_total": "100",
                }
            ],
            "warnings": ["AI changed the row."],
        }

        selected = prefer_safe_ai_preview(deterministic, ai_preview)

        self.assertEqual(selected["lines"], deterministic["lines"])
        self.assertTrue(selected["meta"]["ai_cleanup_rejected"])
        self.assertIn(AI_DETERMINISTIC_GUARD_WARNING, selected["warnings"])

    def test_ai_cannot_replace_strong_items_with_unrelated_metadata(self):
        deterministic = {
            "lines": [
                {
                    "raw_name": "Supply of fire warden jackets",
                    "quantity": "20",
                    "unit": "nos",
                    "parse_confidence": 0.95,
                },
                {
                    "raw_name": "Supply of small drinking water 500ml",
                    "quantity": "30",
                    "unit": "nos",
                    "parse_confidence": 0.95,
                },
            ],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {"lines": [{"raw_name": "University of", "quantity": ""}], "warnings": []}

        selected = prefer_safe_ai_preview(deterministic, ai_preview)

        self.assertEqual(selected["lines"], deterministic["lines"])
        self.assertTrue(selected["meta"]["ai_cleanup_rejected"])

    def test_element_metadata_rows_do_not_override_ai_vision_items(self):
        deterministic = {
            "lines": [
                {
                    "raw_name": "Delivery Date : 08/07/2026",
                    "quantity": "8",
                    "parse_confidence": 0.82,
                },
                {
                    "raw_name": "Phone",
                    "raw_line": "Phone 42713695",
                    "quantity": "42713695",
                    "parse_confidence": 0.82,
                },
                {
                    "raw_name": "Phone 042487029",
                    "quantity": "042487029",
                    "parse_confidence": 0.82,
                },
            ],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {
            "lines": [
                {"raw_name": "Small First Aid Box", "quantity": "8", "parse_confidence": 0.96},
                {"raw_name": "Savoy Burn Spray", "quantity": "4", "parse_confidence": 0.96},
                {"raw_name": "Eye Wash Bottle", "quantity": "2", "parse_confidence": 0.96},
            ],
            "warnings": [],
            "meta": {"ai_mode": "vision"},
        }

        selected = prefer_safe_ai_preview(deterministic, ai_preview)

        self.assertIs(selected, ai_preview)
        self.assertEqual(
            [row["raw_name"] for row in selected["lines"]],
            ["Small First Aid Box", "Savoy Burn Spray", "Eye Wash Bottle"],
        )

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

    def test_manual_lpo_upload_detects_intermass_number_from_filename(self):
        details = _extract_lpo_details(
            {
                "source_filename": "PO_PO111_123301_0.pdf",
                "original_text": "",
                "lines": [],
                "meta": {},
            }
        )

        self.assertEqual(details["lpo_number"], "PO111_123301")

    @override_settings(QUOTATION_AI_PARSE_VISION_MODEL="test-vision-model")
    def test_gmail_pdf_attachment_uses_vision_in_auto_mode(self):
        mode = _select_mode(
            {
                "source_type": QuotationLPO.SOURCE_GMAIL,
                "source_filename": "customer-po.pdf",
                "source_file_ref": "inquiry_sources/customer-po.pdf",
            },
            requested_mode="auto",
            allow_vision=True,
            settings_obj=SimpleNamespace(ai_pdf_vision_enabled=True),
        )

        self.assertEqual(mode, AIParseCache.MODE_VISION)

    @override_settings(QUOTATION_AI_PARSE_VISION_MODEL="test-vision-model")
    def test_gmail_pdf_without_private_source_uses_text_in_auto_mode(self):
        mode = _select_mode(
            {
                "source_type": QuotationLPO.SOURCE_GMAIL,
                "source_filename": "customer-po.pdf",
                "source_file_ref": "gmail:message-only",
            },
            requested_mode="auto",
            allow_vision=True,
            settings_obj=SimpleNamespace(ai_pdf_vision_enabled=True),
        )

        self.assertEqual(mode, AIParseCache.MODE_TEXT)

    @override_settings(QUOTATION_AI_PARSE_VISION_MODEL="test-vision-model")
    @patch("quotations.ai_parsing._run_ai_cleanup", return_value={"lines": []})
    @patch(
        "quotations.ai_parsing._render_pdf_images",
        side_effect=AIParseError("Source PDF is not available in private storage."),
    )
    @patch("quotations.ai_parsing._assert_ai_allowed")
    @patch("quotations.ai_parsing.QuotationSettings.get_solo")
    def test_auto_vision_render_failure_falls_back_to_text(
        self,
        get_settings,
        _assert_allowed,
        _render,
        run_cleanup,
    ):
        get_settings.return_value = SimpleNamespace(ai_pdf_vision_enabled=True)

        clean_preview_with_ai(
            {
                "source_type": QuotationLPO.SOURCE_GMAIL,
                "source_filename": "customer-po.pdf",
                "source_file_ref": "inquiry_sources/customer-po.pdf",
                "meta": {},
                "lines": [],
            },
            requested_mode="auto",
            allow_vision=True,
        )

        self.assertEqual(run_cleanup.call_args.kwargs["mode"], AIParseCache.MODE_TEXT)
        self.assertEqual(run_cleanup.call_args.kwargs["images"], [])


class LPOOutcomeGuardRegressionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("lpo-parser-regression", is_staff=True)
        self.company = Company.objects.create(name="LPO Parser Regression Customer")
        self.quotation = Quotation.objects.create(company=self.company, created_by=self.user)

    def add_line(self, name, *, sort_order=0, quantity="1", unit_price="10", unit="No"):
        return QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot=name,
            quantity=Decimal(quantity),
            unit=unit,
            unit_price=Decimal(unit_price),
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

    def test_po_contact_and_delivery_metadata_are_rejected_before_item_matching(self):
        quoted_metadata_line = self.add_line("Phone")
        preview = {
            "lines": [
                {"raw_name": "Delivery Date : 08/07/2026", "quantity": "8"},
                {"raw_name": "Phone", "quantity": "42713695"},
                {"raw_name": "Phone 042487029", "quantity": "042487029"},
                {"raw_name": "Phone-42713695", "quantity": "42713695"},
                {"raw_name": "Phone - 42713695", "quantity": "42713695"},
                {"raw_name": "Delivery Date - 08/07/2026", "quantity": "8"},
                {"raw_name": "Payment Terms: 30 Days", "quantity": "30"},
            ]
        }

        suggestions, unmatched, missing = build_po_outcome_suggestions(self.quotation, preview)

        self.assertEqual(suggestions, [])
        self.assertEqual(
            [row["reason_code"] for row in unmatched],
            ["non_item_metadata"] * 7,
        )
        self.assertEqual(missing, [quoted_metadata_line.id])

    def test_hyphenated_item_names_near_metadata_labels_are_preserved(self):
        item_names = [
            "TEL - 40",
            "Contact - Lens Solution",
            "TRN - 500 Tablets",
            "Mobile - Toilet Chair",
            "Status - Cream",
        ]
        quote_lines = [self.add_line(item_name) for item_name in item_names]
        preview = {
            "lines": [
                {"raw_name": item_name, "quantity": "1"}
                for item_name in item_names
            ]
        }

        suggestions, unmatched, missing = build_po_outcome_suggestions(self.quotation, preview)

        self.assertEqual(
            {row["quotation_line_id"] for row in suggestions},
            {line.id for line in quote_lines},
        )
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

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

    def test_item_matching_ignores_name_separator_punctuation(self):
        quoted_line = self.add_line("MED-SAVOY BURN SPRAY")

        for po_name in (
            "Med - Savoy Burn Spray",
            "Med – Savoy Burn Spray",
            "Med—Savoy Burn Spray",
            "Med_Savoy Burn Spray",
            "Med/Savoy Burn Spray",
            "Med (Savoy) Burn Spray",
            "Med, Savoy: Burn Spray",
        ):
            with self.subTest(po_name=po_name):
                suggestions, unmatched, missing = build_po_outcome_suggestions(
                    self.quotation,
                    {"lines": [{"raw_name": po_name, "quantity": "6"}]},
                )

                self.assertEqual(len(suggestions), 1)
                self.assertEqual(suggestions[0]["quotation_line_id"], quoted_line.id)
                self.assertEqual(suggestions[0]["po_quantity"], "6")
                self.assertEqual(suggestions[0]["confidence"], 99)
                self.assertEqual(unmatched, [])
                self.assertEqual(missing, [])

    def test_item_matching_accepts_joined_or_split_compound_words(self):
        waterproof_line = self.add_line("dependa plaster water proof")
        betadine_line = self.add_line("Betadine Solution 500ML", sort_order=1)

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"raw_name": "Dependa Plaster Waterproof", "quantity": "1"},
                    {"raw_name": "Betadine Solution 500 ml", "quantity": "1"},
                ]
            },
        )

        self.assertEqual(len(suggestions), 2)
        self.assertEqual(
            {suggestion["quotation_line_id"] for suggestion in suggestions},
            {waterproof_line.id, betadine_line.id},
        )
        self.assertEqual({suggestion["confidence"] for suggestion in suggestions}, {99})
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_item_matching_accepts_ocr_split_millimetre_unit(self):
        quoted_line = self.add_line(
            "STEP UP STOOL SINGLE STEP 400X380X260M M SINGLE STEP"
        )

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Step Up Stool Single Step 400 x 380 x 260mm Single Step",
                        "quantity": "1",
                    }
                ]
            },
        )

        self.assertEqual([row["quotation_line_id"] for row in suggestions], [quoted_line.id])
        self.assertEqual([row["confidence"] for row in suggestions], [99])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_split_mm_repair_does_not_merge_separate_metre_dimensions(self):
        quoted_line = self.add_line("Safety Mat 5 m x 2 m")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Safety Mat 5 mm x 2 mm", "quantity": "1"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_split_mm_repair_does_not_merge_a_metre_and_size_marker(self):
        quoted_line = self.add_line("Cable 5m M")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Cable 5mm", "quantity": "1"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_item_matching_accepts_ocr_split_gram_unit_for_medication(self):
        quoted_line = self.add_line("DEEP HEAT OINTMENT 100G M", quantity="5", unit_price="18")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Deep Heat Ointment 100gm",
                        "quantity": "5",
                        "unit_price": "17.46",
                    }
                ]
            },
        )

        self.assertEqual([row["quotation_line_id"] for row in suggestions], [quoted_line.id])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_split_gm_repair_does_not_rewrite_unrelated_size_marker(self):
        quoted_line = self.add_line("Cream Colored Cable 100g M")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Cream Colored Cable 100gm", "quantity": "1"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_item_matching_accepts_ocr_space_inside_decimal_spec(self):
        quoted_line = self.add_line("GAUZE SWAB 7. 5CM", quantity="10", unit_price="10")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Gauze Swab 7.5cm",
                        "quantity": "10",
                        "unit_price": "9.70",
                    }
                ]
            },
        )

        self.assertEqual([row["quotation_line_id"] for row in suggestions], [quoted_line.id])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_dual_volume_quote_identity_does_not_match_one_volume(self):
        quoted_line = self.add_line("OPTREX EYE LOTION 110 ML (Omivis eye wash 100ml)")

        for po_name in ("Optrex Eye Lotion 110 ml", "Omivis Eye Wash 100ml"):
            with self.subTest(po_name=po_name):
                suggestions, unmatched, missing = build_po_outcome_suggestions(
                    self.quotation,
                    {"lines": [{"raw_name": po_name, "quantity": "1"}]},
                )

                self.assertEqual(suggestions, [])
                self.assertEqual(
                    [row["reason_code"] for row in unmatched],
                    ["specification_conflict"],
                )
                self.assertEqual(missing, [quoted_line.id])

    def test_identical_dual_specs_and_dimensions_can_still_match(self):
        dual_line = self.add_line("Medicine 500mg/125mg", sort_order=0)
        dimension_line = self.add_line("Gauze 5cm x 7.5cm", sort_order=1)

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"raw_name": "Medicine 500 mg / 125 mg", "quantity": "1"},
                    {"raw_name": "Gauze 5 cm x 7.5 cm", "quantity": "1"},
                ]
            },
        )

        self.assertEqual(
            {row["quotation_line_id"] for row in suggestions},
            {dual_line.id, dimension_line.id},
        )
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_x_separated_compound_strength_is_not_treated_as_a_dimension(self):
        quoted_line = self.add_line("Medicine 500mg x 125mg")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Medicine 500mg", "quantity": "1"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_quantity_breaks_an_exact_duplicate_name_tie(self):
        ordered_line = self.add_line("Betadine Dry Powder Spray", quantity="2")
        unselected_line = self.add_line("Betadine Dry Powder Spray", sort_order=1, quantity="5")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Betadine Dry Powder Spray", "quantity": "2"}]},
        )

        self.assertEqual([row["quotation_line_id"] for row in suggestions], [ordered_line.id])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [unselected_line.id])

    def test_price_breaks_an_exact_duplicate_name_tie_after_quantity(self):
        lower_price_line = self.add_line("Alcohol Pads", quantity="2", unit_price="10")
        ordered_line = self.add_line(
            "Alcohol Pads",
            sort_order=1,
            quantity="2",
            unit_price="12",
        )

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Alcohol Pads",
                        "quantity": "2",
                        "unit_price": "12",
                    }
                ]
            },
        )

        self.assertEqual([row["quotation_line_id"] for row in suggestions], [ordered_line.id])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [lower_price_line.id])

    def test_conflicting_duplicate_quantity_and_price_stay_ambiguous(self):
        quantity_line = self.add_line("Alcohol Pads", quantity="2", unit_price="10")
        price_line = self.add_line(
            "Alcohol Pads",
            sort_order=1,
            quantity="5",
            unit_price="12",
        )

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Alcohol Pads",
                        "quantity": "2",
                        "unit_price": "12",
                    }
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["ambiguous_match"])
        self.assertEqual(missing, [quantity_line.id, price_line.id])

    def test_quantity_never_breaks_a_tie_between_different_item_names(self):
        blue_line = self.add_line("Nitrile Gloves Medium Blue", quantity="1")
        black_line = self.add_line("Nitrile Gloves Medium Black", sort_order=1, quantity="2")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Nitrile Gloves Medium", "quantity": "2"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["ambiguous_match"])
        self.assertEqual(missing, [blue_line.id, black_line.id])

    def test_equal_duplicate_rows_are_assigned_one_to_one(self):
        first_line = self.add_line("Pickup Forceps", sort_order=0)
        second_line = self.add_line("Pickup Forceps", sort_order=1)

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"raw_name": "Pickup Forceps", "quantity": "1"},
                    {"raw_name": "Pickup Forceps", "quantity": "1"},
                ]
            },
        )

        self.assertEqual(
            [
                (row["po_row_number"], row["quotation_line_id"])
                for row in suggestions
            ],
            [(1, first_line.id), (2, second_line.id)],
        )
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_one_duplicate_row_against_two_quote_lines_stays_ambiguous(self):
        first_line = self.add_line("Pickup Forceps", sort_order=0)
        second_line = self.add_line("Pickup Forceps", sort_order=1)

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Pickup Forceps", "quantity": "1"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["ambiguous_match"])
        self.assertEqual(missing, [first_line.id, second_line.id])

    def test_two_duplicate_rows_against_three_quote_lines_stay_ambiguous(self):
        quote_lines = [
            self.add_line("Pickup Forceps", sort_order=index)
            for index in range(3)
        ]

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {"raw_name": "Pickup Forceps", "quantity": "1"},
                    {"raw_name": "Pickup Forceps", "quantity": "1"},
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(
            [row["reason_code"] for row in unmatched],
            ["ambiguous_match", "ambiguous_match"],
        )
        self.assertEqual(missing, [line.id for line in quote_lines])

    def test_duplicate_group_with_different_units_stays_ambiguous(self):
        box_line = self.add_line("Alcohol Pads", sort_order=0, unit="Box")
        each_line = self.add_line("Alcohol Pads", sort_order=1, unit="No")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Alcohol Pads",
                        "quantity": "1",
                        "unit_price": "10",
                        "unit": "No",
                    },
                    {
                        "raw_name": "Alcohol Pads",
                        "quantity": "1",
                        "unit_price": "10",
                        "unit": "Box",
                    },
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(
            [row["reason_code"] for row in unmatched],
            ["ambiguous_match", "ambiguous_match"],
        )
        self.assertEqual(missing, [box_line.id, each_line.id])

    def test_priced_item_raw_line_recovers_description_and_size(self):
        seven_line = self.add_line(
            "Conforming Gauze Bandage Pbt Confirming Dressing Bandage - 7.5 cm",
            sort_order=0,
            quantity="2",
            unit_price="0.80",
        )
        five_line = self.add_line(
            "Conforming Gauze Bandage Pbt Confirming Dressing Bandage - 5 cm",
            sort_order=1,
            quantity="2",
            unit_price="0.70",
        )
        knee_line = self.add_line(
            "Bandage Knee Bandage",
            sort_order=2,
            quantity="2",
            unit_price="22",
        )
        preview = {
            "lines": [
                {
                    "raw_name": "Conforming Gauze Bandage",
                    "raw_line": (
                        "6 | MED10050 | CONFORMING GAUZE BANDAGE | "
                        "PBT CONFIRMING DRESSING BANDAGE-7.5 CM | 2 | NUM | 0.78 | 1.55"
                    ),
                    "quantity": "2",
                    "unit_price": "0.78",
                },
                {
                    "raw_name": "Conforming Gauze Bandage",
                    "raw_line": (
                        "7 | MED10050 | CONFORMING GAUZE BANDAGE | "
                        "PBT CONFIRMING DRESSING BANDAGE - 5 CM | 2 | NUM | 0.68 | 1.36"
                    ),
                    "quantity": "2",
                    "unit_price": "0.68",
                },
                {
                    "raw_name": "Bandage",
                    "raw_line": "8 | MED10024 | BANDAGE | KNEE BANDAGE | 2 | PKT | 21.34 | 42.68",
                    "quantity": "2",
                    "unit_price": "21.34",
                },
            ]
        }

        suggestions, unmatched, missing = build_po_outcome_suggestions(self.quotation, preview)

        self.assertEqual(
            {row["quotation_line_id"] for row in suggestions},
            {seven_line.id, five_line.id, knee_line.id},
        )
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_raw_line_match_cannot_override_an_explicit_primary_spec_conflict(self):
        quoted_line = self.add_line("Sterile Gauze 5 cm x 5 m", quantity="2")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Sterile Gauze 10 cm x 5 m",
                        "raw_line": (
                            "1 | MED10050 | STERILE GAUZE 5 CM X 5 M | "
                            "2 | NUM | 10.00 | 20.00"
                        ),
                        "quantity": "2",
                        "unit_price": "10",
                    }
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_generic_product_alias_cannot_override_snapshot_spec_conflict(self):
        quoted_line = self.add_line("Sterile Gauze 5 cm", quantity="2")
        quoted_line.product = Product.objects.create(name="Sterile Gauze", price=Decimal("10"))
        quoted_line.save(update_fields=["product"])

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {"lines": [{"raw_name": "Sterile Gauze 10 cm", "quantity": "2"}]},
        )

        self.assertEqual(suggestions, [])
        self.assertEqual([row["reason_code"] for row in unmatched], ["specification_conflict"])
        self.assertEqual(missing, [quoted_line.id])

    def test_comment_raw_line_cannot_create_an_item_match(self):
        quoted_line = self.add_line("First Aid Box")

        suggestions, unmatched, missing = build_po_outcome_suggestions(
            self.quotation,
            {
                "lines": [
                    {
                        "raw_name": "Comments",
                        "raw_line": "Comments | First Aid Box | approved",
                        "quantity": "1",
                        "unit_price": "10",
                    },
                    {
                        "raw_name": "Remarks",
                        "raw_line": "Remarks | First Aid Box | approved | 1 | NUM | 10 | 10",
                        "quantity": "1",
                        "unit_price": "10",
                    },
                    {
                        "raw_name": "Notes",
                        "raw_line": "Notes | First Aid Box | approved | 1 | NUM | 10 | 10",
                        "quantity": "1",
                        "unit_price": "10",
                    },
                ]
            },
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(
            [row["reason_code"] for row in unmatched],
            ["non_item_metadata"] * 3,
        )
        self.assertEqual(missing, [quoted_line.id])

    def test_possessive_ocr_spelling_matches_plural_item_name(self):
        quoted_line = self.add_line("SURGICAL GLOVES (M SIZE)")

        for po_name in ("Surgical Glove's m Sizes", "Surgical Glove’s m Sizes"):
            with self.subTest(po_name=po_name):
                suggestions, unmatched, missing = build_po_outcome_suggestions(
                    self.quotation,
                    {"lines": [{"raw_name": po_name, "quantity": "1"}]},
                )

                self.assertEqual([row["quotation_line_id"] for row in suggestions], [quoted_line.id])
                self.assertEqual(unmatched, [])
                self.assertEqual(missing, [])

    def test_ai_cannot_reduce_strong_quotation_line_coverage(self):
        jacket_line = self.add_line("Fire Warden Jacket", sort_order=1)
        water_line = self.add_line("Small Drinking Water 500ml", sort_order=2)
        deterministic = {
            "lines": [
                {"raw_name": "Fire Warden Jacket", "quantity": "20"},
                {"raw_name": "Small Drinking Water 500ml", "quantity": "30"},
            ],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {
            "lines": [{"raw_name": "Fire Warden Jacket", "quantity": "20"}],
            "warnings": [],
            "meta": {},
        }

        selected, suggestions, unmatched, missing = build_guarded_po_outcome_suggestions(
            self.quotation,
            deterministic,
            ai_preview,
        )

        self.assertEqual({row["quotation_line_id"] for row in suggestions}, {jacket_line.id, water_line.id})
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])
        self.assertEqual(selected["lines"], deterministic["lines"])
        self.assertIn(AI_QUOTE_COVERAGE_GUARD_WARNING, selected["warnings"])
        self.assertEqual(
            selected["meta"]["ai_cleanup_rejection_reason"],
            "strong_quote_matches_removed_or_changed",
        )

    def test_ai_cannot_change_quantity_for_the_same_strong_quotation_match(self):
        jacket_line = self.add_line("Fire Warden Jacket")
        deterministic = {
            "lines": [
                {
                    "raw_name": "Fire Warden Jacket",
                    "quantity": "20",
                    "unit_price": "15",
                    "line_total": "300",
                }
            ],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {
            "lines": [
                {
                    "raw_name": "Fire Warden Jacket",
                    "quantity": "2",
                    "unit_price": "15",
                    "line_total": "30",
                }
            ],
            "warnings": [],
            "meta": {},
        }

        selected, suggestions, unmatched, missing = build_guarded_po_outcome_suggestions(
            self.quotation,
            deterministic,
            ai_preview,
        )

        self.assertEqual(suggestions[0]["quotation_line_id"], jacket_line.id)
        self.assertEqual(suggestions[0]["po_quantity"], "20")
        self.assertEqual(suggestions[0]["po_unit_price"], "15.00")
        self.assertEqual(selected["lines"], deterministic["lines"])
        self.assertEqual(unmatched, [])
        self.assertEqual(missing, [])

    def test_aggregate_summary_guard_wins_over_deterministic_suggestions(self):
        quote_line = self.add_line("Clinic Supplies")
        deterministic = {
            "lines": [{"raw_name": "Clinic Supplies", "quantity": "38"}],
            "warnings": [],
            "meta": {},
        }
        ai_preview = {
            "lines": [{"raw_name": "Clinic Supplies", "quantity": "38"}],
            "warnings": [
                "Aggregate PO item summary detected. Staff must review the source document manually."
            ],
            "meta": {},
        }

        selected, suggestions, unmatched, missing = build_guarded_po_outcome_suggestions(
            self.quotation,
            deterministic,
            ai_preview,
        )

        self.assertEqual(suggestions, [])
        self.assertEqual(unmatched[0]["reason_code"], "aggregate_summary")
        self.assertEqual(missing, [quote_line.id])
        self.assertTrue(selected["meta"]["aggregate_po_summary_detected"])
