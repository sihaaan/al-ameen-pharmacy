from decimal import Decimal
from io import BytesIO

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.units import mm

from .models import Company, ProformaInvoice, ProformaInvoiceLine, Quotation, QuotationLine
from .pdf import (
    _number,
    _pdf_styles,
    _single_line_table_cell,
    build_proforma_invoice_pdf,
    build_quotation_pdf,
    build_standalone_proforma_invoice_pdf,
)


class PdfQuantityFormattingTests(SimpleTestCase):
    def test_number_hides_only_insignificant_decimal_places(self):
        cases = [
            (None, "-"),
            (Decimal("0.000"), "0"),
            (Decimal("50.000"), "50"),
            (Decimal("1000.000"), "1000"),
            (Decimal("1000.125"), "1000.125"),
            (Decimal("1.230"), "1.23"),
            (Decimal("0.125"), "0.125"),
        ]

        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(_number(value), expected)

    def test_single_line_cells_measure_and_shrink_to_their_actual_inner_width(self):
        styles = _pdf_styles(colors.HexColor("#0F766E"))
        cases = [
            ("999999999.999", styles["TableCellQuantity"], 16 * mm - 10, "RIGHT"),
            ("InternationalUnitsPerAmpouleContainerPackaging", styles["TableCellUnit"], 18 * mm - 10, "CENTER"),
        ]

        for value, style, available_width, alignment in cases:
            with self.subTest(value=value):
                cell = _single_line_table_cell(value, style, h_align=alignment)
                width, height = cell.wrap(available_width, 100)

                self.assertEqual(width, available_width)
                self.assertLessEqual(height, style.leading)
                self.assertLess(cell.draw_font_size, style.fontSize)
                self.assertLessEqual(cell.rendered_text_width, available_width + 0.001)


class QuotationPdfDisplayTests(TestCase):
    def pdf_text(self, pdf_bytes):
        reader = PdfReader(BytesIO(pdf_bytes))
        return "\n".join(page.extract_text() or "" for page in reader.pages)

    def assert_compact_values(self, text):
        self.assertIn("1000", text)
        self.assertIn("1000.125", text)
        self.assertIn("0.125", text)
        self.assertIn("Ampoules", text)
        self.assertIn("Suppositories", text)
        self.assertIn("Each/Ampoule", text)
        self.assertIn("999999999.999", text)
        self.assertIn("InternationalUnitsPerAmpouleContainerPackaging", text)
        self.assertNotIn("1000.000", text)
        self.assertNotIn("1000.00\n0", text)
        self.assertNotIn("1000.\n125", text)
        self.assertNotIn("Ampoule\ns", text)
        self.assertNotIn("Suppositorie\ns", text)
        self.assertNotIn("Each/\nAmpoule", text)
        self.assertNotIn("999999999.\n999", text)
        self.assertNotIn("InternationalUnitsPerAmpoule\nContainerPackaging", text)

    def test_pdf_keeps_compact_quantities_and_common_units_on_one_line(self):
        user = get_user_model().objects.create_user(username="pdf_display_staff", is_staff=True)
        company = Company.objects.create(name="PDF Display Company")
        quotation = Quotation.objects.create(company=company, created_by=user)
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Ammonia Inhalant - Bottle",
            quantity=Decimal("1000.000"),
            unit="Ampoules",
            unit_price=Decimal("1.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=1,
        )
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Measured Liquid",
            quantity=Decimal("0.125"),
            unit="Bottles",
            unit_price=Decimal("8.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=2,
        )
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Specialist Rectal Product",
            quantity=Decimal("1000.125"),
            unit="Suppositories",
            unit_price=Decimal("2.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=3,
        )
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Combined Dispensing Unit",
            quantity=Decimal("12.000"),
            unit="Each/Ampoule",
            unit_price=Decimal("3.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=4,
        )
        QuotationLine.objects.create(
            quotation=quotation,
            item_name_snapshot="Maximum Quantity and Long Unit",
            quantity=Decimal("999999999.999"),
            unit="InternationalUnitsPerAmpouleContainerPackaging",
            unit_price=Decimal("0.01"),
            match_status=QuotationLine.MATCH_CONFIRMED,
            sort_order=5,
        )

        quotation_text = self.pdf_text(build_quotation_pdf(quotation))
        quotation_proforma_text = self.pdf_text(build_proforma_invoice_pdf(quotation))

        self.assert_compact_values(quotation_text)
        self.assert_compact_values(quotation_proforma_text)

        proforma = ProformaInvoice.objects.create(company=company, created_by=user)
        for index, line in enumerate(quotation.lines.order_by("sort_order", "id")):
            ProformaInvoiceLine.objects.create(
                proforma=proforma,
                item_name=line.item_name_snapshot,
                quantity=line.quantity,
                unit=line.unit,
                unit_price=line.unit_price,
                sort_order=index,
            )
        standalone_text = self.pdf_text(build_standalone_proforma_invoice_pdf(proforma))
        self.assert_compact_values(standalone_text)
