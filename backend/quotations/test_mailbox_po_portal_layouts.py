from decimal import Decimal

from django.test import SimpleTestCase

from quotations.mailbox_po_reconciliation import (
    _al_sahel_order_rows,
    _dubai_holding_order_rows,
    _document_total,
    _ecc_order_rows,
    _emrill_order_rows,
    _hotel_procurement_order_rows,
    _khansaheb_order_rows,
    _raq_order_rows,
)


class MailboxPOPortalLayoutTests(SimpleTestCase):
    def test_split_currency_total_labels_are_extracted(self):
        cases = {
            "khansaheb": ("Total AED\n1,770.00", Decimal("1770.00")),
            "hotel": ("Total amount due:\nAED1,249.50", Decimal("1249.50")),
            "net": ("Net Amount AED\nAED 622.00", Decimal("622.00")),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(_document_total({}, text), expected)

    def test_same_line_total_labels_are_extracted(self):
        cases = {
            "hotel": ("Total amount due: AED 1,249.50", Decimal("1249.50")),
            "net": ("Net Amount AED 622.00", Decimal("622.00")),
        }
        for name, (text, expected) in cases.items():
            with self.subTest(name=name):
                self.assertEqual(_document_total({}, text), expected)

    def test_hotel_vertical_cells_parse_every_commercial_row(self):
        text = "\n".join(
            [
                "PURCHASE ORDER",
                "BVLGARI RESORT DUBAI",
                "# Item",
                "Product Desc.",
                "Qty Unit",
                "Unit price",
                "Extension",
                "Tax",
                "Amount",
                "Department: Human Resources",
                "1",
                "BURNSPRAY *",
                "MED-SAVOY BURN SPRAY",
                "10.00 EA",
                "AED11.0000",
                "AED110.00",
                "AED0.00",
                "EARE0",
                "AED110.00",
                "2 Tablet *",
                "MED-CLARINASE",
                "3.00 EA",
                "AED28.0000",
                "AED84.00",
                "AED0.00",
                "EARE0",
                "AED84.00",
                "* - Non catalog item",
                "Sub Total:",
                "AED194.00",
            ]
        )

        recognized, rows, warnings = _hotel_procurement_order_rows(
            text, source="hotel.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual([row.name for row in rows], ["MED-SAVOY BURN SPRAY", "MED-CLARINASE"])
        self.assertEqual(rows[0].quantity, Decimal("10.00"))
        self.assertEqual(rows[0].unit_price, Decimal("11.0000"))
        self.assertEqual(rows[1].line_total, Decimal("84.00"))

    def test_hotel_layout_without_tax_columns_does_not_skip_the_next_row(self):
        text = "\n".join(
            [
                "PURCHASE ORDER",
                "BVLGARI RESORT DUBAI",
                "# Item",
                "Product Desc.",
                "Qty Unit",
                "Unit price",
                "Extension",
                "Department: Human Resources",
                "1 FIRST *",
                "Burn Spray",
                "2.00 EA",
                "AED11.00",
                "AED22.00",
                "2 SECOND *",
                "Cold Pack",
                "3.00 EA",
                "AED8.00",
                "AED24.00",
                "* - Non catalog item",
            ]
        )

        recognized, rows, warnings = _hotel_procurement_order_rows(
            text, source="compact-hotel.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual([row.name for row in rows], ["Burn Spray", "Cold Pack"])
        self.assertEqual(rows[1].line_total, Decimal("24.00"))

    def test_integer_hotel_values_are_not_counted_as_extra_item_rows(self):
        text = "\n".join(
            [
                "PURCHASE ORDER",
                "BVLGARI RESORT DUBAI",
                "# Item",
                "Product Desc.",
                "Qty Unit",
                "Unit price",
                "Extension",
                "Department: Human Resources",
                "1 FIRST *",
                "Burn Spray",
                "2 EA",
                "11",
                "22",
                "2 SECOND *",
                "Cold Pack",
                "3 EA",
                "8",
                "24",
                "* - Non catalog item",
            ]
        )

        recognized, rows, warnings = _hotel_procurement_order_rows(
            text, source="integer-hotel.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(len(rows), 2)
        self.assertEqual(warnings, ())

    def test_raq_visual_columns_parse_partial_po_rows(self):
        text = "\n".join(
            [
                "Purchase Order",
                "Unit Price",
                "Description",
                "#",
                "Unit",
                "Quantity",
                "Total Price",
                "1",
                "7.00",
                "BANDAGE 10CMX5YARDS",
                "NO.S",
                "2.00",
                "3.50",
                "Bandage Crepe 10cm",
                "2",
                "20.00",
                "FIRST AID ITEMS",
                "NO",
                "10.00",
                "2.00",
                "Bio Hazard Bags Red",
                "Powered by Sanisoft Information Technologies.",
            ]
        )

        recognized, rows, warnings = _raq_order_rows(text, source="raq.pdf")

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 2)
        self.assertIn("Bandage Crepe 10cm", rows[0].name)
        self.assertEqual(rows[1].name, "Bio Hazard Bags Red")
        self.assertEqual(rows[1].quantity, Decimal("10.00"))
        self.assertEqual(rows[1].line_total, Decimal("20.00"))

    def test_raq_malformed_middle_row_emits_incomplete_warning(self):
        text = "\n".join(
            [
                "Purchase Order",
                "Unit Price",
                "Description",
                "#",
                "Unit",
                "Quantity",
                "Total Price",
                "1",
                "7.00",
                "Bandage",
                "NO.S",
                "2.00",
                "3.50",
                "2",
                "20.00",
                "Broken row",
                "???",
                "10.00",
                "2.00",
                "3",
                "12.00",
                "Cold Pack",
                "NO",
                "2.00",
                "6.00",
                "Powered by Sanisoft Information Technologies.",
            ]
        )

        recognized, rows, warnings = _raq_order_rows(text, source="raq-malformed.pdf")

        self.assertTrue(recognized)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("incomplete" in warning for warning in warnings))

    def test_raq_value_before_net_amount_label_is_extracted(self):
        text = "\n".join(
            [
                "Purchase Order",
                "840.00",
                "Net Amount (AED) :",
                "Total (In Words): AED Eight Hundred Forty Only",
                "Powered by Sanisoft Information Technologies.",
            ]
        )

        self.assertEqual(_document_total({}, text), Decimal("840.00"))

    def test_khansaheb_split_cells_keep_each_calibration_line(self):
        text = "\n".join(
            [
                "Purchase Order",
                "Khansaheb Civil Engineering L.L.C.",
                "S. N.",
                "Commodity Code",
                "Description",
                "Quantity",
                "UOM",
                "Unit Price",
                "Disc%",
                "Total",
                "Calibration of First Aid Room Equipment",
                "1",
                "61001A01",
                "Blood pressure monitor-calibration",
                "2.000",
                "NR",
                "190.00",
                "0.00",
                "380.00",
                "2",
                "61001A01",
                "Weighing Scale with Height Measuring Rod -",
                "calibration",
                "1.000",
                "NR",
                "250.00",
                "0.00",
                "250.00",
                "Delivery Contact: Amarnath",
            ]
        )

        recognized, rows, warnings = _khansaheb_order_rows(
            text, source="khansaheb.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].quantity, Decimal("2.000"))
        self.assertIn("Weighing Scale", rows[1].name)
        self.assertEqual(rows[1].line_total, Decimal("250.00"))

    def test_dubai_holding_schedule_ignores_page_and_contract_text(self):
        text = "\n".join(
            [
                "Purchase Order: MJR-PO-00943941",
                "Purchase Order",
                "SCHEDULE OF DETAILS",
                "#",
                "Item Description",
                "Delivery",
                "Date",
                "UOM",
                "Qty",
                "Unit Price",
                "Amount",
                "Tax Rate (%)",
                "Line Total",
                "1",
                "Gloves 'Nitrile Examination Gloves' Powder Free Large",
                "Product Code:",
                "24-JUN-2026",
                "BOX",
                "10",
                "18.00",
                "180.00",
                "5.00",
                "189.00",
                "Attachments:",
                "Page 3 of 8",
            ]
        )

        recognized, rows, warnings = _dubai_holding_order_rows(
            text, source="mjr.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantity, Decimal("10"))
        self.assertEqual(rows[0].unit_price, Decimal("18.00"))
        self.assertEqual(rows[0].line_total, Decimal("180.00"))

    def test_ecc_reordered_summary_uses_only_arithmetically_valid_grand_total(self):
        text = "\n".join(
            [
                "Engineering Contracting Co. LLC.",
                "PURCHASE ORDER",
                "841.50",
                "832.00",
                "Gross Total",
                "Discount",
                "Net Total",
                "0.00",
                "9.50",
                "VAT",
                "841.50",
            ]
        )

        self.assertEqual(_document_total({}, text), Decimal("841.50"))
        self.assertIsNone(
            _document_total({}, text.replace("841.50\n832.00", "840.00\n832.00", 1))
        )

    def test_ambiguous_split_total_sequence_fails_closed(self):
        self.assertIsNone(_document_total({}, "Net Total\n832.00\n0.00\n9.50"))

    def test_ecc_reordered_cells_parse_partial_order(self):
        text = "\n".join(
            [
                "Engineering Contracting Co. LLC.",
                "PURCHASE ORDER",
                "Resource Name/Description",
                "Quantity",
                "Unit Price",
                "6.000",
                "30.00",
                "1",
                "5.00",
                "5.00",
                "1005020010071",
                "CALAMINE LOTION 100 - ML.",
                "NOS",
                "10.000",
                "80.00",
                "2",
                "5.00",
                "8.00",
                "1005020010169",
                "DEEP HEAT RUB CREAM 67 GM.",
                "NOS",
            ]
        )

        recognized, rows, warnings = _ecc_order_rows(text, source="ecc.pdf")

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].quantity, Decimal("5.00"))
        self.assertEqual(rows[0].unit_price, Decimal("6.000"))
        self.assertEqual(rows[1].line_total, Decimal("80.00"))

    def test_al_sahel_layout_parses_reduced_and_repriced_line(self):
        text = "\n".join(
            [
                "Al Sahel Contracting Company L.L.C",
                "Item Code",
                "Qty.",
                "Unit",
                "LOCAL PURCHASE ORDER",
                "01",
                "14980308",
                "MACHINE-AUTOMATED EXTERNAL",
                "DEFIBRILLATORS-HEARTPLUS KOREA - ONE YEAR WARRANTY",
                "NO",
                "7.00",
                "22,400.00",
                "3,200.000",
                "****END****",
            ]
        )

        recognized, rows, warnings = _al_sahel_order_rows(
            text, source="al-sahel.pdf"
        )

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].quantity, Decimal("7.00"))
        self.assertEqual(rows[0].unit_price, Decimal("3200.000"))
        self.assertEqual(rows[0].line_total, Decimal("22400.00"))

    def test_emrill_reference_preamble_does_not_replace_item_name(self):
        text = "\n".join(
            [
                "Emrill Services LLC",
                "Purchase Order",
                "Line number",
                "Unit price",
                "Amount Delivery",
                "LS",
                "2.00",
                "1450.00",
                "0.00",
                "5.00%",
                "145.00",
                "2900.00 06/07/2026",
                "Description: QTN NO: 260513",
                "Fire staircase evacuation chair",
                "Quantity: 02",
                "Amount: 2900",
                "WareHouse: 066",
            ]
        )

        recognized, rows, warnings = _emrill_order_rows(text, source="emrill.pdf")

        self.assertTrue(recognized)
        self.assertEqual(warnings, ())
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].name, "Fire staircase evacuation chair")
        self.assertEqual(rows[0].description, "QTN NO: 260513")
        self.assertEqual(rows[0].quantity, Decimal("2.00"))
