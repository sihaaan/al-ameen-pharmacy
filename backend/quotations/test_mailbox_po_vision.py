import base64
import io
import json
import os
from datetime import timedelta
from unittest.mock import Mock, patch

import fitz
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from .ai_parsing import (
    AIParseError,
    AIProviderUnavailable,
    MAILBOX_PO_VISION_JSON_SCHEMA,
    OpenAIResponsesParseProvider,
    _render_pdf_bytes_images,
    clean_pdf_bytes_with_ai,
)
from .mailbox_po_audit import (
    MAILBOX_AI_REVIEW_WARNING,
    _is_plausible_document_attachment,
    _merge_mailbox_vision_preview,
    _preview_attachment,
    attachment_needs_mailbox_vision_repair,
    classify_mailbox_message,
    hydrate_plausible_attachments,
    mailbox_po_audit_repair_remaining,
    mark_unavailable_mailbox_vision_for_manual_review,
    reclassify_mailbox_po_audit_messages,
    repair_mailbox_po_audit_pdf_vision,
)
from .mailbox_po_matching import rank_message_to_quotations
from .mailbox_po_reconciliation import document_variants, reconcile_mailbox_po_audit
from .models import (
    AIParseCache,
    AIParseLog,
    GmailOAuthConnection,
    MailboxPOAuditRun,
    MailboxPOAuditRunMessage,
    MailboxPOMatchRun,
    MailboxPOMessage,
    QuotationSettings,
)


def pdf_bytes(page_count=1, *, text=""):
    document = fitz.open()
    for _index in range(page_count):
        page = document.new_page()
        if text:
            page.insert_text((72, 72), text)
    data = document.tobytes()
    document.close()
    return data


def raster_pdf_bytes(page_count=1):
    pixel = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pixel.clear_with(230)
    image = pixel.tobytes("png")
    document = fitz.open()
    for _index in range(page_count):
        page = document.new_page()
        page.insert_image(fitz.Rect(72, 72, 300, 180), stream=image)
    data = document.tobytes()
    document.close()
    return data


def ai_result(item="Sterile Gauze 10cm", **overrides):
    result = {
        "rows": [
            {
                "item_name": item,
                "quantity": "12",
                "unit": "box",
                "unit_price": "5",
                "vat_rate": "5",
                "vat_amount": "3",
                "line_total": "60",
                "pack_info": "",
                "notes": "visible on page 1",
                "raw_source_text": f"{item} 12 box 5 60",
                "page_number": "1",
                "confidence": 96,
                "parse_status": "parsed",
                "reason": "clear row",
            }
        ],
        "warnings": [],
        "document_notes": "PO JLMG-PO-00028268 for QT-20260525-0001",
        "document_type": "local_purchase_order",
        "po_references": [
            {"reference": "JLMG-PO-00028268", "page_number": "1", "confidence": 98}
        ],
        "quotation_references": [
            {"reference": "QT-20260525-0001", "page_number": "1", "confidence": 97}
        ],
        "currency": "AED",
        "subtotal": "60",
        "vat_total": "3",
        "grand_total": "63",
        "totals_page_number": "1",
        "document_confidence": 96,
    }
    result.update(overrides)
    return result


