from datetime import datetime
from io import BytesIO
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from openpyxl import Workbook
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
from rest_framework.test import APITestCase

from .ai_parsing import AI_PARSE_JSON_SCHEMA, _bind_result_source, _normalize_ai_result
from .import_parsers import parse_file_preview, parse_text_preview
from .models import AIParseCache, Company, InquiryLine


HEADERS = ["SN#", "Medication / Description", "Quantity", "Unit", "available expiry", "u price", "vat"]
ROWS = [
    [1, "EPIEN junior", 10, "each", "9/27", 290, 0],
    [2, "Sugar free strepsils", 5, "box", "08/28", 26, "5%"],
    [3, "Eye wash solution", 2, "each", "", 38, 0],
]


def excel_upload(*, dates=False, expiry=True):
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(HEADERS if expiry else ["Item", "Qty", "Unit"])
    for row in ROWS:
        sheet.append(row if expiry else row[1:4])
    if dates:
        sheet["E2"] = datetime(2027, 9, 1)
        sheet["E2"].number_format = "m/yy"
        sheet["E3"] = datetime(2028, 8, 31)
        sheet["E3"].number_format = "dd/mm/yyyy"
    buffer = BytesIO()
    workbook.save(buffer)
    return SimpleUploadedFile("expiry.xlsx", buffer.getvalue())


class InquiryExpiryParserTests(SimpleTestCase):
    def assert_rows(self, lines, dates=("9/27", "08/28", "")):
        self.assertEqual(len(lines), 3)
        self.assertEqual([line.get("expiry_date", "") for line in lines], list(dates))
        self.assertEqual([line["raw_name"].lower() for line in lines], [row[1].lower() for row in ROWS])
        self.assertEqual([line["quantity"] for line in lines], ["10", "5", "2"])
        self.assertEqual([line["unit_price"] for line in lines], ["290", "26", "38"])
        self.assertEqual(lines[1]["vat_rate"], "5")

    def test_excel_screenshot_layout_and_real_excel_dates(self):
        self.assert_rows(parse_file_preview(excel_upload(), store_source=False)["lines"])
        self.assert_rows(parse_file_preview(excel_upload(dates=True), store_source=False)["lines"], ("09/2027", "31/08/2028", ""))

    def test_tab_paste_and_html_keep_expiry_separate_from_price(self):
        text = "\n".join("\t".join(map(str, row)) for row in [HEADERS, *ROWS])
        self.assert_rows(parse_text_preview(text)["lines"])
        html = "<table>" + "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in [HEADERS, *ROWS]) + "</table>"
        self.assert_rows(parse_text_preview(text, raw_html=html)["lines"])

    def test_pdf_table_with_expiry(self):
        buffer = BytesIO()
        table = Table([["No", "Description", "Qty", "Unit", "Expiry date", "Price", "VAT"], *ROWS], colWidths=[25, 140, 35, 35, 75, 45, 35])
        table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.5, colors.black)]))
        SimpleDocTemplate(buffer).build([table])
        preview = parse_file_preview(SimpleUploadedFile("expiry.pdf", buffer.getvalue()), store_source=False)
        self.assert_rows(preview["lines"])

    def test_no_expiry_column_does_not_guess_from_other_dates(self):
        text = "Quotation date: 03/10/2026\nValid until: 03/11/2026\nItem\tQty\tUnit\nGauze\t3\tbox"
        lines = parse_text_preview(text)["lines"]
        self.assertEqual(len(lines), 1)
        self.assertFalse(lines[0].get("expiry_date"))

    def test_ai_text_and_vision_return_expiry_and_keep_blank_values(self):
        schema = AI_PARSE_JSON_SCHEMA["properties"]["rows"]["items"]
        self.assertIn("expiry_date", schema["required"])
        for mode in (AIParseCache.MODE_TEXT, AIParseCache.MODE_VISION):
            with self.subTest(mode=mode):
                result = _normalize_ai_result({"rows": [
                    {"item_name": "EPIEN junior", "quantity": "10", "unit": "each", "expiry_date": "9/27", "unit_price": "290", "parse_status": "parsed", "confidence": 99},
                    {"item_name": "Eye wash solution", "quantity": "2", "unit": "each", "parse_status": "parsed", "confidence": 99},
                ]}, preview={}, mode=mode, provider="test", model="test", output_style="inquiry")
                self.assertEqual([row["expiry_date"] for row in result["lines"]], ["9/27", ""])

    def test_ai_cleanup_cannot_drop_swap_or_invent_structured_expiry(self):
        preview = parse_file_preview(excel_upload(), store_source=False)
        candidate = {"lines": [dict(line, expiry_date="01/2099") for line in reversed(preview["lines"])]}
        rebound = _bind_result_source(candidate, preview)
        self.assertEqual([line["expiry_date"] for line in rebound["lines"]], ["", "08/28", "9/27"])
        self.assertEqual(candidate["lines"][0]["expiry_date"], "01/2099")
        missing = _bind_result_source({"lines": candidate["lines"][:1]}, preview)
        self.assertEqual(missing["lines"], preview["lines"])
        self.assertEqual(missing["meta"]["ai_cleanup_rejection_reason"], "expiry_evidence_unmapped")


