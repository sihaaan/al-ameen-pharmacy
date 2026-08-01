from copy import deepcopy
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from .models import (
    Company,
    ProformaInvoice,
    Quotation,
    QuotationLPO,
    QuotationLine,
    QuotationOutcomePOImport,
)


User = get_user_model()


INSPECTION_META = {
    "attachment_safety": {
        "validated_format": "xlsx",
        "hard_limits_applied": True,
    },
    "spreadsheet_fidelity": {
        "formula_cell_count": 2,
        "hidden_sheet_count": 1,
    },
    "pdf_fidelity": {
        "form_field_markers": False,
    },
}

PARSER_WARNINGS = [
    "Workbook contains formula cells.",
    "Workbook contains hidden sheets, rows, or columns.",
    "Workbook contains merged cells.",
    "Stopped reading sheet 'Items' after 500 rows.",
    "Stopped reading columns in sheet 'Items' after 100 columns.",
]


def parsed_preview():
    return {
        "source_type": "excel",
        "source_filename": "customer-lpo.xlsx",
        "source_mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "source_sha256": "a" * 64,
        "source_file_ref": "inquiry_sources/2026/08/01/customer-lpo.xlsx",
        "source_file_size": 1234,
        "parse_method": "openpyxl_structured_v2",
        "original_text": "Purchase Order No: LPO-900 Date: 31/07/2026",
        "warnings": list(PARSER_WARNINGS),
        "meta": {
            **deepcopy(INSPECTION_META),
            "lpo_number": "LPO-900",
            "lpo_date": "2026-07-31",
        },
        "lines": [
            {
                "raw_name": "Bandage Pack",
                "raw_line": "Bandage Pack | 2 | box | 10.00",
                "quantity": "2",
                "unit": "box",
                "unit_price": "10.00",
                "line_total": "20.00",
                "parse_status": "parsed",
                "parse_confidence": 0.95,
            }
        ],
    }


class AttachmentInspectionMetaRetentionTests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="attachment-meta-staff",
            password="pass",
            is_staff=True,
        )
        self.company = Company.objects.create(name="Attachment Meta Customer")
        self.quotation = Quotation.objects.create(
            company=self.company,
            created_by=self.staff,
            status=Quotation.STATUS_APPROVED,
        )
        QuotationLine.objects.create(
            quotation=self.quotation,
            item_name_snapshot="Bandage Pack",
            quantity=Decimal("2.000"),
            unit="box",
            unit_price=Decimal("10.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        self.client.force_authenticate(self.staff)

    def upload(self):
        return SimpleUploadedFile(
            "customer-lpo.xlsx",
            b"parser-is-mocked",
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @patch("quotations.views.clean_preview_with_ai")
    @patch("quotations.views.parse_file_preview")
    def test_quotation_lpo_keeps_all_warnings_and_structured_inspection_meta(
        self,
        parse_file,
        clean_preview,
    ):
        parse_file.return_value = parsed_preview()
        ai_preview = parsed_preview()
        ai_preview["meta"] = {"ai_provider": "test-provider"}
        ai_preview["warnings"] = []
        clean_preview.return_value = ai_preview

        response = self.client.post(
            reverse("quotation-upload-lpo", args=[self.quotation.id]),
            {"file": self.upload(), "use_ai": "true"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        lpo = QuotationLPO.objects.get(quotation=self.quotation)
        for key, value in INSPECTION_META.items():
            self.assertEqual(lpo.parsed_meta[key], value)
            self.assertEqual(response.data["lpo"]["parsed_meta"][key], value)
        self.assertEqual(lpo.warnings, PARSER_WARNINGS)
        self.assertIn("Stopped reading sheet 'Items'", " ".join(response.data["lpo"]["warnings"]))

    @patch("quotations.views.clean_preview_with_ai")
    @patch("quotations.views.parse_file_preview")
    def test_proforma_keeps_all_warnings_and_structured_inspection_meta(
        self,
        parse_file,
        clean_preview,
    ):
        parse_file.return_value = parsed_preview()
        ai_preview = parsed_preview()
        ai_preview["meta"] = {"ai_provider": "test-provider"}
        ai_preview["warnings"] = []
        clean_preview.return_value = ai_preview
        proforma = ProformaInvoice.objects.create(
            company=self.company,
            created_by=self.staff,
        )

        response = self.client.post(
            reverse("quotation-standalone-proforma-upload-lpo", args=[proforma.id]),
            {"file": self.upload(), "use_ai": "true"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        proforma.refresh_from_db()
        for key, value in INSPECTION_META.items():
            self.assertEqual(proforma.parsed_meta[key], value)
            self.assertEqual(response.data["proforma"]["parsed_meta"][key], value)
        self.assertEqual(proforma.warnings, PARSER_WARNINGS)

    @patch("quotations.views.clean_preview_with_ai")
    @patch("quotations.views.parse_file_preview")
    def test_outcome_import_keeps_every_parser_warning_and_meta_with_ai(
        self,
        parse_file,
        clean_preview,
    ):
        parse_file.return_value = parsed_preview()
        ai_preview = parsed_preview()
        ai_preview["meta"] = {"ai_provider": "test-provider"}
        ai_preview["warnings"] = []
        clean_preview.return_value = ai_preview
        self.quotation.status = Quotation.STATUS_FINALIZED
        self.quotation.save(update_fields=["status", "updated_at"])

        response = self.client.post(
            reverse("quotation-parse-outcome-po", args=[self.quotation.id]),
            {"file": self.upload(), "use_ai": "true"},
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        po_import = QuotationOutcomePOImport.objects.get(pk=response.data["id"])
        self.assertEqual(po_import.warnings, PARSER_WARNINGS)
        self.assertEqual(response.data["warnings"], PARSER_WARNINGS)
        self.assertEqual(po_import.parsed_meta, INSPECTION_META)
        self.assertEqual(response.data["parsed_meta"], INSPECTION_META)
