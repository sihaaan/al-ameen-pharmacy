import base64
import json
import tempfile
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from PIL import Image as PILImage
from rest_framework import status
from rest_framework.test import APITestCase

from api.models import Product

from .ai_parsing import (
    AI_PARSE_JSON_SCHEMA,
    AIParseError,
    AIProviderUnavailable,
    ai_parse_contract_descriptor,
    clean_image_bytes_with_ai,
    clean_preview_with_ai,
)
from .import_parsers import (
    _validate_upload_type,
    normalize_image_bytes_for_ai,
    parse_file_preview,
)
from .models import AIParseCache, AIParseLog, Company, Inquiry, ProductAlias, Quotation, QuotationSettings
from .serializers import ImportedInquiryCreateSerializer


User = get_user_model()


def make_image_bytes(image_format="PNG", *, size=(16, 10), exif=None, save_all=False, append_images=None):
    image = PILImage.new("RGB", size, "white")
    output = BytesIO()
    save_kwargs = {}
    if exif is not None:
        save_kwargs["exif"] = exif
    if save_all:
        save_kwargs.update(save_all=True, append_images=append_images or [], duration=100, loop=0)
    image.save(output, format=image_format, **save_kwargs)
    return output.getvalue()


class RecordingVisionProvider:
    def __init__(self):
        self.calls = []

    def clean_rows(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "rows": [
                {
                    "item_name": "Gloves Medium",
                    "quantity": "5",
                    "unit": "boxes",
                    "unit_price": "12.00",
                    "vat_rate": "5",
                    "vat_amount": "3.00",
                    "line_total": "63.00",
                    "pack_info": "",
                    "notes": "",
                    "raw_source_text": "Gloves Medium 5 boxes",
                    "page_number": "1",
                    "confidence": 0.93,
                    "parse_status": "parsed",
                    "reason": "Clearly visible in the image.",
                }
            ],
            "warnings": [],
            "document_notes": "",
        }, {"input_tokens": 10, "output_tokens": 15}


class FailingVisionProvider:
    def clean_rows(self, **kwargs):
        raise AIParseError("Vision provider could not read this screenshot.")


class EmptyVisionProvider:
    def clean_rows(self, **kwargs):
        return {"rows": [], "warnings": []}, {
            "input_tokens": 21,
            "output_tokens": 3,
        }