class FakeVisionProvider:
    def __init__(self, *, error=None):
        self.error = error
        self.calls = []

    def clean_rows(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return ai_result(), {"input_tokens": 10, "output_tokens": 20}


AI_SETTINGS = {
    "QUOTATION_AI_PARSE_GLOBAL_ENABLED": True,
    "QUOTATION_AI_PARSE_PROVIDER": "openai",
    "QUOTATION_AI_PARSE_TEXT_MODEL": "test-text-model",
    "QUOTATION_AI_PARSE_VISION_MODEL": "test-vision-model",
    "QUOTATION_AI_PARSE_MAX_RENDERED_PAGES": 3,
    "QUOTATION_MAILBOX_AI_VISION_ENABLED": True,
}


@override_settings(**AI_SETTINGS)
@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
class InMemoryPDFVisionTests(TestCase):
    def enable_ai(self, *, auto=True, vision=True):
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_parsing_enabled = True
        settings_obj.ai_auto_cleanup_enabled = auto
        settings_obj.ai_pdf_vision_enabled = vision
        settings_obj.save()

    def preview(self, data):
        return {
            "source_type": "pdf",
            "source_filename": "scanned-po.pdf",
            "source_mime_type": "application/pdf",
            "source_sha256": __import__("hashlib").sha256(data).hexdigest(),
            "source_file_ref": "gmail:must-not-be-read",
            "parse_method": "ocr_required_not_configured_v2",
            "original_text": "",
            "warnings": ["No selectable text detected."],
            "meta": {"page_count": 1, "source_file_ref": "private:must-not-leak"},
            "lines": [],
        }

    def test_disabled_vision_never_renders_or_calls_provider(self):
        self.enable_ai(vision=False)
        provider = FakeVisionProvider()
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with patch("quotations.ai_parsing._render_pdf_bytes_images") as render:
                with self.assertRaisesMessage(AIProviderUnavailable, "vision cleanup is disabled"):
                    clean_pdf_bytes_with_ai(b"%PDF-empty", self.preview(b"%PDF-empty"))
        render.assert_not_called()
        self.assertEqual(provider.calls, [])

    def test_in_memory_path_uses_provider_cache_and_log_without_private_storage(self):
        self.enable_ai()
        data = pdf_bytes(text="scanned image placeholder")
        provider = FakeVisionProvider()
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with patch("quotations.ai_parsing.read_private_ref") as private_read:
                first = clean_pdf_bytes_with_ai(data, self.preview(data))
                second = clean_pdf_bytes_with_ai(data, self.preview(data))

        self.assertEqual(len(provider.calls), 1)
        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(first["source_file_ref"], "")
        self.assertEqual(second["source_file_ref"], "")
        self.assertEqual(first["meta"]["source_file_ref"], "")
        self.assertEqual(second["meta"]["source_file_ref"], "")
        private_read.assert_not_called()
        self.assertEqual(AIParseCache.objects.count(), 1)
        self.assertEqual(AIParseLog.objects.filter(success=True).count(), 2)

    def test_provider_failure_is_logged_and_not_cached(self):
        self.enable_ai()
        data = pdf_bytes()
        provider = FakeVisionProvider(error=RuntimeError("provider timeout"))
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with self.assertRaisesMessage(AIParseError, "provider timeout"):
                clean_pdf_bytes_with_ai(data, self.preview(data))
        self.assertEqual(AIParseCache.objects.count(), 0)
        failed_log = AIParseLog.objects.get(success=False)
        self.assertIn("provider timeout", failed_log.error)

    def test_mailbox_log_keeps_actor_and_non_content_source_identity(self):
        self.enable_ai()
        actor = User.objects.create_user("vision-log-actor", is_staff=True)
        data = pdf_bytes()
        preview = {
            **self.preview(data),
            "ai_log_source_identity": {
                "audit_run_id": 41,
                "gmail_message_id": "gmail-message-9",
                "attachment_id": "attachment-3",
            },
        }
        provider = FakeVisionProvider()
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            clean_pdf_bytes_with_ai(data, preview, actor=actor)

        log = AIParseLog.objects.get()
        self.assertEqual(log.actor, actor)
        self.assertEqual(
            log.usage["source_identity"],
            {
                "audit_run_id": "41",
                "gmail_message_id": "gmail-message-9",
                "attachment_id": "attachment-3",
            },
        )

    @override_settings(QUOTATION_AI_PARSE_TIMEOUT_SECONDS=17)
    @patch("quotations.ai_parsing.urllib.request.urlopen")
    def test_openai_responses_disables_provider_storage_and_keeps_timeout(self, urlopen):
        response = urlopen.return_value.__enter__.return_value
        response.read.return_value = json.dumps(
            {"output_text": json.dumps(ai_result()), "usage": {"input_tokens": 1}}
        ).encode("utf-8")
        provider = OpenAIResponsesParseProvider(api_key="test-key")

        provider.clean_rows(
            mode="vision",
            model="test-vision-model",
            instructions="extract",
            text_context="mailbox review",
            image_data_urls=["data:image/png;base64,AA=="],
            json_schema=MAILBOX_PO_VISION_JSON_SCHEMA,
            schema_name="mailbox_po_vision_parse",
        )

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data.decode("utf-8"))
        self.assertIs(payload["store"], False)
        self.assertEqual(urlopen.call_args.kwargs["timeout"], 17)

    def test_mailbox_schema_normalizes_structured_metadata_and_fails_closed_on_conflicts(self):
        self.enable_ai()
        data = pdf_bytes()
        provider = FakeVisionProvider()
        provider.clean_rows = lambda **_kwargs: (
            ai_result(
                document_notes="QT-20260525-0001",
                po_references=[
                    {"reference": "not-an-order", "page_number": "1", "confidence": 99}
                ],
                quotation_references=[
                    {"reference": "QT-20260525-0002", "page_number": "1", "confidence": 95}
                ],
                subtotal="50",
                vat_total="3",
                grand_total="999",
            ),
            {},
        )
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            cleaned = clean_pdf_bytes_with_ai(
                data,
                self.preview(data),
                json_schema=MAILBOX_PO_VISION_JSON_SCHEMA,
                schema_name="mailbox_po_vision_parse",
            )

        metadata = cleaned["document_metadata"]
        self.assertEqual(metadata["po_references"], [])
        self.assertEqual(metadata["quotation_references"][0]["reference"], "QT-20260525-0002")
        self.assertEqual(metadata["grand_total"], "999")
        warning_text = " ".join(cleaned["warnings"]).lower()
        self.assertIn("invalid po reference", warning_text)
        self.assertIn("line arithmetic", warning_text)
        self.assertIn("subtotal plus vat", warning_text)
        self.assertIn("structured quotation references conflict", warning_text)

        merged = _merge_mailbox_vision_preview(self.preview(data), cleaned)
        self.assertEqual(merged["original_text"], "")
        self.assertNotIn("QT-20260525-0001", merged["original_text"])
        self.assertEqual(merged["totals"], {})
        self.assertIn("excluded from quotation matching", " ".join(merged["warnings"]))

    def test_low_confidence_or_followup_metadata_is_display_only(self):
        deterministic = self.preview(b"pdf")
        base_metadata = {
            "document_type": "local_purchase_order",
            "po_references": [],
            "quotation_references": [
                {"reference": "QT-20260525-0009", "page_number": "1", "confidence": 0.05}
            ],
            "currency": "AED",
            "subtotal": "60",
            "vat_total": "3",
            "grand_total": "63",
            "totals_page_number": "1",
            "confidence": 0.95,
        }
        low_ref = _merge_mailbox_vision_preview(
            deterministic,
            {"lines": [], "warnings": [], "meta": {}, "document_metadata": base_metadata},
        )
        self.assertNotIn("QT-20260525-0009", low_ref["original_text"])
        self.assertIn("grand_total", low_ref["totals"])

        followup = _merge_mailbox_vision_preview(
            deterministic,
            {
                "lines": [],
                "warnings": [],
                "meta": {},
                "document_metadata": {
                    **base_metadata,
                    "document_type": "payment_or_follow_up",
                    "quotation_references": [
                        {"reference": "QT-20260525-0010", "page_number": "1", "confidence": 0.99}
                    ],
                },
            },
        )
        self.assertEqual(followup["original_text"], "")
        self.assertEqual(followup["totals"], {})
        self.assertIn("display-only", " ".join(followup["warnings"]))

    def test_metadata_only_mailbox_result_is_terminal_review_not_provider_retry(self):
        self.enable_ai()
        data = pdf_bytes()
        provider = FakeVisionProvider()
        provider.clean_rows = lambda **_kwargs: (
            ai_result(rows=[], document_type="payment_or_follow_up"),
            {},
        )
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            cleaned = clean_pdf_bytes_with_ai(
                data,
                self.preview(data),
                json_schema=MAILBOX_PO_VISION_JSON_SCHEMA,
                schema_name="mailbox_po_vision_parse",
            )
        self.assertEqual(cleaned["lines"], [])
        self.assertIn("metadata-only", " ".join(cleaned["warnings"]))

    @override_settings(
        QUOTATION_AI_PARSE_MAX_PDF_PAGES=10,
        QUOTATION_AI_PARSE_HARD_MAX_PDF_PAGES=50,
        QUOTATION_AI_PARSE_MAX_RENDERED_PAGES=3,
    )
    def test_mailbox_override_accepts_12_pages_but_rejects_report_sized_pdf(self):
        twelve_pages = pdf_bytes(12)
        with self.assertRaisesMessage(AIParseError, "capped at 10 pages"):
            _render_pdf_bytes_images(twelve_pages)
        images, rendered = _render_pdf_bytes_images(twelve_pages, max_pages=25)
        self.assertEqual((len(images), rendered), (3, 3))

        self.enable_ai()
        provider = FakeVisionProvider()
        twelve_preview = self.preview(twelve_pages)
        twelve_preview["meta"]["page_count"] = 12
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            cleaned = clean_pdf_bytes_with_ai(
                twelve_pages,
                twelve_preview,
                max_pages=25,
                json_schema=MAILBOX_PO_VISION_JSON_SCHEMA,
                schema_name="mailbox_po_vision_parse",
            )
        self.assertEqual(cleaned["meta"]["ai_source_page_count"], 12)
        self.assertEqual(cleaned["meta"]["ai_rendered_page_count"], 3)
        self.assertTrue(cleaned["meta"]["ai_render_truncated"])
        self.assertIn("incomplete", " ".join(cleaned["warnings"]).lower())

        with override_settings(QUOTATION_AI_PARSE_MAX_RENDERED_BYTES=1):
            with self.assertRaisesMessage(AIParseError, "image byte limit"):
                _render_pdf_bytes_images(twelve_pages, max_pages=25)

        with self.assertRaisesMessage(AIParseError, "PDF has 446 pages"):
            _render_pdf_bytes_images(pdf_bytes(446), max_pages=25)

    @override_settings(
        QUOTATION_AI_PARSE_MAX_PDF_BYTES=100,
        QUOTATION_AI_PARSE_HARD_MAX_PDF_BYTES=200,
    )
    def test_in_memory_byte_override_is_still_hard_capped(self):
        self.enable_ai()
        with self.assertRaisesMessage(AIParseError, "capped at 200 bytes"):
            clean_pdf_bytes_with_ai(
                b"%PDF-" + (b"x" * 250),
                self.preview(b"%PDF-" + (b"x" * 250)),
                max_pdf_bytes=500,
            )


