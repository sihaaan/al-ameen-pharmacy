from copy import deepcopy
from decimal import Decimal
from io import BytesIO
from unittest.mock import patch
from tempfile import TemporaryDirectory

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse
from pypdf import PdfReader
from rest_framework.test import APIClient

from .models import Company, Quotation, QuotationLine, QuotationSettings, TaxInvoice, TaxInvoiceSequence
from .pdf import build_quotation_pdf
from .test_delivery_lpo import preview


@override_settings(QUOTATION_AI_PARSE_GLOBAL_ENABLED=False, QUOTATION_PDF_ALLOW_REMOTE_IMAGES=False)
class TaxInvoiceTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user("invoice-staff", is_staff=True)
        self.company = Company.objects.create(name="Resort LLC", billing_address="Dubai, UAE")
        QuotationSettings.objects.create(pk=1, company_name="Al Ameen Pharmacy LLC", address="Dubai, UAE", trn="100064879800003", show_trn=False)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)
        self.next_number = 604670
        self.payload = {"company": self.company.pk, "customer_name": self.company.name,
                        "customer_trn": "105416477500003",
                        "customer_address": "Dubai, UAE", "invoice_date": "2026-09-22", "supply_date": "2026-09-21",
                        "quotation_reference": "QT-123", "lpo_number": "PO112_112353", "currency": "AED",
                        "lines": [{"item_name": "Gauze", "quantity": "3", "unit": "Box", "unit_price": "12.555", "vat_rate": "5", "discount": "2"}]}

    def url(self, action, pk=None):
        return reverse(f"quotation-tax-invoice-{action}", kwargs={"pk": pk} if pk else None)

    def draft(self, payload=None):
        payload = {"invoice_number": str(self.next_number), **(payload or self.payload)}
        self.next_number += 1
        response = self.client.post(self.url("list"), payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def issue(self, draft):
        return self.client.post(self.url("issue", draft["id"]), {"expected_revision": draft["revision"]}, format="json")

    def test_manual_draft_calculates_discount_then_vat_with_half_up_rounding(self):
        draft = self.draft()
        self.assertEqual(draft["subtotal"], "35.67")
        self.assertEqual(draft["discount_total"], "2.00")
        self.assertEqual(draft["vat_total"], "1.78")
        self.assertEqual(draft["total"], "37.45")
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(draft["invoice_number"], "604670")

    def test_preview_does_not_issue_and_issued_pdf_preserves_supplier_and_customer(self):
        draft = self.draft()
        draft_pdf = self.client.get(self.url("pdf", draft["id"]))
        self.assertEqual(draft_pdf.status_code, 200)
        self.assertEqual(TaxInvoice.objects.get(pk=draft["id"]).status, "draft")
        self.assertIn("DRAFT - NOT ISSUED", " ".join(PdfReader(BytesIO(draft_pdf.content)).pages[0].extract_text().split()))
        issued = self.issue(draft)
        self.assertEqual(issued.status_code, 200, issued.data)
        self.assertEqual(issued.data["invoice_number"], "604670")
        pdf = self.client.get(self.url("pdf", draft["id"]))
        text = " ".join("\n".join(page.extract_text() for page in PdfReader(BytesIO(pdf.content)).pages).split())
        for value in ["TAX INVOICE", "100064879800003", "105416477500003", "604670", "PO112_112353", "12.555", "37.45", "21/09/2026", "Resort LLC",
                      "RAK Bank", "AL AMEEN PHARMACY LLC", "0025346405061", "AE440400000025346405061", 'Cheques payable to "AL AMEEN PHARMACY LLC".']:
            self.assertIn(value, text)
        self.assertNotIn("DRAFT", text)
        self.company.name = "Changed customer"
        self.company.save()
        QuotationSettings.objects.filter(pk=1).update(trn="999999999999999")
        with patch("quotations.tax_invoice_pdf.BANK_DETAILS", {}):
            self.assertEqual(self.client.get(self.url("pdf", draft["id"])).content, pdf.content)
        self.assertEqual(TaxInvoice.objects.get(pk=draft["id"]).supplier_snapshot["bank_details"]["account_number"], "0025346405061")
        repeat = self.issue(draft)
        self.assertEqual(repeat.data["invoice_number"], issued.data["invoice_number"])
        self.assertFalse(TaxInvoiceSequence.objects.exists())

    def test_issued_invoice_cannot_be_edited_deleted_or_status_spoofed(self):
        payload = {**self.payload, "status": "issued", "invoice_number": "FAKE", "total": "1"}
        draft = self.draft(payload)
        self.assertEqual(draft["status"], "draft")
        self.assertEqual(self.issue(draft).status_code, 200)
        response = self.client.patch(self.url("detail", draft["id"]), {"notes": "overwrite", "expected_revision": 1}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.delete(self.url("detail", draft["id"])).status_code, 405)

    def test_stale_window_cannot_overwrite_or_issue_a_newer_draft(self):
        draft = self.draft()
        first = self.client.patch(self.url("detail", draft["id"]), {"notes": "First edit", "expected_revision": 1}, format="json")
        self.assertEqual(first.status_code, 200, first.data)
        stale = self.client.put(self.url("detail", draft["id"]), {**self.payload, "expected_revision": 1}, format="json")
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(self.issue(draft).status_code, 400)
        self.assertEqual(TaxInvoice.objects.get(pk=draft["id"]).notes, "First edit")

    def test_incomplete_prices_and_vat_can_be_saved_but_not_issued(self):
        payload = deepcopy(self.payload)
        payload["lines"][0].update(unit_price=None, vat_rate=None, discount="0")
        draft = self.draft(payload)
        self.assertEqual(self.issue(draft).status_code, 400)
        self.assertFalse(TaxInvoiceSequence.objects.exists())

    def test_supplier_trn_and_customer_address_are_required_for_issue(self):
        draft = self.draft({**self.payload, "customer_address": ""})
        self.assertEqual(self.issue(draft).status_code, 400)
        draft = self.draft()
        QuotationSettings.objects.filter(pk=1).update(trn="invalid")
        self.assertEqual(self.issue(draft).status_code, 400)

    def test_invalid_line_update_is_atomic(self):
        draft = self.draft()
        for change in [{"quantity": "0"}, {"unit_price": "-1"}, {"vat_rate": "100"}, {"discount": "999"}, {"unit_price": "NaN"}]:
            payload = deepcopy(self.payload)
            payload["lines"][0].update(change)
            payload["expected_revision"] = 1
            response = self.client.put(self.url("detail", draft["id"]), payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        unchanged = TaxInvoice.objects.get(pk=draft["id"])
        self.assertEqual(unchanged.revision, 1)
        self.assertEqual(unchanged.total, Decimal("37.45"))
        self.assertEqual(unchanged.lines.count(), 1)

    def test_parser_keeps_prices_leaves_missing_vat_and_never_creates_an_invoice(self):
        source = preview()
        source["lines"].append({"raw_name": "Second item", "quantity": "2", "unit": "box", "line_total": "100", "vat_rate": "5"})
        with patch("quotations.tax_invoices.parse_text_preview", return_value=source):
            response = self.client.post(self.url("parse-document"), {"text": "Quote", "use_ai": False}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["lines"][0]["unit_price"], "12.5")
        self.assertIsNone(response.data["lines"][0]["vat_rate"])
        self.assertIsNone(response.data["lines"][1]["unit_price"])
        self.assertEqual(response.data["lines"][1]["vat_rate"], "5")
        self.assertFalse(TaxInvoice.objects.exists())
        draft = self.draft({**self.payload, "import_token": response.data["import_token"]})
        self.assertEqual(draft["source_filename"], "purchase-order.xlsx")
        other = get_user_model().objects.create_user("another-staff", is_staff=True)
        self.client.force_authenticate(other)
        rejected = self.client.post(self.url("list"), {**self.payload, "import_token": response.data["import_token"]}, format="json")
        self.assertEqual(rejected.status_code, 400)

    def test_unprivileged_accounts_cannot_read_create_parse_or_issue(self):
        draft = self.draft()
        self.client.force_authenticate(get_user_model().objects.create_user("customer"))
        self.assertEqual(self.client.get(self.url("list")).status_code, 403)
        self.assertEqual(self.client.get(self.url("pdf", draft["id"])).status_code, 403)
        self.assertEqual(self.client.post(self.url("list"), self.payload, format="json").status_code, 403)
        self.assertEqual(self.client.post(self.url("parse-document"), {"text": "Quote"}, format="json").status_code, 403)
        self.assertEqual(self.issue(draft).status_code, 403)

    def test_actual_quotation_pdf_keeps_item_and_unit_price(self):
        quote = Quotation.objects.create(company=self.company, status=Quotation.STATUS_FINALIZED)
        QuotationLine.objects.create(quotation=quote, item_name_snapshot="Sterile gauze swabs", quantity=3, unit="Box",
                                     unit_price=Decimal("12.555"), vat_rate=5, match_status=QuotationLine.MATCH_CONFIRMED)
        data = build_quotation_pdf(quote)
        with TemporaryDirectory() as folder, override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=folder):
            response = self.client.post(self.url("parse-document"), {"file": SimpleUploadedFile("quotation.pdf", data, content_type="application/pdf"), "use_ai": "false"}, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["lines"]), 1, response.data)
        self.assertEqual(response.data["lines"][0]["item_name"].lower(), "sterile gauze swabs")
        self.assertEqual(Decimal(response.data["lines"][0]["unit_price"]), Decimal("12.555"))
        self.assertEqual(response.data["company"], self.company.pk)

    def test_list_search_status_and_private_source_fields(self):
        draft = self.draft()
        self.issue(draft)
        self.draft({**self.payload, "customer_name": "Second customer"})
        response = self.client.get(self.url("list"), {"status": "issued", "search": "Resort"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["count"], 1)
        row = response.data["results"][0]
        for field in ["lines", "issued_pdf", "source", "supplier_snapshot"]:
            self.assertNotIn(field, row)

    def test_numbering_is_unique_and_pdf_failure_rolls_back_issuance(self):
        first, second = self.draft(), self.draft()
        with patch("quotations.tax_invoice_pdf.build_tax_invoice_pdf", side_effect=ValueError("render failure")), self.assertLogs(level="ERROR"):
            self.assertEqual(self.issue(first).status_code, 500)
        self.assertEqual(TaxInvoice.objects.get(pk=first["id"]).status, "draft")
        self.assertFalse(TaxInvoiceSequence.objects.exists())
        self.assertEqual(self.issue(second).data["invoice_number"], "604671")
        self.assertEqual(self.issue(first).data["invoice_number"], "604670")

    def test_manual_number_preserves_leading_zeroes_and_safe_download_filename(self):
        draft = self.draft({**self.payload, "invoice_number": "  00604670/26-A_1  "})
        self.assertEqual(draft["invoice_number"], "00604670/26-A_1")
        self.assertEqual(self.issue(draft).data["invoice_number"], "00604670/26-A_1")
        pdf = self.client.get(self.url("pdf", draft["id"]))
        self.assertEqual(pdf["Content-Disposition"], 'attachment; filename="00604670_26-A_1.pdf"')

    def test_duplicate_numbers_are_blocked_on_create_and_update_including_case_and_spaces(self):
        first = self.draft({**self.payload, "invoice_number": "Inv-006"})
        duplicate = self.client.post(self.url("list"), {**self.payload, "invoice_number": "  INV-006  "}, format="json")
        self.assertEqual(duplicate.status_code, 400, duplicate.data)
        self.assertIn("already used", str(duplicate.data["invoice_number"]))
        second = self.draft()
        duplicate = self.client.patch(self.url("detail", second["id"]), {"invoice_number": "inv-006", "expected_revision": 1}, format="json")
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(TaxInvoice.objects.get(pk=second["id"]).revision, 1)
        own_number = self.client.patch(self.url("detail", first["id"]), {"invoice_number": "Inv-006", "expected_revision": 1}, format="json")
        self.assertEqual(own_number.status_code, 200, own_number.data)
        # The database constraint also protects simultaneous requests that both
        # pass serializer validation before either request commits its number.
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaxInvoice.objects.filter(pk=second["id"]).update(invoice_number=" inv-006 ")

    def test_duplicate_race_returns_validation_error_and_rolls_back_edits(self):
        self.draft({**self.payload, "invoice_number": "Inv-006"})
        second = self.draft()
        with patch("quotations.tax_invoices.TaxInvoiceSerializer.validate_invoice_number", return_value="inv-006"):
            response = self.client.patch(self.url("detail", second["id"]),
                                         {"invoice_number": "inv-006", "notes": "must roll back", "expected_revision": 1}, format="json")
        self.assertEqual(response.status_code, 400, response.data)
        unchanged = TaxInvoice.objects.get(pk=second["id"])
        self.assertEqual(unchanged.invoice_number, second["invoice_number"])
        self.assertEqual(unchanged.revision, 1)
        self.assertEqual(unchanged.notes, "")

    def test_blank_number_and_billing_details_can_remain_draft_but_cannot_issue(self):
        for field in ["invoice_number", "customer_address", "customer_trn"]:
            with self.subTest(field=field):
                draft = self.draft({**self.payload, field: ""})
                self.assertEqual(self.issue(draft).status_code, 400)
                self.assertEqual(TaxInvoice.objects.get(pk=draft["id"]).status, "draft")
        self.assertEqual(TaxInvoice.objects.filter(invoice_number=None).count(), 1)
        self.draft({**self.payload, "invoice_number": ""})  # Multiple unnumbered drafts remain valid.

    def test_full_customer_details_print_without_altering_company_record(self):
        name = "JUSTLIFE HOME HEALTH CARE CENTER L.L.C"
        address = "Office 216, Al Attar Business Centre\nAl Barsha First, Dubai, UAE"
        draft = self.draft({**self.payload, "customer_name": name, "customer_address": address})
        self.assertEqual(self.issue(draft).status_code, 200)
        pdf = self.client.get(self.url("pdf", draft["id"]))
        document = PdfReader(BytesIO(pdf.content))
        text = " ".join("\n".join(page.extract_text() for page in document.pages).split())
        self.assertIn(name, text)
        self.assertIn(" ".join(address.split()), text)
        self.assertTrue(document.pages[0].images)
        self.company.refresh_from_db()
        self.assertEqual(self.company.name, "Resort LLC")

    def test_legacy_issued_pdf_remains_accessible_without_new_required_fields(self):
        draft = self.draft()
        TaxInvoice.objects.filter(pk=draft["id"]).update(status="issued", invoice_number="TI-2026-000001", customer_trn="", issued_pdf=b"original issued PDF")
        self.assertEqual(self.client.get(self.url("pdf", draft["id"])).content, b"original issued PDF")
        self.assertEqual(self.issue(draft).status_code, 200)