class InquiryImageParserTests(TestCase):
    def upload(self, filename, data, content_type):
        return SimpleUploadedFile(filename, data, content_type=content_type)

    def test_png_jpeg_and_webp_create_private_image_previews(self):
        cases = [
            ("inquiry.png", "PNG", "image/png"),
            ("inquiry.jpg", "JPEG", "image/jpeg"),
            ("inquiry.webp", "WEBP", "image/webp"),
        ]
        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                for filename, image_format, mime_type in cases:
                    with self.subTest(filename=filename):
                        data = make_image_bytes(image_format)
                        preview = parse_file_preview(self.upload(filename, data, mime_type))
                        self.assertEqual(preview["source_type"], Inquiry.SOURCE_TYPE_IMAGE)
                        self.assertEqual(preview["source_mime_type"], mime_type)
                        self.assertEqual(preview["source_file_size"], len(data))
                        self.assertEqual(preview["parse_method"], "image_vision_input_v1")
                        self.assertEqual(preview["lines"], [])
                        self.assertTrue(preview["source_file_ref"].startswith("inquiry_sources/"))
                        self.assertEqual(preview["meta"]["image_width"], 16)
                        self.assertEqual(preview["meta"]["image_height"], 10)
                        self.assertTrue(preview["meta"]["requires_vision"])

    def test_image_extension_must_match_decoded_format(self):
        upload = self.upload("renamed.jpg", make_image_bytes("PNG"), "image/jpeg")
        with self.assertRaisesMessage(ValidationError, "does not match the decoded PNG"):
            parse_file_preview(upload)

    def test_renamed_pdf_and_truncated_image_are_rejected(self):
        renamed_pdf = self.upload("not-an-image.png", b"%PDF-1.4\n", "image/png")
        with self.assertRaisesMessage(ValidationError, "Invalid image file"):
            parse_file_preview(renamed_pdf)

        truncated = self.upload("truncated.png", make_image_bytes("PNG")[:24], "image/png")
        with self.assertRaisesMessage(ValidationError, "Invalid image file"):
            parse_file_preview(truncated)

    @override_settings(QUOTATION_IMPORT_MAX_UPLOAD_BYTES=20)
    def test_image_obeys_existing_upload_byte_limit(self):
        upload = self.upload("large.png", make_image_bytes("PNG"), "image/png")
        with self.assertRaisesMessage(ValidationError, "too large"):
            parse_file_preview(upload)

    @override_settings(QUOTATION_IMPORT_MAX_IMAGE_PIXELS=15)
    def test_image_pixel_limit_blocks_decompression_bombs(self):
        upload = self.upload("too-many-pixels.png", make_image_bytes("PNG", size=(4, 4)), "image/png")
        with self.assertRaisesMessage(ValidationError, "too many pixels"):
            parse_file_preview(upload)

    @override_settings(QUOTATION_IMPORT_MAX_IMAGE_DIMENSION=15)
    def test_image_dimension_limit_rejects_extreme_edges(self):
        upload = self.upload("too-wide.png", make_image_bytes("PNG", size=(16, 1)), "image/png")
        with self.assertRaisesMessage(ValidationError, "dimensions are too large"):
            parse_file_preview(upload)

    def test_animated_webp_is_rejected(self):
        second_frame = PILImage.new("RGB", (16, 10), "black")
        try:
            data = make_image_bytes("WEBP", save_all=True, append_images=[second_frame])
        except OSError as exc:  # pragma: no cover - depends on Pillow build features
            self.skipTest(f"Animated WebP encoder unavailable: {exc}")
        upload = self.upload("animated.webp", data, "image/webp")
        with self.assertRaisesMessage(ValidationError, "multi-frame"):
            parse_file_preview(upload)

    def test_document_only_validator_still_rejects_images(self):
        with self.assertRaisesMessage(ValidationError, "Upload .xlsx"):
            _validate_upload_type(make_image_bytes("PNG"), "historical.png")

    def test_ai_normalization_applies_exif_orientation_and_strips_metadata(self):
        exif = PILImage.Exif()
        exif[274] = 6  # Rotate 90 degrees clockwise for display.
        exif[270] = "private note"
        data = make_image_bytes("JPEG", size=(12, 6), exif=exif)

        normalized, meta = normalize_image_bytes_for_ai(data, "phone-photo.jpg")
        with PILImage.open(BytesIO(normalized)) as image:
            self.assertEqual(image.format, "PNG")
            self.assertEqual(image.size, (6, 12))
            self.assertFalse(image.getexif())
        self.assertEqual(meta["normalized_width"], 6)
        self.assertEqual(meta["normalized_height"], 12)