@override_settings(**AI_SETTINGS)
@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
class MailboxAttachmentVisionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mailbox-vision", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.user,
            is_shared=True,
            email="vision@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )

    def enable_ai(self, enabled=True):
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_parsing_enabled = enabled
        settings_obj.ai_auto_cleanup_enabled = enabled
        settings_obj.ai_pdf_vision_enabled = enabled
        settings_obj.save()

    def attachment(self, data=b"pdf-data", **overrides):
        value = {
            "filename": "scanned-po.pdf",
            "mime_type": "application/pdf",
            "size": len(data),
            "part_id": "1",
            "_inline_data": base64.urlsafe_b64encode(data).decode("ascii").rstrip("="),
        }
        value.update(overrides)
        return value

    def deterministic_preview(self, **overrides):
        value = {
            "source_sha256": "a" * 64,
            "source_file_ref": "",
            "source_mime_type": "application/pdf",
            "parse_method": "ocr_required_not_configured_v2",
            "original_text": "",
            "meta": {"page_count": 1},
            "totals": {},
            "lines": [],
            "warnings": ["No selectable text detected. OCR is not enabled."],
        }
        value.update(overrides)
        return value

    @patch("quotations.mailbox_po_audit.clean_pdf_bytes_with_ai")
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_rowless_scanned_pdf_uses_ai_rows_for_review_only(self, parse_preview, clean):
        self.enable_ai()
        parse_preview.return_value = self.deterministic_preview()
        clean.return_value = {
            "source_file_ref": "should-be-scrubbed",
            "parse_method": "ocr_required_not_configured_v2+ai_vision_cleanup",
            "lines": [
                {
                    "raw_name": "Sterile Gauze 10cm",
                    "quantity": "12",
                    "parse_status": "parsed",
                }
            ],
            "warnings": ["AI source warning"],
            "meta": {"ai_document_notes": "LPO JLMG-PO-00028268 for QT-20260525-0001 total AED 60"},
            "document_metadata": {
                "document_type": "local_purchase_order",
                "po_references": [
                    {"reference": "JLMG-PO-00028268", "page_number": "1", "confidence": 0.98}
                ],
                "quotation_references": [
                    {"reference": "QT-20260525-0001", "page_number": "1", "confidence": 0.97}
                ],
                "currency": "AED",
                "subtotal": "60",
                "vat_total": "3",
                "grand_total": "63",
                "totals_page_number": "1",
                "confidence": 0.96,
            },
            "result_source": "ai_vision_cleanup",
            "ai_status": "ai_vision_cleanup_used",
            "provider": "openai",
            "model": "vision-model",
            "cache_hit": False,
        }

        result, byte_count = _preview_attachment(
            self.connection,
            "message-1",
            self.attachment(),
            "token",
        )

        self.assertGreater(byte_count, 0)
        clean.assert_called_once()
        self.assertEqual(result["lines"][0]["parse_status"], "needs_review")

    def test_real_12_page_raster_pdf_is_deferred_then_repaired_in_memory(self):
        self.enable_ai()
        data = raster_pdf_bytes(12)
        attachment = self.attachment(data=data, size=len(data))

        with patch("quotations.ai_parsing.get_ai_parse_provider") as provider_factory:
            deferred, deferred_bytes = _preview_attachment(
                self.connection,
                "real-raster-12",
                attachment,
                "token",
                allow_ai_vision=False,
            )
        provider_factory.assert_not_called()
        self.assertEqual(deferred_bytes, len(data))
        self.assertEqual(deferred["line_count"], 0)
        self.assertEqual(deferred["vision_repair_status"], "pending")
        self.assertEqual(deferred["meta"]["page_count"], 12)

        provider = FakeVisionProvider()
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            repaired, repaired_bytes = _preview_attachment(
                self.connection,
                "real-raster-12",
                attachment,
                "token",
                allow_ai_vision=True,
            )

        self.assertEqual(repaired_bytes, len(data))
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(len(provider.calls[0]["image_data_urls"]), 3)
        self.assertEqual(repaired["vision_repair_status"], "completed")
        self.assertEqual(repaired["line_count"], 1)
        self.assertEqual(repaired["lines"][0]["parse_status"], "needs_review")
        self.assertEqual(repaired["meta"]["ai_source_page_count"], 12)
        self.assertTrue(repaired["meta"]["ai_render_truncated"])
        self.assertEqual(repaired["lines"][0]["status"], "needs_review")
        self.assertEqual(repaired["result_source"], "ai_vision_cleanup")
        self.assertTrue(repaired["ai_review_required"])
        self.assertFalse(repaired["auto_approval_eligible"])
        self.assertEqual(repaired["source_file_ref"], "")
        self.assertIn("QT-20260525-0001", repaired["original_text"])
        self.assertIn(MAILBOX_AI_REVIEW_WARNING, repaired["warnings"])
        self.assertTrue(repaired["meta"]["mailbox_ai_vision"]["review_only"])

    @patch("quotations.mailbox_po_audit.clean_pdf_bytes_with_ai")
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_disabled_or_usable_deterministic_parse_does_not_call_vision(self, parse_preview, clean):
        self.enable_ai(False)
        parse_preview.return_value = self.deterministic_preview()
        result, _ = _preview_attachment(self.connection, "message-1", self.attachment(), "token")
        self.assertEqual(result["line_count"], 0)
        clean.assert_not_called()

        self.enable_ai(True)
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_auto_cleanup_enabled = False
        settings_obj.save(update_fields=["ai_auto_cleanup_enabled", "updated_at"])
        result, _ = _preview_attachment(
            self.connection, "message-1", self.attachment(), "token"
        )
        self.assertEqual(result["line_count"], 0)
        clean.assert_not_called()

        settings_obj.ai_auto_cleanup_enabled = True
        settings_obj.save(update_fields=["ai_auto_cleanup_enabled", "updated_at"])
        with override_settings(QUOTATION_MAILBOX_AI_VISION_ENABLED=False):
            result, _ = _preview_attachment(
                self.connection, "message-1", self.attachment(), "token"
            )
        self.assertEqual(result["line_count"], 0)
        clean.assert_not_called()

        with override_settings(QUOTATION_AI_PARSE_GLOBAL_ENABLED=False):
            result, _ = _preview_attachment(
                self.connection, "message-1", self.attachment(), "token"
            )
        self.assertEqual(result["line_count"], 0)
        clean.assert_not_called()

        parse_preview.return_value = self.deterministic_preview(
            lines=[{"raw_name": "Existing row", "quantity": "1", "parse_status": "parsed"}]
        )
        result, _ = _preview_attachment(self.connection, "message-1", self.attachment(), "token")
        self.assertEqual(result["line_count"], 1)
        clean.assert_not_called()

    @patch("quotations.mailbox_po_audit.clean_pdf_bytes_with_ai", side_effect=AIParseError("provider timeout"))
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_provider_failure_keeps_deterministic_manifest_retryable(self, parse_preview, _clean):
        self.enable_ai()
        parse_preview.return_value = self.deterministic_preview()

        result, byte_count = _preview_attachment(
            self.connection, "message-1", self.attachment(), "token"
        )

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(result["vision_repair_status"], "retryable")
        self.assertTrue(result["content_fetched"])
        self.assertEqual(result["fetched_bytes"], byte_count)
        self.assertIn("provider timeout", " ".join(result["warnings"]))

    @patch("quotations.mailbox_po_audit.clean_pdf_bytes_with_ai")
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_12_page_raster_pdf_uses_bounded_mailbox_vision_after_deterministic_parse(self, parse_preview, clean):
        self.enable_ai()
        parse_preview.return_value = self.deterministic_preview(meta={"page_count": 12})
        clean.return_value = {
            "lines": [{"raw_name": "PO item", "quantity": "2"}],
            "warnings": [],
            "meta": {},
            "result_source": "ai_vision_cleanup",
            "provider": "openai",
            "model": "vision-model",
            "cache_hit": False,
        }

        result, _ = _preview_attachment(
            self.connection, "message-12", self.attachment(), "token"
        )

        self.assertEqual(result["status"], "parsed")
        self.assertEqual(parse_preview.call_args.kwargs["max_pdf_pages_override"], 25)
        self.assertEqual(clean.call_args.kwargs["max_pages"], 25)
        self.assertEqual(clean.call_args.kwargs["max_pdf_bytes"], 10 * 1024 * 1024)
        self.assertEqual(clean.call_args.args[1]["meta"]["page_count"], 12)
        self.assertEqual(result["lines"][0]["parse_status"], "needs_review")

    @patch("quotations.mailbox_po_audit.clean_pdf_bytes_with_ai")
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_12_page_selectable_pdf_stays_deterministic(self, parse_preview, clean):
        self.enable_ai()
        parse_preview.return_value = self.deterministic_preview(
            meta={"page_count": 12},
            parse_method="pdf_text_v2",
            original_text="Sterile Gauze 10cm quantity 12",
            lines=[{"raw_name": "Sterile Gauze 10cm", "quantity": "12"}],
            warnings=[],
        )

        result, _ = _preview_attachment(
            self.connection,
            "message-12-text",
            self.attachment(),
            "token",
        )

        self.assertEqual(parse_preview.call_args.kwargs["max_pdf_pages_override"], 25)
        self.assertEqual(result["result_source"], "deterministic_parse")
        self.assertEqual(result["line_count"], 1)
        clean.assert_not_called()

    @patch(
        "quotations.mailbox_po_audit.clean_pdf_bytes_with_ai",
        side_effect=AIParseError("PDF has 446 pages. AI cleanup is capped at 25 pages."),
    )
    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_report_sized_pdf_remains_rejected(self, parse_preview, _clean):
        self.enable_ai()
        parse_preview.side_effect = ValidationError(
            "PDF has 446 pages. Maximum supported pages: 10."
        )
        result, byte_count = _preview_attachment(
            self.connection, "message-446", self.attachment(), "token"
        )
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["vision_repair_status"], "rejected")
        self.assertGreater(byte_count, 0)

    @patch("quotations.mailbox_po_audit.parse_file_preview")
    def test_mailbox_only_size_cap_accepts_six_mib_and_rejects_above_ten(self, parse_preview):
        self.enable_ai(False)
        parse_preview.return_value = self.deterministic_preview(
            original_text="Purchase Order item 1 quantity 1",
            lines=[{"raw_name": "item 1", "quantity": "1"}],
            warnings=[],
        )
        six_mib_declared = self.attachment(size=6 * 1024 * 1024)
        self.assertTrue(_is_plausible_document_attachment(six_mib_declared))
        result, _ = _preview_attachment(
            self.connection, "message-size", six_mib_declared, "token"
        )
        self.assertEqual(result["status"], "parsed")
        self.assertEqual(parse_preview.call_args.kwargs["max_bytes"], 10 * 1024 * 1024)

        too_large = self.attachment(size=(10 * 1024 * 1024) + 1)
        self.assertFalse(_is_plausible_document_attachment(too_large))
        result, byte_count = _preview_attachment(
            self.connection, "message-size", too_large, "token"
        )
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["vision_repair_status"], "rejected")
        self.assertTrue(result["manual_review_required"])
        self.assertFalse(attachment_needs_mailbox_vision_repair(result))
        self.assertEqual(byte_count, 0)

    @patch("quotations.mailbox_po_audit.parse_file_preview")
    @patch("quotations.mailbox_po_audit._decode_gmail_data")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_unknown_decoded_sizes_stop_at_cumulative_message_cap(
        self,
        _token,
        decode,
        parse_preview,
    ):
        self.enable_ai(False)
        eleven_mib = b"x" * (11 * 1024 * 1024)
        decode.side_effect = [eleven_mib, eleven_mib, b"must-not-be-decoded"]
        attachments = [
            {
                "filename": f"scan-{index}.pdf",
                "mime_type": "application/pdf",
                "size": 0,
                "part_id": str(index),
                "_inline_data": f"encoded-{index}",
            }
            for index in range(3)
        ]
        message = {
            "gmail_message_id": "unknown-size-message",
            "_attachment_refs": attachments,
            "attachment_manifest": [
                {key: value for key, value in attachment.items() if key != "_inline_data"}
                for attachment in attachments
            ],
        }

        manifest, candidates, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=True,
        )

        self.assertEqual(candidates, 3)
        self.assertEqual(decode.call_count, 2)
        self.assertEqual(fetched_bytes, 22 * 1024 * 1024)
        self.assertEqual(manifest[0]["vision_repair_status"], "rejected")
        self.assertFalse(attachment_needs_mailbox_vision_repair(manifest[0]))
        self.assertTrue(manifest[1]["content_fetched"])
        self.assertEqual(manifest[1]["fetched_bytes"], 11 * 1024 * 1024)
        self.assertEqual(manifest[2]["status"], "skipped")
        self.assertIn("total attachment byte limit", manifest[2]["reason"])
        parse_preview.assert_not_called()

    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    def test_seventeen_independent_po_attachments_are_all_audited_under_byte_cap(
        self,
        _token,
        preview,
    ):
        attachment_size = 260 * 1024
        attachments = [
            {
                "filename": f"PO-{index:05d}.pdf",
                "mime_type": "application/pdf",
                "size": attachment_size,
                "attachment_id": f"attachment-{index}",
                "part_id": str(index),
            }
            for index in range(17)
        ]

        def parsed(_connection, _message_id, attachment, _token, **_kwargs):
            return (
                {
                    **attachment,
                    "candidate": True,
                    "content_fetched": True,
                    "status": "parsed",
                    "line_count": 1,
                    "lines": [{"raw_name": attachment["filename"], "quantity": "1"}],
                },
                attachment_size,
            )

        preview.side_effect = parsed
        heartbeat = Mock()
        manifest, candidates, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            {
                "gmail_message_id": "seventeen-pos",
                "_attachment_refs": attachments,
                "attachment_manifest": attachments,
            },
            is_relevant=True,
            heartbeat=heartbeat,
        )

        self.assertEqual(candidates, 17)
        self.assertEqual(preview.call_count, 17)
        self.assertEqual(heartbeat.call_count, 34)
        self.assertEqual(fetched_bytes, 17 * attachment_size)
        self.assertEqual(len(manifest), 17)
        self.assertEqual(
            {attachment["attachment_id"] for attachment in manifest},
            {f"attachment-{index}" for index in range(17)},
        )
        self.assertTrue(all(attachment["status"] == "parsed" for attachment in manifest))

    @patch("quotations.mailbox_po_audit.get_valid_access_token")
    def test_po_wording_surfaces_unsupported_attachment_for_manual_review(self, token):
        message = {
            "gmail_message_id": "unsupported-po",
            "subject": "Purchase Order attached",
            "newest_body_text": "Please find the purchase order attached.",
            "attachment_manifest": [
                {
                    "filename": "signed-order.docx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size": 4000,
                    "attachment_id": "docx-1",
                    "part_id": "1",
                }
            ],
            "_attachment_refs": [
                {
                    "filename": "signed-order.docx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "size": 4000,
                    "attachment_id": "docx-1",
                    "part_id": "1",
                }
            ],
        }
        classification = classify_mailbox_message(message)
        self.assertEqual(classification["classification"], MailboxPOMessage.CLASS_PURCHASE_ORDER)

        manifest, candidates, fetched_bytes = hydrate_plausible_attachments(
            self.connection,
            message,
            is_relevant=classification["is_relevant"],
            surface_unsupported=(
                classification["classification"] == MailboxPOMessage.CLASS_PURCHASE_ORDER
            ),
        )

        token.assert_not_called()
        self.assertEqual((candidates, fetched_bytes), (0, 0))
        self.assertEqual(manifest[0]["status"], "manual_review")
        self.assertTrue(manifest[0]["manual_review_required"])
        self.assertIn("exact Gmail source", manifest[0]["reason"])


