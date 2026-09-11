from decimal import Decimal
from io import BytesIO
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from reportlab.pdfgen import canvas
from rest_framework.test import APIClient

from .ai_parsing import AIParseError, _normalize_ai_result, LPO_DELIVERY_JSON_SCHEMA, prefer_safe_ai_preview
from .delivery_pdf import build_delivery_note_pdf
from .import_parsers import parse_file_preview
from .import_rules import classify_header_cell, is_obvious_document_metadata_row
from .lpo_parsing import normalize_lpo_preview, preserve_lpo_line_details
from .models import Company, DeliveryNote, Quotation, QuotationLine
from .pdf import build_quotation_pdf
from .views import _extract_lpo_details


def preview():
    return {
        "source_filename": "purchase-order.xlsx", "source_sha256": "a" * 64,
        "source_file_ref": "", "parse_method": "deterministic_test",
        "original_text": "Purchase Order: RES-PO-00957871\nPO Date: 09-SEP-26",
        "meta": {"delivery_details": {
            "customer_name": "Resort LLC", "delivery_address": "Al Sufouh\nDubai",
            "attention": "Receiving contact", "contact_phone": "", "requested_delivery_date": "2026-09-06",
        }},
        "warnings": [],
        "lines": [{"raw_name": "Gauze 7.5cm", "requested_item_name": "Gauze 7.5cm",
                   "quantity": "10", "unit": "BOX", "unit_price": "12.5", "parse_confidence": .98}],
    }