@override_settings(
    QUOTATION_AI_PARSE_GLOBAL_ENABLED=True,
    QUOTATION_AI_PARSE_PROVIDER="openai",
    QUOTATION_AI_PARSE_TEXT_MODEL="test-text-model",
    QUOTATION_AI_PARSE_VISION_MODEL="test-vision-model",
)
class InquiryImageAITests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="image-ai-staff", is_staff=True)
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_parsing_enabled = True
        settings_obj.ai_pdf_vision_enabled = True
        settings_obj.save(update_fields=["ai_parsing_enabled", "ai_pdf_vision_enabled", "updated_at"])

    def preview(self, data, *, filename="phone-inquiry.jpg", source_file_ref=""):
        return {
            "source_type": Inquiry.SOURCE_TYPE_IMAGE,
            "source_filename": filename,
            "source_mime_type": "image/jpeg",
            "source_sha256": "",
            "source_file_ref": source_file_ref,
            "source_file_size": len(data),
            "parse_method": "image_vision_input_v1",
            "original_text": "",
            "lines": [],
            "warnings": [],
            "meta": {"requires_vision": True},
        }

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_text_cache_rebinds_current_upload_and_omits_source_payload(self):
        provider = RecordingVisionProvider()
        first_preview = {
            "source_type": Inquiry.SOURCE_TYPE_EXCEL,
            "source_filename": "customer-request.xlsx",
            "source_mime_type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "source_sha256": "a" * 64,
            "source_file_ref": "inquiry_sources/first/customer-request.xlsx",
            "source_file_size": 2048,
            "parse_method": "openpyxl_structured_v2",
            "original_text": "PRIVATE BUYER NOTE THAT IS NOT PART OF AN ITEM ROW",
            "lines": [
                {
                    "raw_name": "Gloves Medium",
                    "quantity": "5",
                    "unit": "boxes",
                    "raw_line": "Gloves Medium 5 boxes",
                }
            ],
            "warnings": [],
            "meta": {
                "source_file_ref": "inquiry_sources/first/customer-request.xlsx",
                "source_file_size": 2048,
                "selected_sheets": [{"sheet_name": "Request", "selected": True}],
                "ai_cleanup_rejected": True,
                "ai_cleanup_rejection_reason": "client-forged-on-cache-miss",
            },
        }
        second_preview = {
            **first_preview,
            "source_file_ref": "inquiry_sources/second/customer-request.xlsx",
            "meta": {
                **first_preview["meta"],
                "source_file_ref": "inquiry_sources/stale-browser-value.xlsx",
                "current_upload_marker": "second-upload",
                "ai_provider": "client-forged-provider",
                "ai_usage": {"private_trace": "must-not-win"},
                "ai_cleanup_rejected": True,
                "ai_cleanup_rejection_reason": "client-forged-reason",
            },
        }

        with patch(
            "quotations.ai_parsing.get_ai_parse_provider",
            return_value=provider,
        ):
            first = clean_preview_with_ai(
                first_preview,
                actor=self.staff,
                requested_mode="text",
            )
            self.assertNotIn("ai_cleanup_rejected", first["meta"])
            self.assertNotIn("ai_cleanup_rejection_reason", first["meta"])

            cached = AIParseCache.objects.get()
            cached_payload = cached.result
            for field in (
                "source_type",
                "source_filename",
                "source_mime_type",
                "source_sha256",
                "source_file_ref",
                "source_file_size",
                "parse_method",
                "original_text",
                "cache_hit",
            ):
                self.assertNotIn(field, cached_payload)
            serialized_cache = json.dumps(cached_payload, sort_keys=True)
            self.assertNotIn("PRIVATE BUYER NOTE", serialized_cache)
            self.assertNotIn("inquiry_sources/first", serialized_cache)
            self.assertNotIn("selected_sheets", serialized_cache)
            self.assertNotIn("ai_usage", cached_payload["meta"])
            self.assertEqual(cached_payload["lines"][0]["raw_line"], "Gloves Medium 5 boxes")

            # Legacy rows may still contain an earlier upload's full payload.
            # Reads must filter them without requiring destructive deletion.
            cached.result = {
                **cached_payload,
                "source_file_ref": "inquiry_sources/legacy/private.xlsx",
                "original_text": "LEGACY PRIVATE BODY",
                "legacy_private_key": "LEGACY PRIVATE VALUE",
                "meta": {
                    **cached_payload["meta"],
                    "source_file_ref": "inquiry_sources/legacy/private.xlsx",
                    "legacy_private_meta": "LEGACY PRIVATE META",
                    "ai_usage": {"provider_private_trace": "LEGACY TRACE"},
                },
            }
            cached.save(update_fields=["result", "updated_at"])

            second = clean_preview_with_ai(
                second_preview,
                actor=self.staff,
                requested_mode="text",
            )

        self.assertFalse(first["cache_hit"])
        self.assertTrue(second["cache_hit"])
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(
            second["source_file_ref"],
            "inquiry_sources/second/customer-request.xlsx",
        )
        self.assertEqual(
            second["meta"]["source_file_ref"],
            "inquiry_sources/second/customer-request.xlsx",
        )
        self.assertEqual(second["meta"]["current_upload_marker"], "second-upload")
        self.assertEqual(second["meta"]["ai_provider"], "openai")
        self.assertEqual(second["meta"]["ai_usage"], {})
        self.assertNotIn("ai_cleanup_rejected", second["meta"])
        self.assertNotIn("ai_cleanup_rejection_reason", second["meta"])
        self.assertNotIn("legacy_private_key", second)
        self.assertNotIn("legacy_private_meta", second["meta"])
        self.assertEqual(
            second["parse_method"],
            "openpyxl_structured_v2+ai_text_cleanup",
        )

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_in_memory_image_uses_normalized_vision_input_and_cache(self):
        exif = PILImage.Exif()
        exif[274] = 6
        data = make_image_bytes("JPEG", size=(12, 6), exif=exif)
        provider = RecordingVisionProvider()
        parsed = parse_file_preview(
            SimpleUploadedFile("phone-inquiry.jpg", data, content_type="image/jpeg"),
            store_source=False,
        )
        self.assertEqual(parsed["source_file_ref"], "")
        parsed["meta"] = {
            **parsed["meta"],
            "ai_mode": "text",
            "ai_provider": "client-forged-provider",
            "ai_model": "client-forged-model",
            "ai_usage": {"input_tokens": 999999},
        }

        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            first = clean_image_bytes_with_ai(
                data,
                parsed,
                actor=self.staff,
            )
            second = clean_image_bytes_with_ai(
                data,
                {**parsed, "source_file_ref": "inquiry_sources/second.jpg"},
                actor=self.staff,
            )

        self.assertEqual(first["source_type"], Inquiry.SOURCE_TYPE_IMAGE)
        self.assertEqual(first["source_file_ref"], "")
        self.assertEqual(first["source_mime_type"], "image/jpeg")
        self.assertEqual(first["result_source"], "ai_vision_cleanup")
        self.assertEqual(first["lines"][0]["raw_name"], "Gloves Medium")
        self.assertEqual(first["meta"]["ai_normalized_width"], 6)
        self.assertEqual(first["meta"]["ai_normalized_height"], 12)
        self.assertEqual(first["meta"]["ai_mode"], "vision")
        self.assertEqual(first["meta"]["ai_provider"], "openai")
        self.assertEqual(first["meta"]["ai_model"], "test-vision-model")
        self.assertEqual(first["meta"]["ai_usage"], {"input_tokens": 10, "output_tokens": 15})
        self.assertTrue(second["cache_hit"])
        self.assertEqual(second["source_filename"], "phone-inquiry.jpg")
        self.assertEqual(second["source_file_ref"], "inquiry_sources/second.jpg")
        self.assertEqual(len(provider.calls), 1)
        call = provider.calls[0]
        self.assertEqual(call["mode"], "vision")
        self.assertEqual(len(call["image_data_urls"]), 1)
        self.assertTrue(call["image_data_urls"][0].startswith("data:image/png;base64,"))
        normalized = base64.b64decode(call["image_data_urls"][0].split(",", 1)[1])
        with PILImage.open(BytesIO(normalized)) as image:
            self.assertEqual(image.size, (6, 12))
            self.assertFalse(image.getexif())
        self.assertIn("untrusted document content", call["instructions"])
        self.assertEqual(AIParseLog.objects.filter(mode="vision", success=True).count(), 2)
        provider_log, cache_log = list(
            AIParseLog.objects.filter(mode="vision", success=True).order_by("created_at", "id")
        )
        provider_observation = provider_log.usage["observability"]
        expected_contract = ai_parse_contract_descriptor(
            pipeline_version="manual_ai_cleanup_v1",
            schema_name=call["schema_name"],
            instructions=call["instructions"],
            schema=call["json_schema"] or AI_PARSE_JSON_SCHEMA,
        )
        self.assertEqual(provider_observation["route"], "manual")
        self.assertEqual(provider_observation["contract"], expected_contract)
        self.assertTrue(provider_observation["provider_call_attempted"])
        self.assertFalse(provider_observation["application_cache_hit"])
        self.assertEqual(provider_observation["cost_basis"]["input_tokens"], 10)
        self.assertEqual(provider_observation["cost_basis"]["output_tokens"], 15)
        self.assertEqual(provider_observation["source_shape"]["image_count"], 1)
        self.assertTrue(
            {"source_preparation", "cache_lookup", "provider", "validation", "cache_write", "total"}
            <= set(provider_observation["timings_ms"])
        )
        cache_observation = cache_log.usage["observability"]
        self.assertTrue(cache_observation["application_cache_hit"])
        self.assertFalse(cache_observation["provider_call_attempted"])
        self.assertEqual(cache_observation["cost_basis"]["total_tokens"], 0)
        persisted_metrics = json.dumps(
            [provider_log.usage, cache_log.usage],
            sort_keys=True,
        )
        self.assertNotIn("phone-inquiry.jpg", persisted_metrics)
        self.assertNotIn("inquiry_sources/second.jpg", persisted_metrics)
        self.assertEqual(ProductAlias.objects.count(), 0)
        self.assertEqual(Quotation.objects.count(), 0)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_empty_ai_result_retains_usage_on_failure_audit_log(self):
        data = make_image_bytes("PNG")
        parsed = parse_file_preview(
            SimpleUploadedFile(
                "empty-result.png",
                data,
                content_type="image/png",
            ),
            store_source=False,
        )

        with patch(
            "quotations.ai_parsing.get_ai_parse_provider",
            return_value=EmptyVisionProvider(),
        ):
            with self.assertRaisesMessage(
                AIParseError,
                "did not return any item rows",
            ):
                clean_image_bytes_with_ai(data, parsed, actor=self.staff)

        log = AIParseLog.objects.get(success=False, mode="vision")
        self.assertEqual(log.usage["input_tokens"], 21)
        self.assertEqual(log.usage["output_tokens"], 3)
        observation = log.usage["observability"]
        self.assertEqual(observation["outcome"], "failure")
        self.assertEqual(observation["failure_stage"], "validation")
        self.assertTrue(observation["provider_call_attempted"])
        self.assertEqual(observation["cost_basis"]["total_tokens"], 24)
        self.assertGreaterEqual(observation["timings_ms"]["total"], 0)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_private_image_preview_auto_selects_vision_mode(self):
        data = make_image_bytes("PNG")
        provider = RecordingVisionProvider()
        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                parsed = parse_file_preview(
                    SimpleUploadedFile("screenshot.png", data, content_type="image/png")
                )
                with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
                    cleaned = clean_preview_with_ai(parsed, actor=self.staff, requested_mode="auto")

        self.assertEqual(cleaned["result_source"], "ai_vision_cleanup")
        self.assertEqual(provider.calls[0]["mode"], "vision")
        self.assertEqual(len(provider.calls[0]["image_data_urls"]), 1)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_ai_cache_key_includes_prompt_and_schema_contract(self):
        data = make_image_bytes("PNG")
        parsed = parse_file_preview(
            SimpleUploadedFile("cache-contract.png", data, content_type="image/png"),
            store_source=False,
        )
        provider = RecordingVisionProvider()
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with patch("quotations.ai_parsing._ai_instructions", return_value="contract version one"):
                first = clean_image_bytes_with_ai(data, parsed, actor=self.staff)
            with patch("quotations.ai_parsing._ai_instructions", return_value="contract version two"):
                second = clean_image_bytes_with_ai(data, parsed, actor=self.staff)
            with patch(
                "quotations.ai_parsing.MANUAL_AI_PIPELINE_VERSION",
                "manual_ai_cleanup_v2",
            ), patch(
                "quotations.ai_parsing._ai_instructions",
                return_value="contract version two",
            ):
                third = clean_image_bytes_with_ai(data, parsed, actor=self.staff)

        self.assertFalse(first["cache_hit"])
        self.assertFalse(second["cache_hit"])
        self.assertFalse(third["cache_hit"])
        self.assertEqual(len(provider.calls), 3)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_private_image_storage_read_error_is_a_controlled_ai_error(self):
        provider = RecordingVisionProvider()
        data = make_image_bytes("PNG")
        with patch("quotations.ai_parsing.read_private_ref", side_effect=OSError("storage unavailable")):
            with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
                with self.assertRaisesMessage(AIParseError, "could not read the source image safely"):
                    clean_preview_with_ai(
                        self.preview(
                            data,
                            filename="screenshot.png",
                            source_file_ref="inquiry_sources/screenshot.png",
                        ),
                        actor=self.staff,
                        requested_mode="auto",
                    )
        self.assertEqual(provider.calls, [])

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_disabled_vision_fails_without_provider_call(self):
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_pdf_vision_enabled = False
        settings_obj.save(update_fields=["ai_pdf_vision_enabled", "updated_at"])
        provider = RecordingVisionProvider()
        data = make_image_bytes("PNG")
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with self.assertRaisesMessage(AIProviderUnavailable, "vision cleanup is disabled"):
                clean_image_bytes_with_ai(
                    data,
                    self.preview(data, filename="screenshot.png"),
                    actor=self.staff,
                )
        self.assertEqual(provider.calls, [])

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_stored_preview_path_never_falls_back_to_text_without_image_bytes(self):
        provider = RecordingVisionProvider()
        data = make_image_bytes("PNG")
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with self.assertRaisesMessage(AIParseError, "not available in private storage"):
                clean_preview_with_ai(
                    self.preview(data, filename="missing.png", source_file_ref=""),
                    actor=self.staff,
                    requested_mode="auto",
                )
        self.assertEqual(provider.calls, [])

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_image_preview_rejects_text_only_cleanup(self):
        provider = RecordingVisionProvider()
        data = make_image_bytes("PNG")
        with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
            with self.assertRaisesMessage(AIProviderUnavailable, "require Vision AI"):
                clean_preview_with_ai(
                    self.preview(data, filename="screenshot.png", source_file_ref="inquiry_sources/a.png"),
                    actor=self.staff,
                    requested_mode="text",
                )
        self.assertEqual(provider.calls, [])

    def test_imported_inquiry_serializer_accepts_image_source(self):
        company = Company.objects.create(name="Screenshot Customer")
        serializer = ImportedInquiryCreateSerializer(
            data={
                "company": company.id,
                "source_type": Inquiry.SOURCE_TYPE_IMAGE,
                "source_filename": "request.png",
                "source_mime_type": "image/png",
                "lines": [
                    {
                        "raw_name": "Gloves Medium",
                        "raw_line": "Gloves Medium 5 boxes",
                        "quantity": "5",
                        "unit": "boxes",
                    }
                ],
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        self.assertEqual(serializer.validated_data["source_type"], Inquiry.SOURCE_TYPE_IMAGE)


@override_settings(
    QUOTATION_AI_PARSE_GLOBAL_ENABLED=True,
    QUOTATION_AI_PARSE_PROVIDER="openai",
    QUOTATION_AI_PARSE_TEXT_MODEL="test-text-model",
    QUOTATION_AI_PARSE_VISION_MODEL="test-vision-model",
)
class InquiryImageParseFileAPITests(APITestCase):
    def setUp(self):
        self.staff = User.objects.create_user(username="image-api-staff", is_staff=True)
        self.company = Company.objects.create(name="Image API Customer")
        self.product = Product.objects.create(name="Medical Gloves Medium", price="12.00", status="draft")
        ProductAlias.objects.create(
            company=self.company,
            product=self.product,
            alias="Gloves Medium",
            created_by=self.staff,
        )
        settings_obj = QuotationSettings.get_solo()
        settings_obj.ai_parsing_enabled = True
        settings_obj.ai_pdf_vision_enabled = True
        settings_obj.save(update_fields=["ai_parsing_enabled", "ai_pdf_vision_enabled", "updated_at"])
        self.client.force_authenticate(self.staff)
        self.url = reverse("quotation-inquiry-parse-file")

    def upload(self):
        return SimpleUploadedFile(
            "customer-screenshot.png",
            make_image_bytes("PNG"),
            content_type="image/png",
        )

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_image_upload_returns_direct_ai_rows_with_company_matches(self):
        provider = RecordingVisionProvider()
        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=provider):
                    response = self.client.post(
                        self.url,
                        {"file": self.upload(), "company": self.company.id},
                        format="multipart",
                    )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["source_type"], Inquiry.SOURCE_TYPE_IMAGE)
        self.assertEqual(response.data["result_source"], "ai_vision_cleanup")
        self.assertNotIn("ai_candidate", response.data)
        self.assertEqual(len(response.data["lines"]), 1)
        self.assertEqual(response.data["lines"][0]["raw_name"], "Gloves Medium")
        self.assertEqual(response.data["lines"][0]["matched_product"], self.product.id)
        self.assertEqual(response.data["lines"][0]["match_status"], "confirmed")
        self.assertEqual(len(provider.calls), 1)
        self.assertEqual(provider.calls[0]["mode"], "vision")
        self.assertEqual(len(provider.calls[0]["image_data_urls"]), 1)

    @patch.dict("os.environ", {"OPENAI_API_KEY": "test-key"}, clear=False)
    def test_image_ai_failure_returns_clear_400_without_saving_an_inquiry(self):
        with tempfile.TemporaryDirectory() as private_root:
            with override_settings(QUOTATION_PRIVATE_STORAGE_ROOT=private_root):
                with patch("quotations.ai_parsing.get_ai_parse_provider", return_value=FailingVisionProvider()):
                    response = self.client.post(
                        self.url,
                        {"file": self.upload(), "company": self.company.id},
                        format="multipart",
                    )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Vision provider could not read", response.data["detail"])
        self.assertEqual(response.data["ai_status"], "ai_failed_using_original_parse")
        self.assertEqual(response.data["ai_status_label"], "The image could not be read with Vision AI.")
        self.assertEqual(Inquiry.objects.count(), 0)
        self.assertEqual(AIParseLog.objects.filter(success=False, mode="vision").count(), 1)