class MailboxAIVisionMatchingSafetyTests(TestCase):
    def test_ai_quote_reference_is_review_only_and_cannot_hide_true_item_match(self):
        received_at = timezone.now()
        inventory = MailboxPOMessage(
            gmail_message_id="ai-reference-message",
            sender="Acme Buyer <buyer@acme.example>",
            subject="Purchase Order attached",
            newest_body_text="Please proceed with the attached order.",
            sent_at=received_at,
            attachment_manifest=[
                {
                    "filename": "scanned-lpo.pdf",
                    "attachment_id": "ai-attachment",
                    "status": "parsed",
                    "result_source": "ai_vision_cleanup",
                    "original_text": (
                        "PO: LPO-1001\nQuotation: QT-20260701-0002\nGrand total: 60"
                    ),
                    "lines": [
                        {
                            "raw_name": "Sterile Gauze 10cm",
                            "quantity": "12",
                            "unit_price": "5",
                            "line_total": "60",
                            "unit": "box",
                        }
                    ],
                    "totals": {"grand_total": "60"},
                    "warnings": [MAILBOX_AI_REVIEW_WARNING],
                    "meta": {"mailbox_ai_vision": {"review_only": True}},
                }
            ],
        )
        variant = document_variants(inventory)[0]
        self.assertTrue(variant.message.quotation_references_are_review_only)
        self.assertEqual(variant.quotation_references, ("QT-20260701-0002",))

        true_quote = {
            "quote_id": 1,
            "quotation_number": "QT-20260701-0001",
            "sent_at": received_at - timedelta(days=1),
            "company_name": "Acme Medical",
            "customer_emails": ("buyer@acme.example",),
            "grand_total": "60",
            "lines": [
                {
                    "line_id": 11,
                    "name": "Sterile Gauze 10cm",
                    "quantity": "12",
                    "unit_price": "5",
                    "line_total": "60",
                    "unit": "box",
                }
            ],
        }
        referenced_but_wrong = {
            "quote_id": 2,
            "quotation_number": "QT-20260701-0002",
            "sent_at": received_at - timedelta(days=1),
            "company_name": "Acme Medical",
            "customer_emails": ("buyer@acme.example",),
            "grand_total": "60",
            "lines": [
                {
                    "line_id": 21,
                    "name": "Nitrile Examination Gloves",
                    "quantity": "12",
                    "unit_price": "5",
                    "line_total": "60",
                    "unit": "box",
                }
            ],
        }

        result = rank_message_to_quotations(
            variant.message,
            [true_quote, referenced_but_wrong],
        )

        self.assertEqual([candidate.quote_id for candidate in result.candidates], [1])
        self.assertFalse(result.candidates[0].exact_quote_reference)
        self.assertFalse(
            any(
                component.signal == "quotation_reference"
                for component in result.candidates[0].components
            )
        )