@override_settings(QUOTATION_AI_PARSE_GLOBAL_ENABLED=False)
class DeliveryLPOTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user("delivery-lpo-staff", is_staff=True)
        self.company = Company.objects.create(name="Resort LLC")
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def parse(self, data=None, source=None):
        with patch("quotations.delivery_views.parse_text_preview", return_value=source or preview()):
            return self.client.post(reverse("quotation-delivery-note-parse-lpo"),
                data or {"text": "PO", "use_ai": False}, format="json")

    def test_lpo_references_keep_underscores_dashes_and_pdf_separator_spacing(self):
        for text, expected in [
            ("PO No: PO112_112353", "PO112_112353"),
            ("Purchase Order: PO112-112353", "PO112-112353"),
            ("LPO Number: LPO_A12_000123-4/26", "LPO_A12_000123-4/26"),
            ("PO No: PO112 _ 112353\nDate: 11/09/2026", "PO112_112353"),
            ("Purchase Order: AB_12345 / 26\nCustomer: Example", "AB_12345/26"),
        ]:
            with self.subTest(text=text):
                source = preview()
                source["original_text"] = text
                response = self.parse(source=source)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual(response.data["details"]["lpo_number"], expected)

    def test_full_document_reference_repairs_truncated_ai_value_but_not_conflicting_values(self):
        for metadata, text, expected in [
            ("PO112", "PO No: PO112_112353", "PO112_112353"),
            ("OTHER-123", "PO No: PO112_112353", "OTHER-123"),
            ("PO112", "PO No: PO112_112353\nPO No: PO112_998877", "PO112"),
        ]:
            source = preview()
            source["original_text"] = text
            source["meta"]["delivery_details"]["lpo_number"] = metadata
            response = self.parse(source=source)
            self.assertEqual(response.data["details"]["lpo_number"], expected)

    def test_intermass_filename_can_complete_reference_without_including_attachment_revision(self):
        source = preview()
        source["original_text"] = ""
        source["source_filename"] = "PO_PO112_112353_0.pdf"
        source["meta"]["delivery_details"]["lpo_number"] = "PO112"
        self.assertEqual(self.parse(source=source).data["details"]["lpo_number"], "PO112_112353")

    def test_parse_then_save_reviewed_standalone_note_does_not_create_a_quotation_or_issue(self):
        response = self.parse()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(DeliveryNote.objects.count(), 0)
        self.assertEqual(Quotation.objects.count(), 0)
        self.assertEqual(response.data["company"], self.company.pk)
        self.assertEqual(response.data["details"]["lpo_number"], "RES-PO-00957871")
        self.assertEqual(response.data["details"]["lpo_date"], "2026-09-09")
        rows = response.data["lines"]
        rows[0]["quantity"] = "6"
        saved = self.client.post(reverse("quotation-delivery-note-list"), {
            "company": self.company.pk, "quotation": None,
            "lpo_number": response.data["details"]["lpo_number"],
            "delivery_address": response.data["details"]["delivery_address"],
            "lpo_import_token": response.data["import_token"], "lines": rows,
        }, format="json")
        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertEqual(saved.data["status"], "draft")
        self.assertIsNone(saved.data["quotation"])
        self.assertNotEqual(saved.data["delivery_date"], "2026-09-06")
        self.assertEqual(Decimal(saved.data["lines"][0]["quantity"]), 6)
        note = DeliveryNote.objects.get(pk=saved.data["id"])
        self.assertEqual(note.lpo_import["rows"][0]["quantity"], "10")
        self.assertEqual(note.lpo_import["parsed_by"], self.actor.pk)
        self.assertNotIn("source_file_ref", saved.data["lpo_source"])
        self.assertNotIn("lpo_import", saved.data)

    def test_ai_is_used_for_rows_and_header_details_and_failure_keeps_a_reviewable_preview(self):
        cleaned = preview()
        cleaned["meta"]["delivery_details"]["contact_phone"] = "04 111 2222"
        with patch("quotations.delivery_views.clean_preview_with_ai", return_value=cleaned) as ai:
            response = self.parse({"text": "PO", "use_ai": True})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertTrue(ai.call_args.kwargs["delivery_details"])
        self.assertEqual(response.data["details"]["contact_phone"], "04 111 2222")
        with patch("quotations.delivery_views.clean_preview_with_ai", side_effect=AIParseError("AI unavailable")):
            response = self.parse({"text": "PO", "use_ai": True})
        self.assertEqual(response.status_code, 200)
        self.assertIn("AI unavailable", response.data["warnings"])
        self.assertEqual(response.data["lines"][0]["quantity"], "10")

    def test_matching_lpo_supplies_missing_customer_snapshot_without_editing_company(self):
        source = preview()
        source["meta"]["delivery_details"].update(customer_address="King Street, Dubai", customer_trn="100000000000003")
        parsed = self.parse(source=source).data
        response = self.client.post(reverse("quotation-delivery-note-list"), {
            "company": self.company.pk, "lpo_import_token": parsed["import_token"], "lines": parsed["lines"],
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["customer_address"], "King Street, Dubai")
        self.assertEqual(response.data["customer_trn"], "100000000000003")
        self.company.refresh_from_db()
        self.assertEqual(self.company.billing_address, "")
        self.assertEqual(self.company.trn, "")

    def test_ai_cannot_remove_a_strong_item_or_change_its_quantity(self):
        cleaned = preview()
        cleaned["lines"][0]["quantity"] = "999"
        with patch("quotations.delivery_views.clean_preview_with_ai", return_value=cleaned):
            response = self.parse({"text": "PO", "use_ai": True})
        self.assertEqual(response.data["lines"][0]["quantity"], "10")
        self.assertTrue(any("rejected" in warning for warning in response.data["warnings"]))

    def test_missing_or_invalid_quantities_are_never_invented(self):
        for quantity in ("", "NaN", "-1", "0", "0.0001"):
            source = preview()
            source["lines"][0]["quantity"] = quantity
            response = self.parse(source=source)
            self.assertEqual(response.status_code, 200, response.data)
            self.assertEqual(response.data["lines"][0]["quantity"], "")
            self.assertTrue(any("quantity" in warning for warning in response.data["warnings"]))

    def test_larger_lpo_cannot_silently_lose_items_during_ai_cleanup(self):
        source = preview()
        source["lines"] = [{**source["lines"][0], "raw_name": "Gauze " + str(i), "requested_item_name": "Gauze " + str(i)} for i in range(12)]
        cleaned = {**source, "lines": source["lines"][:10]}
        with patch("quotations.delivery_views.clean_preview_with_ai", return_value=cleaned):
            response = self.parse({"text": "PO", "use_ai": True}, source=source)
        self.assertEqual(len(response.data["lines"]), 12)
        self.assertTrue(any("rejected" in warning for warning in response.data["warnings"]))

    def test_ai_unit_changes_are_rejected_but_equivalent_spellings_are_allowed(self):
        for unit, expected in (("PIECE", "BOX"), ("boxes", "boxes")):
            cleaned = preview()
            cleaned["lines"][0]["unit"] = unit
            with patch("quotations.delivery_views.clean_preview_with_ai", return_value=cleaned):
                response = self.parse({"text": "PO", "use_ai": True})
            self.assertEqual(response.data["lines"][0]["unit"], expected)

    def test_ai_keeps_source_references_only_for_unique_same_item_quantity_and_unit(self):
        source = preview()
        source["lines"][0]["description"] = "Product code: GI123\nBPA: JIL-PA-1"
        cleaned = preview()
        cleaned["lines"][0].update(requested_item_name="GAUZE 7.5 CM", quantity="10.000", unit="boxes")
        result = preserve_lpo_line_details(source, cleaned)
        self.assertEqual(result["lines"][0]["description"], source["lines"][0]["description"])
        self.assertNotIn("description", cleaned["lines"][0])
        for changes in ({"requested_item_name": "Gauze 10cm"}, {"requested_item_name": "Gauze 75cm"}, {"quantity": "5"}, {"unit": "piece"}):
            changed = {**cleaned, "lines": [{**cleaned["lines"][0], **changes}]}
            self.assertNotIn("description", preserve_lpo_line_details(source, changed)["lines"][0])
        source["lines"].append({**source["lines"][0], "description": "Product code: DIFFERENT"})
        self.assertNotIn("description", preserve_lpo_line_details(source, cleaned)["lines"][0])

    def test_ambiguous_customer_requires_selection(self):
        Company.objects.create(name="Resort L.L.C.")
        response = self.parse()
        self.assertIsNone(response.data["company"])
        self.assertEqual(len(response.data["company_candidates"]), 2)

    def test_signed_preview_cannot_be_tampered_with_or_used_by_another_staff_member(self):
        token = self.parse().data["import_token"]
        for value, actor in [(token + "x", self.actor), (token, get_user_model().objects.create_user("other", is_staff=True))]:
            self.client.force_authenticate(actor)
            response = self.client.post(reverse("quotation-delivery-note-list"), {
                "company": self.company.pk, "lpo_import_token": value,
                "lines": [{"item_name": "Gauze", "quantity": 1}],
            }, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(DeliveryNote.objects.exists())

    def test_empty_unsupported_and_nonstaff_uploads_are_rejected(self):
        url = reverse("quotation-delivery-note-parse-lpo")
        self.assertEqual(self.client.post(url, {}, format="json").status_code, 400)
        bad = SimpleUploadedFile("bad.exe", b"invalid", content_type="application/octet-stream")
        self.assertEqual(self.client.post(url, {"file": bad}, format="multipart").status_code, 400)
        self.client.force_authenticate(get_user_model().objects.create_user("external"))
        self.assertEqual(self.client.post(url, {"text": "PO"}, format="json").status_code, 403)

    def test_quotation_lpo_headers_and_accepted_quantities_flow_into_delivery(self):
        quote = Quotation.objects.create(company=self.company, status="sent")
        line = QuotationLine.objects.create(quotation=quote, item_name_snapshot="Gauze 7.5cm",
            quantity=10, unit="BOX", unit_price=12.5, match_status="confirmed")
        with patch("quotations.views.parse_text_preview", return_value=preview()):
            parsed = self.client.post(reverse("quotation-parse-outcome-po", args=[quote.pk]),
                {"text": "PO", "use_ai": False}, format="json")
        self.assertEqual(parsed.status_code, 201, parsed.data)
        lpo = quote.lpos.get()
        self.assertEqual(lpo.parsed_meta["delivery_details"]["delivery_address"], "Al Sufouh\nDubai")
        approved = self.client.patch(reverse("quotation-outcome", args=[quote.pk]), {
            "lpo_id": lpo.pk, "po_import_id": parsed.data["id"], "applied_po_line_ids": [line.pk],
            "line_updates": [{"id": line.pk, "outcome_status": "quantity_changed",
                             "accepted_quantity": "6", "accepted_unit_price": "12.5"}],
        }, format="json")
        self.assertEqual(approved.status_code, 200, approved.data)
        note = self.client.post(reverse("quotation-delivery-note-list"), {
            "quotation": quote.pk, "lpo_number": lpo.lpo_number,
            "lines": [{"quotation_line": line.pk, "quantity": "6"}],
        }, format="json")
        self.assertEqual(note.status_code, 201, note.data)
        self.assertEqual(note.data["delivery_address"], "Al Sufouh\nDubai")
        self.assertEqual(note.data["attention"], "Receiving contact")
        self.assertEqual(Decimal(note.data["lines"][0]["quantity"]), 6)
        self.assertEqual(note.data["status"], "draft")

    def test_cleanup_removes_document_rows_and_preserves_codes_sizes_and_prices(self):
        source = preview()
        row = source["lines"][0]
        row.update(raw_name="Gauze 7.5cm Product Code: GI123 BPA: REF-1 Note from Requester:",
                   requested_item_name="Gauze 7.5cm Product Code: GI123 BPA: REF-1 Note from Requester:",
                   raw_line="1 | Gauze 7.5cm Product Code: GI123 BPA: REF-1 Note from Requester: | BOX | 10")
        source["lines"].extend({"raw_name": value} for value in
            ["Table Of Particulars", "PO Total (AED) 125", "* Tax Total (AED) 6.25", "Page 1 O F 8"])
        result = normalize_lpo_preview(source, read_pdf=False)
        self.assertEqual(len(result["lines"]), 1)
        self.assertEqual(result["lines"][0]["raw_name"], "Gauze 7.5cm")
        self.assertIn("Product code: GI123", result["lines"][0]["description"])
        self.assertEqual(result["lines"][0]["unit_price"], "12.5")
        self.assertEqual(classify_header_cell("Unit\nPrice\n(AED)"), "unit_price")
        self.assertEqual(classify_header_cell("Amou\nnt\n(AED)"), "amount")

    def test_pdf_columns_keep_supplier_phone_and_payment_terms_out_of_customer_fields(self):
        stream = BytesIO()
        pdf = canvas.Canvas(stream, pagesize=(600, 840))
        for x, y, text in [
            (25, 730, "Purchase Order: RES-PO-00957871"), (320, 730, "PO Date: 09-SEP-26"),
            (25, 690, "Supplier:"), (25, 675, "Our Pharmacy"), (25, 660, "Tel: 04 999 9999"),
            (290, 690, "Bill To:"), (290, 675, "Resort LLC"), (290, 660, "King Street, Dubai"),
            (290, 630, "100000000000003"),
            (25, 600, "Ship To:"), (290, 600, "Payment Terms: 45 days"),
            (25, 580, "Al Sufouh"), (25, 565, "Dubai"), (25, 550, "United Arab Emirates"),
            (25, 530, "Requestor: Receiving contact"), (25, 515, "Requestor Phone Number:"),
            (240, 480, "Table Of Particulars"),
        ]:
            pdf.drawString(x, y, text)
        pdf.save()
        with TemporaryDirectory() as folder, override_settings(
            QUOTATION_PRIVATE_STORAGE_ROOT=folder,
            STORAGES={"default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
                      "quotation_evidence": {"BACKEND": "quotations.private_storage.QuotationEvidenceFileSystemStorage"}},
        ):
            parsed = parse_file_preview(SimpleUploadedFile("header.pdf", stream.getvalue(), content_type="application/pdf"))
            result = normalize_lpo_preview(parsed)
        fields = result["meta"]["delivery_details"]
        self.assertEqual(fields["customer_name"], "Resort LLC")
        self.assertEqual(fields["customer_address"], "King Street, Dubai")
        self.assertEqual(fields["customer_trn"], "100000000000003")
        self.assertEqual(fields["delivery_address"], "Al Sufouh\nDubai\nUnited Arab Emirates")
        self.assertEqual(fields["attention"], "Receiving contact")
        self.assertEqual(fields["contact_phone"], "")
        self.assertEqual(_extract_lpo_details(result)["lpo_number"], "RES-PO-00957871")

    def test_lpo_ai_contract_retains_delivery_fields(self):
        raw = {"rows": [{"item_name": "Gauze", "quantity": "10", "unit": "BOX", "parse_status": "parsed",
                        "confidence": .99}], "warnings": [], "delivery_details": {"customer_name": "Resort LLC"}}
        result = _normalize_ai_result(raw, preview=preview(), mode="text", provider="test", model="test", output_style="inquiry",
                                      schema_name="lpo_delivery_parse")
        self.assertEqual(result["meta"]["delivery_details"]["customer_name"], "Resort LLC")
        self.assertIn("delivery_details", LPO_DELIVERY_JSON_SCHEMA["required"])

    def test_ai_can_remove_numeric_quotation_headers_without_restoring_them_as_products(self):
        source = preview()
        source["original_text"] = "QUOTATION"
        source["meta"] = {}
        headers = [
            ("Customer Resort LLC Quotation 0008", "Customer | Resort LLC | Quotation # | QT-20260910-0008", "8"),
            ("Customer TRN 100000000000003 Date 2026 09", "Customer TRN | 100000000000003 | Date | 2026-09-10", "10"),
            ("Valid Until 2026 10 10 Prepared By Staff", "Valid Until | 2026-10-10 | Prepared By | Staff", None),
            ("Status Sent Currency AED", "Status | Sent | Currency | AED", None),
            ("Terms and Conditions Prices are subject to availability", "Terms and Conditions Prices are subject to availability | Prepared / Approved By Staff", None),
        ]
        source["lines"] += [{"requested_item_name": name, "raw_line": raw, "quantity": quantity,
                             "parse_confidence": .82} for name, raw, quantity in headers]
        cleaned = {**source, "meta": {}, "lines": source["lines"][:1]}
        # The shared inquiry guard must also permit removal before normalization.
        self.assertEqual(len(prefer_safe_ai_preview(source, cleaned, max_guard_rows=300, check_units=True)["lines"]), 1)
        with patch("quotations.delivery_views.clean_preview_with_ai", return_value=cleaned):
            response = self.parse({"text": "QUOTATION", "use_ai": True}, source=source)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertEqual(response.data["document_type"], "quotation")
        self.assertEqual(response.data["details"]["customer_name"], "Resort LLC")
        self.assertEqual(response.data["details"]["customer_trn"], "100000000000003")
        self.assertEqual(response.data["details"]["quotation_number"], "QT-20260910-0008")
        self.assertEqual(response.data["details"]["requested_delivery_date"], "")
        self.assertFalse(any("rejected" in warning for warning in response.data["warnings"]))
        for name in ("Customer Care Kit", "Status Monitor", "Contact Lens Solution"):
            self.assertFalse(is_obvious_document_metadata_row({"requested_item_name": name,
                             "raw_line": name + " | 5 | BOX", "quantity": "5"}))

    def test_uploaded_quotation_pdf_produces_only_items_and_saves_its_own_reference(self):
        import fitz
        quote = Quotation.objects.create(company=self.company, status="sent", valid_until="2026-10-10")
        names = ["Gauze", "Gloves", "Syringe", "Bandage", "Cold Pack", "Cotton", "Thermometer", "Tape"]
        for index, name in enumerate(names, 1):
            QuotationLine.objects.create(quotation=quote, item_name_snapshot=name, quantity=index,
                                         unit="BOX", unit_price=12.5, match_status="confirmed")
        pdf = build_quotation_pdf(quote)
        with TemporaryDirectory() as root, override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=root):
            response = self.client.post(reverse("quotation-delivery-note-parse-document"), {
                "file": SimpleUploadedFile("quotation.pdf", pdf, content_type="application/pdf"),
                "use_ai": "false", "document_type": "auto",
            }, format="multipart")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["document_type"], "quotation")
        self.assertEqual([row["item_name"] for row in response.data["lines"]], names)
        self.assertEqual([Decimal(row["quantity"]) for row in response.data["lines"]], list(range(1, 9)))
        self.assertEqual(response.data["company"], self.company.pk)
        self.assertEqual(response.data["details"]["quotation_number"], quote.quotation_number)
        self.assertEqual(response.data["details"]["lpo_number"], "")
        self.assertEqual(response.data["details"]["requested_delivery_date"], "")
        self.assertFalse(any("LPO number" in warning for warning in response.data["warnings"]))
        self.assertFalse(DeliveryNote.objects.exists())
        rows = response.data["lines"]
        rows[1]["quantity"] = "1"
        saved = self.client.post(reverse("quotation-delivery-note-list"), {
            "company": self.company.pk, "quotation_reference": response.data["details"]["quotation_number"],
            "lpo_import_token": response.data["import_token"], "lines": rows,
        }, format="json")
        self.assertEqual(saved.status_code, 201, saved.data)
        self.assertEqual(saved.data["status"], "draft")
        self.assertIsNone(saved.data["quotation"])
        self.assertEqual(saved.data["lpo_number"], "")
        self.assertEqual(saved.data["lpo_source"]["document_type"], "quotation")
        note = DeliveryNote.objects.get(pk=saved.data["id"])
        with fitz.open(stream=build_delivery_note_pdf(note), filetype="pdf") as document:
            text = "\n".join(page.get_text() for page in document)
        self.assertIn("Quotation Ref.", text)
        self.assertIn(quote.quotation_number, text)
        self.assertNotIn("LPO No.", text)
        found = self.client.get(reverse("quotation-delivery-note-list"), {"search": quote.quotation_number})
        self.assertEqual(found.data["count"], 1)

    def test_pasted_quotation_without_a_reference_does_not_require_an_lpo(self):
        response = self.client.post(reverse("quotation-delivery-note-parse-document"), {
            "text": "Customer: Resort LLC\nGauze 5 BOX",
            "use_ai": False, "document_type": "quotation",
        }, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["document_type"], "quotation")
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertFalse(any("LPO number" in warning for warning in response.data["warnings"]))
        self.assertFalse(DeliveryNote.objects.exists())