@override_settings(QUOTATION_AI_PARSE_GLOBAL_ENABLED=False)
class InquiryExpiryWorkflowTests(APITestCase):
    def setUp(self):
        self.staff = get_user_model().objects.create_user(username="expiry_import_staff", is_staff=True)
        self.company = Company.objects.create(name="Expiry importer")
        self.client.force_authenticate(self.staff)
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        settings = override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=temp.name)
        settings.enable()
        self.addCleanup(settings.disable)

    def import_and_quote(self, *, expiry=True, clear=False):
        parsed = self.client.post(reverse("quotation-inquiry-parse-file"), {"file": excel_upload(expiry=expiry)}, format="multipart")
        self.assertEqual(parsed.status_code, 200, parsed.data)
        lines = parsed.data["lines"]
        # Mirror the review screen's submission of detected source pricing.
        for row in lines:
            row["unit_price"] = row.get("customer_unit_price") or None
            row["vat_rate"] = row.get("customer_vat_rate") or "0"
        if clear:
            for row in lines:
                row["expiry_date"] = ""
        payload = {"company": self.company.pk, "source_type": "excel", "lines": lines}
        created = self.client.post(reverse("quotation-inquiry-create-imported"), payload, format="json")
        self.assertEqual(created.status_code, 201, created.data)
        quote = self.client.post(reverse("quotation-inquiry-create-quote", args=[created.data["id"]]))
        self.assertEqual(quote.status_code, 201, quote.data)
        return created.data, quote.data

    def test_uploaded_expiry_persists_through_inquiry_into_quotation(self):
        inquiry, quote = self.import_and_quote()
        self.assertEqual([row["expiry_date"] for row in inquiry["lines"]], ["9/27", "08/28", ""])
        self.assertEqual([row["expiry_date"] for row in quote["lines"]], ["9/27", "08/28", ""])
        self.assertTrue(quote["show_expiry_column"])
        self.assertEqual(InquiryLine.objects.filter(expiry_date="9/27").count(), 1)
        retry = self.client.post(reverse("quotation-inquiry-create-quote", args=[inquiry["id"]]))
        self.assertEqual(retry.data["id"], quote["id"])

    def test_missing_or_cleared_expiry_keeps_quotation_option_off(self):
        for options in ({"expiry": False}, {"clear": True}):
            with self.subTest(options=options):
                _, quote = self.import_and_quote(**options)
                self.assertFalse(quote["show_expiry_column"])
                self.assertTrue(all(not row["expiry_date"] for row in quote["lines"]))