@override_settings(**AI_SETTINGS)
@patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}, clear=False)
class MailboxPDFRepairTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("mailbox-repair", is_staff=True)
        self.connection = GmailOAuthConnection.objects.create(
            user=self.user,
            is_shared=True,
            email="repair@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_parsing_enabled = True
        settings_obj.ai_auto_cleanup_enabled = True
        settings_obj.ai_pdf_vision_enabled = True
        settings_obj.save()
        now = timezone.now()
        self.run = MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.user,
            earliest_quote_at=now - timedelta(days=1),
            mailbox_cutoff_at=now,
            gmail_query="in:anywhere after:1 before:2 -from:me",
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            exhausted=True,
            completed_at=now,
        )

    def manifest(self):
        return [
            {
                "filename": "scan.pdf",
                "mime_type": "application/pdf",
                "attachment_id": "target-id",
                "part_id": "1",
                "size": 100,
                "status": "parsed",
                "content_fetched": True,
                "line_count": 0,
                "lines": [],
                "original_text": "",
                "warnings": ["No selectable text detected. OCR is not enabled."],
                "parse_method": "ocr_required_not_configured_v2",
            },
            {
                "filename": "already-good.pdf",
                "mime_type": "application/pdf",
                "attachment_id": "good-id",
                "part_id": "2",
                "size": 100,
                "status": "parsed",
                "line_count": 1,
                "lines": [{"raw_name": "Existing item", "quantity": "1"}],
                "original_text": "Existing item 1",
                "warnings": [],
            },
        ]

    def make_message(self, gmail_id="repair-message", *, member=True, manifest=None):
        message = MailboxPOMessage.objects.create(
            gmail_connection=self.connection,
            gmail_message_id=gmail_id,
            mailbox_email=self.connection.email,
            attachment_manifest=self.manifest() if manifest is None else manifest,
        )
        if member:
            MailboxPOAuditRunMessage.objects.create(audit_run=self.run, message=message)
        return message

    def fetched_message(self, gmail_id="repair-message"):
        return {
            "gmail_message_id": gmail_id,
            "_attachment_refs": [
                {
                    "filename": "scan.pdf",
                    "mime_type": "application/pdf",
                    "attachment_id": "target-id",
                    "part_id": "1",
                    "size": 100,
                },
                {
                    "filename": "already-good.pdf",
                    "mime_type": "application/pdf",
                    "attachment_id": "good-id",
                    "part_id": "2",
                    "size": 100,
                },
            ],
        }

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_repair_is_targeted_no_store_and_idempotent(self, fetch, preview, _token):
        message = self.make_message()
        outside = self.make_message("outside-run", member=False)
        fetch.return_value = self.fetched_message()
        preview.return_value = (
            {
                **self.manifest()[0],
                "status": "parsed",
                "line_count": 1,
                "lines": [{"raw_name": "Recovered item", "quantity": "3"}],
                "result_source": "ai_vision_cleanup",
                "vision_repair_status": "completed",
                "source_file_ref": "",
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run, actor=self.user)

        self.assertEqual(first["attachments_targeted"], 1)
        self.assertEqual(first["attachments_repaired"], 1)
        self.assertEqual(first["messages_updated"], 1)
        preview.assert_called_once()
        self.assertEqual(preview.call_args.args[2]["attachment_id"], "target-id")
        self.assertEqual(preview.call_args.kwargs["actor"], self.user)
        self.assertEqual(
            preview.call_args.kwargs["vision_source_identity"],
            {
                "audit_run_id": self.run.id,
                "gmail_message_id": message.gmail_message_id,
                "attachment_id": "target-id",
                "part_id": "1",
            },
        )
        message.refresh_from_db()
        outside.refresh_from_db()
        self.assertEqual(message.attachment_manifest[0]["lines"][0]["raw_name"], "Recovered item")
        self.assertEqual(message.attachment_manifest[1]["lines"][0]["raw_name"], "Existing item")
        self.assertEqual(outside.attachment_manifest, self.manifest())

        fetch.reset_mock()
        preview.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()
        preview.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_repair_uses_stable_mime_part_when_gmail_attachment_token_rotates(
        self,
        fetch,
        preview,
        _token,
    ):
        message = self.make_message()
        fetched = self.fetched_message()
        fetched["_attachment_refs"][0]["attachment_id"] = "rotated-download-token"
        fetch.return_value = fetched
        preview.return_value = (
            {
                **fetched["_attachment_refs"][0],
                "status": "parsed",
                "line_count": 1,
                "lines": [{"raw_name": "Recovered from rotated token", "quantity": "2"}],
                "vision_repair_status": "completed",
                "source_file_ref": "",
            },
            100,
        )

        summary = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(summary["attachments_repaired"], 1)
        preview.assert_called_once()
        self.assertEqual(
            preview.call_args.args[2]["attachment_id"],
            "rotated-download-token",
        )
        message.refresh_from_db()
        repaired = message.attachment_manifest[0]
        self.assertEqual(repaired["attachment_id"], "rotated-download-token")
        self.assertEqual(repaired["vision_identity_strategy"], "mime_part_v2")

    def test_only_legacy_v1_identity_failure_is_retryable_once(self):
        legacy = {
            **self.manifest()[0],
            "status": "manual_review",
            "vision_repair_status": "manual",
            "vision_repair_reason": (
                "The exact Gmail attachment part is missing or no longer unique; "
                "automatic repair stopped."
            ),
        }
        self.assertTrue(attachment_needs_mailbox_vision_repair(legacy))
        self.assertFalse(
            attachment_needs_mailbox_vision_repair(
                {**legacy, "vision_identity_strategy": "mime_part_v2"}
            )
        )

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_legacy_v1_manual_identity_failure_repairs_once_with_rotated_token(
        self,
        fetch,
        preview,
        _token,
    ):
        legacy = {
            **self.manifest()[0],
            "status": "manual_review",
            "manual_review_required": True,
            "vision_repair_status": "manual",
            "vision_repair_reason": (
                "The exact Gmail attachment part is missing or no longer unique; "
                "automatic repair stopped."
            ),
        }
        message = self.make_message(manifest=[legacy])
        fetched_ref = {
            **legacy,
            "attachment_id": "rotated-after-v1-failure",
            "status": "metadata_only",
        }
        fetch.return_value = {"_attachment_refs": [fetched_ref]}
        preview.return_value = (
            {
                **fetched_ref,
                "status": "parsed",
                "line_count": 1,
                "lines": [{"raw_name": "Recovered legacy item", "quantity": "1"}],
                "vision_repair_status": "completed",
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(first["attachments_repaired"], 1)
        message.refresh_from_db()
        repaired = message.attachment_manifest[0]
        self.assertEqual(repaired["vision_identity_strategy"], "mime_part_v2")
        self.assertEqual(repaired["vision_repair_status"], "completed")

        fetch.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_legacy_v1_manual_identity_failure_becomes_terminal_v2_if_still_ambiguous(
        self,
        fetch,
    ):
        legacy = {
            **self.manifest()[0],
            "status": "manual_review",
            "manual_review_required": True,
            "vision_repair_status": "manual",
            "vision_repair_reason": (
                "The exact Gmail attachment part is missing or no longer unique; "
                "automatic repair stopped."
            ),
        }
        message = self.make_message(manifest=[legacy])
        rotated = {**legacy, "attachment_id": "rotated-but-ambiguous"}
        fetch.return_value = {"_attachment_refs": [rotated, {**rotated}]}

        first = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(first["attachments_missing"], 1)
        message.refresh_from_db()
        terminal = message.attachment_manifest[0]
        self.assertEqual(terminal["vision_identity_strategy"], "mime_part_v2")
        self.assertEqual(terminal["vision_repair_status"], "manual")

        fetch.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_legacy_v1_identity_recovery_keeps_three_transient_provider_attempts(
        self,
        fetch,
        preview,
        _token,
    ):
        legacy = {
            **self.manifest()[0],
            "status": "manual_review",
            "manual_review_required": True,
            "vision_repair_status": "manual",
            "vision_repair_reason": (
                "The exact Gmail attachment part is missing or no longer unique; "
                "automatic repair stopped."
            ),
        }
        message = self.make_message(manifest=[legacy])
        rotated = {
            **legacy,
            "attachment_id": "rotated-before-provider-failure",
            "status": "metadata_only",
        }
        fetch.return_value = {"_attachment_refs": [rotated]}
        preview.return_value = (
            {
                **rotated,
                "status": "failed",
                "reason": "Temporary provider timeout",
                "vision_repair_status": "retryable",
                "vision_repair_reason": "Temporary provider timeout",
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run)
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        third = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(first["attachments_retryable"], 1)
        self.assertEqual(second["attachments_retryable"], 1)
        self.assertEqual(third["attachments_rejected"], 1)
        self.assertEqual(preview.call_count, 3)
        message.refresh_from_db()
        terminal = message.attachment_manifest[0]
        self.assertEqual(terminal["vision_repair_attempts"], 3)
        self.assertEqual(terminal["vision_repair_status"], "manual")
        self.assertEqual(terminal["vision_identity_strategy"], "mime_part_v2")

        fetch.reset_mock()
        fourth = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(fourth["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_terminal_page_rejection_is_annotated_once_without_losing_manifest(self, fetch, preview, _token):
        failed = {
            "filename": "report.pdf",
            "mime_type": "application/pdf",
            "attachment_id": "report-id",
            "part_id": "1",
            "size": 100,
            "status": "failed",
            "reason": "PDF has 446 pages. Maximum supported pages: 10.",
        }
        message = self.make_message(manifest=[failed])
        fetch.return_value = {
            "_attachment_refs": [{**failed, "status": "metadata_only"}]
        }
        preview.return_value = (
            {
                **failed,
                "status": "failed",
                "reason": "PDF has 446 pages. AI cleanup is capped at 25 pages.",
                "vision_repair_status": "rejected",
                "vision_repair_reason": "PDF has 446 pages. AI cleanup is capped at 25 pages.",
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(first["attachments_rejected"], 1)
        message.refresh_from_db()
        self.assertEqual(message.attachment_manifest[0]["status"], "failed")
        self.assertEqual(message.attachment_manifest[0]["vision_repair_status"], "rejected")

        fetch.reset_mock()
        preview.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()
        preview.assert_not_called()

    def test_target_predicate_includes_legacy_six_mib_skip_but_not_over_cap(self):
        legacy = {
            "filename": "580277_Azizi Mirage 1.pdf",
            "mime_type": "application/pdf",
            "size": 6 * 1024 * 1024,
            "status": "skipped",
            "reason": "Attachment exceeds the per-file audit limit.",
        }
        too_large = {**legacy, "size": (10 * 1024 * 1024) + 1}
        old_count_skip = {
            **legacy,
            "size": 260 * 1024,
            "reason": "Per-message candidate attachment limit reached.",
        }
        self.assertTrue(attachment_needs_mailbox_vision_repair(legacy))
        self.assertTrue(attachment_needs_mailbox_vision_repair(old_count_skip))
        self.assertFalse(attachment_needs_mailbox_vision_repair(too_large))

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_repair_processes_all_old_count_skips_once_with_independent_ids(
        self,
        fetch,
        preview,
        _token,
    ):
        old_skips = [
            {
                "filename": f"PO-{index}.pdf",
                "mime_type": "application/pdf",
                "attachment_id": f"old-count-{index}",
                "part_id": str(index),
                "size": 260 * 1024,
                "status": "skipped",
                "reason": "Per-message candidate attachment limit reached.",
            }
            for index in range(17)
        ]
        message = self.make_message(manifest=old_skips)
        fetch.return_value = {"_attachment_refs": old_skips}

        def repaired(_connection, _message_id, attachment, _token, **_kwargs):
            return (
                {
                    **attachment,
                    "status": "parsed",
                    "content_fetched": True,
                    "line_count": 1,
                    "lines": [{"raw_name": attachment["filename"], "quantity": "1"}],
                    "original_text": attachment["filename"],
                },
                int(attachment["size"]),
            )

        preview.side_effect = repaired
        first = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(first["attachments_targeted"], 17)
        self.assertEqual(first["attachments_repaired"], 17)
        self.assertEqual(preview.call_count, 17)
        message.refresh_from_db()
        self.assertEqual(
            {attachment["attachment_id"] for attachment in message.attachment_manifest},
            {f"old-count-{index}" for index in range(17)},
        )

        fetch.reset_mock()
        preview.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()
        preview.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_message_byte_budget_is_resumable_across_repair_invocations(
        self,
        fetch,
        preview,
        _token,
    ):
        attachment_size = 8 * 1024 * 1024
        old_skips = [
            {
                "filename": f"large-po-{index}.pdf",
                "mime_type": "application/pdf",
                "attachment_id": f"large-{index}",
                "part_id": str(index),
                "size": attachment_size,
                "status": "skipped",
                "reason": "Per-message candidate attachment limit reached.",
            }
            for index in range(3)
        ]
        self.make_message(manifest=old_skips)
        fetch.return_value = {"_attachment_refs": old_skips}

        def bounded(_connection, _message_id, attachment, _token, **kwargs):
            if kwargs["max_bytes"] < attachment_size:
                return (
                    {
                        **attachment,
                        "status": "skipped",
                        "reason": "Per-message total attachment byte limit reached.",
                        "vision_repair_status": "retryable",
                    },
                    0,
                )
            return (
                {
                    **attachment,
                    "status": "parsed",
                    "line_count": 1,
                    "lines": [{"raw_name": attachment["filename"], "quantity": "1"}],
                    "original_text": attachment["filename"],
                    "vision_repair_status": "completed",
                },
                attachment_size,
            )

        preview.side_effect = bounded
        first = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(first["attachments_repaired"], 2)
        self.assertEqual(first["attachments_retryable"], 1)
        self.assertEqual(first["repair_remaining"], 1)

        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_repaired"], 1)
        self.assertEqual(second["repair_remaining"], 0)

        fetch.reset_mock()
        third = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(third["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_repair_refuses_an_ambiguous_exact_attachment_identity(self, fetch, preview):
        message = self.make_message()
        duplicate = self.fetched_message()["_attachment_refs"][0]
        fetch.return_value = {"_attachment_refs": [duplicate, {**duplicate}]}

        summary = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(summary["attachments_missing"], 1)
        preview.assert_not_called()
        message.refresh_from_db()
        self.assertEqual(message.attachment_manifest[0]["status"], "manual_review")
        self.assertEqual(message.attachment_manifest[0]["vision_repair_status"], "manual")
        self.assertEqual(message.attachment_manifest[1], self.manifest()[1])

        fetch.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_zero_row_completed_repair_is_persisted_and_idempotent(
        self,
        fetch,
        preview,
        _token,
    ):
        message = self.make_message()
        fetch.return_value = self.fetched_message()
        preview.return_value = (
            {
                **self.manifest()[0],
                "status": "parsed",
                "line_count": 0,
                "lines": [],
                "original_text": "",
                "vision_repair_status": "completed",
                "ai_review_required": True,
                "meta": {
                    "mailbox_ai_vision": {
                        "review_only": True,
                        "document_metadata": {"document_type": "payment_or_follow_up"},
                    }
                },
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(first["attachments_repaired"], 1)
        message.refresh_from_db()
        self.assertEqual(message.attachment_manifest[0]["vision_repair_status"], "completed")
        self.assertEqual(message.attachment_manifest[0]["line_count"], 0)

        fetch.reset_mock()
        preview.reset_mock()
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(second["attachments_targeted"], 0)
        fetch.assert_not_called()
        preview.assert_not_called()

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_provider_failure_stops_after_three_persisted_attempts(
        self,
        fetch,
        preview,
        _token,
    ):
        message = self.make_message()
        fetch.return_value = self.fetched_message()
        preview.return_value = (
            {
                **self.manifest()[0],
                "status": "parsed",
                "line_count": 0,
                "lines": [],
                "original_text": "",
                "vision_repair_status": "retryable",
                "vision_repair_reason": "provider timeout",
            },
            100,
        )

        first = repair_mailbox_po_audit_pdf_vision(self.run)
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        third = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(first["attachments_retryable"], 1)
        self.assertEqual(second["attachments_retryable"], 1)
        self.assertEqual(third["attachments_rejected"], 1)
        self.assertEqual(preview.call_count, 3)
        message.refresh_from_db()
        target = message.attachment_manifest[0]
        self.assertEqual(target["vision_repair_attempts"], 3)
        self.assertEqual(target["vision_repair_status"], "manual")
        self.assertEqual(target["status"], "manual_review")

        fetch.reset_mock()
        fourth = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(fourth["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_message_fetch_failure_stops_after_three_persisted_attempts(self, fetch):
        message = self.make_message()
        fetch.side_effect = RuntimeError("Gmail 404 not found")

        first = repair_mailbox_po_audit_pdf_vision(self.run)
        second = repair_mailbox_po_audit_pdf_vision(self.run)
        third = repair_mailbox_po_audit_pdf_vision(self.run)

        self.assertEqual(first["attachments_retryable"], 1)
        self.assertEqual(second["attachments_retryable"], 1)
        self.assertEqual(third["attachments_rejected"], 1)
        self.assertEqual(fetch.call_count, 3)
        message.refresh_from_db()
        target = message.attachment_manifest[0]
        self.assertEqual(target["vision_repair_attempts"], 3)
        self.assertEqual(target["vision_repair_status"], "manual")
        self.assertIn("Gmail 404", target["vision_repair_reason"])

        fetch.reset_mock()
        fourth = repair_mailbox_po_audit_pdf_vision(self.run)
        self.assertEqual(fourth["attachments_targeted"], 0)
        fetch.assert_not_called()

    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_active_repair_lease_prevents_a_second_worker(self, fetch):
        self.make_message()
        MailboxPOAuditRun.objects.filter(pk=self.run.pk).update(
            scan_lease_token="repair-other-worker",
            scan_lease_expires_at=timezone.now() + timedelta(minutes=5),
        )

        with self.assertRaisesMessage(RuntimeError, "already being repaired"):
            repair_mailbox_po_audit_pdf_vision(self.run)

        fetch.assert_not_called()
        self.run.refresh_from_db()
        self.assertEqual(self.run.scan_lease_token, "repair-other-worker")

    @patch("quotations.mailbox_po_audit.get_valid_access_token", return_value="token")
    @patch("quotations.mailbox_po_audit._preview_attachment")
    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_newer_rescan_during_ai_io_cannot_receive_stale_manifest_merge(
        self,
        fetch,
        preview,
        _token,
    ):
        message = self.make_message()
        original_manifest = self.manifest()
        fetch.return_value = self.fetched_message()

        def start_newer_run(*_args, **_kwargs):
            now = timezone.now()
            MailboxPOAuditRun.objects.create(
                gmail_connection=self.connection,
                requested_by=self.user,
                earliest_quote_at=now - timedelta(days=1),
                mailbox_cutoff_at=now,
                gmail_query="in:anywhere after:2 before:3 -from:me",
            )
            return (
                {
                    **original_manifest[0],
                    "status": "parsed",
                    "line_count": 1,
                    "lines": [{"raw_name": "Stale recovered item", "quantity": "1"}],
                    "vision_repair_status": "completed",
                },
                100,
            )

        preview.side_effect = start_newer_run
        with self.assertRaisesMessage(ValueError, "latest audit run"):
            repair_mailbox_po_audit_pdf_vision(self.run)

        message.refresh_from_db()
        self.assertEqual(message.attachment_manifest, original_manifest)
        self.run.refresh_from_db()
        self.assertEqual(self.run.scan_lease_token, "")

    @patch("quotations.mailbox_po_audit.fetch_mailbox_message")
    def test_existing_match_run_blocks_repair_before_gmail_or_ai(self, fetch):
        self.make_message()
        MailboxPOMatchRun.objects.create(
            audit_run=self.run,
            requested_by=self.user,
        )

        with self.assertRaisesMessage(ValueError, "before reconciliation"):
            repair_mailbox_po_audit_pdf_vision(self.run)
        fetch.assert_not_called()

    def test_unavailable_ai_is_terminally_surfaced_before_reconciliation(self):
        message = self.make_message()
        reclassify_mailbox_po_audit_messages(self.run)

        with override_settings(QUOTATION_MAILBOX_AI_VISION_ENABLED=False):
            changed = mark_unavailable_mailbox_vision_for_manual_review(self.run)

        self.assertEqual(changed, 1)
        self.assertEqual(mailbox_po_audit_repair_remaining(self.run), 0)
        message.refresh_from_db()
        target = message.attachment_manifest[0]
        self.assertEqual(target["status"], "manual_review")
        self.assertEqual(target["vision_repair_status"], "manual")
        self.assertIn("Cloud AI vision was not used", target["reason"])

        match_run = reconcile_mailbox_po_audit(self.run, requested_by=self.user)
        self.assertEqual(match_run.status, MailboxPOMatchRun.STATUS_COMPLETED)

    def test_reclassification_surfaces_unsupported_purchase_order_wording(self):
        unsupported = {
            "filename": "signed-order.docx",
            "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "attachment_id": "docx-1",
            "part_id": "1",
            "size": 4000,
            "status": "metadata_only",
            "reason": "Unsupported document type for PO parsing.",
        }
        message = self.make_message(manifest=[unsupported])
        MailboxPOMessage.objects.filter(pk=message.pk).update(
            subject="Purchase Order attached",
            newest_body_text="Please find the purchase order attached.",
        )

        changed = reclassify_mailbox_po_audit_messages(self.run)

        self.assertEqual(changed, 1)
        message.refresh_from_db()
        self.assertTrue(message.is_relevant)
        self.assertEqual(message.classification, MailboxPOMessage.CLASS_PURCHASE_ORDER)
        self.assertEqual(message.attachment_manifest[0]["status"], "manual_review")
        self.assertTrue(message.attachment_manifest[0]["manual_review_required"])

    @patch(
        "quotations.management.commands.audit_shared_mailbox_lpos.reclassify_mailbox_po_audit_messages"
    )
    @patch(
        "quotations.management.commands.audit_shared_mailbox_lpos.resolve_gmail_connection"
    )
    def test_audit_command_refuses_old_completed_resume_before_canonical_mutation(
        self,
        resolve_connection,
        reclassify,
    ):
        resolve_connection.return_value = self.connection
        now = timezone.now()
        MailboxPOAuditRun.objects.create(
            gmail_connection=self.connection,
            requested_by=self.user,
            earliest_quote_at=now - timedelta(days=1),
            mailbox_cutoff_at=now,
            gmail_query="in:anywhere after:2 before:3 -from:me",
            status=MailboxPOAuditRun.STATUS_COMPLETED,
            exhausted=True,
            completed_at=now,
        )

        with self.assertRaisesMessage(CommandError, "latest audit run"):
            call_command("audit_shared_mailbox_lpos", resume_run=self.run.id)
        reclassify.assert_not_called()

    @patch("quotations.management.commands.repair_mailbox_po_pdf_vision.repair_mailbox_po_audit_pdf_vision")
    def test_management_command_targets_a_specific_run(self, repair):
        repair.return_value = {
            "audit_run_id": self.run.id,
            "attachments_retryable": 0,
            "attachments_missing": 0,
        }
        output = io.StringIO()
        call_command(
            "repair_mailbox_po_pdf_vision",
            audit_run=self.run.id,
            message_id=["gmail-1"],
            dry_run=True,
            stdout=output,
        )
        self.assertEqual(repair.call_args.args[0], self.run)
        self.assertEqual(repair.call_args.kwargs["message_ids"], ["gmail-1"])
        self.assertTrue(repair.call_args.kwargs["dry_run"])
        self.assertEqual(repair.call_args.kwargs["limit"], 20)
        self.assertEqual(repair.call_args.kwargs["actor"], self.user)
        self.assertIn(f'"audit_run_id": {self.run.id}', output.getvalue())
