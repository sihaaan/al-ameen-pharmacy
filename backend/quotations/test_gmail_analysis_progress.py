import hashlib
import json
from dataclasses import replace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .ai_parsing import AIParseError
from .gmail_analysis_progress import (
    ERROR_GMAIL_FETCH_FAILED,
    ERROR_RESULT_PERSISTENCE_FAILED,
    ERROR_UNEXPECTED_FAILURE,
    GMAIL_ANALYSIS_PROGRESS_VERSION,
    GmailAnalysisProgressBinding,
    STAGE_ANALYZING_WITH_AI,
    STAGE_FETCHING_ATTACHMENTS,
    STAGE_FETCHING_MESSAGES,
    STAGE_INSPECTING_DOCUMENTS,
    STAGE_MATCHING_COMPANY_PRODUCTS,
    STAGE_PREPARING,
    STAGE_SAVING_RESULTS,
    STAGE_VALIDATING_EVIDENCE,
    advance_gmail_analysis_progress,
    finish_gmail_analysis_progress,
    gmail_analysis_progress_projection,
    initialize_gmail_analysis_progress,
    progress_failure_category_for_stage,
)
from .gmail_inquiry_import import (
    GmailInquiryImportError,
    _mark_analysis_failed,
    _run_native_thread_analysis,
    analyze_gmail_inquiry_import,
    update_gmail_inquiry_selection,
)
from .models import (
    AIParseCache,
    GmailInquiryImport,
    GmailOAuthConnection,
)
from .serializers import GmailInquiryImportSerializer
from .workflow_features import quotation_workflow_features


PROGRESS_KEYS = {
    "version",
    "state",
    "stage",
    "attempt",
    "source_generation",
    "safe_error_category",
    "started_at",
    "updated_at",
    "completed_at",
    "retryable",
}


def _native_result(message_id, source_key):
    return {
        "messages": [
            {
                "gmail_message_id": message_id,
                "classification": "initial_inquiry",
                "usage": "used",
                "reason": "Customer request.",
                "confidence": 0.99,
            }
        ],
        "rows": [
            {
                "item_name": "Sterile gauze",
                "quantity": "2",
                "unit": "BOX",
                "customer_unit_price": "",
                "customer_line_total": "",
                "customer_vat": "",
                "operation": "added",
                "citations": [
                    {
                        "source_key": source_key,
                        "page_number": "",
                        "sheet_name": "",
                        "cell_range": "",
                        "raw_source_text": "2 boxes sterile gauze",
                    }
                ],
                "confidence": 0.99,
                "parse_status": "parsed",
                "reason": "Read from the request.",
            }
        ],
        "customer_identity": {
            "company_name": "",
            "contact_name": "",
            "contact_email": "",
            "source_keys": [],
            "confidence": 0,
            "reason": "",
        },
        "warnings": [],
        "thread_summary": "Inquiry extracted.",
    }


@override_settings(QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=True)
class GmailAnalysisProgressTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="gmail-progress-owner",
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="gmail-progress-other",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="progress@example.com",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id="progress-thread",
            anchor_message_id="progress-message",
            selected_message_ids=["progress-message"],
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
            source_fingerprint="a" * 64,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.staff,
            claimed_at=timezone.now(),
        )

    def _start_progress(self):
        self.gmail_import.status = GmailInquiryImport.STATUS_ANALYZING
        self.gmail_import.analysis_attempts += 1
        self.gmail_import.analysis_started_at = timezone.now()
        binding = initialize_gmail_analysis_progress(self.gmail_import)
        self.gmail_import.save()
        return binding

    def _simple_pipeline_values(self):
        message = {
            "gmail_message_id": self.gmail_import.anchor_message_id,
            "gmail_thread_id": self.gmail_import.gmail_thread_id,
        }
        result = {
            "message_manifest": [],
            "attachment_manifest": [],
            "evidence": [],
            "candidates": {},
            "preview": {"lines": [], "warnings": [], "meta": {}},
            "ready_for_direct_quote": False,
            "warnings": [],
            "recommended_source_keys": [],
            "thread_analysis": {},
        }
        fetched = (
            self.gmail_import.gmail_thread_id,
            [message],
            [message],
            {"canonical_anchor_message_id": self.gmail_import.anchor_message_id},
        )
        return result, fetched

    def test_projection_has_exact_safe_shape_and_never_echoes_invalid_content(self):
        binding = self._start_progress()
        self.gmail_import.analysis_progress_stage = "buyer@example.com"
        self.gmail_import.analysis_progress_error_category = (
            "Private RFQ filename.xlsx"
        )
        self.gmail_import.save()

        payload = gmail_analysis_progress_projection(self.gmail_import)

        self.assertEqual(set(payload), PROGRESS_KEYS)
        self.assertEqual(payload["version"], GMAIL_ANALYSIS_PROGRESS_VERSION)
        self.assertEqual(payload["state"], "idle")
        self.assertEqual(payload["stage"], "")
        self.assertEqual(payload["source_generation"], "")
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("buyer@example.com", serialized)
        self.assertNotIn("Private RFQ", serialized)
        self.assertNotIn(self.gmail_import.gmail_thread_id, serialized)
        self.assertNotIn(self.gmail_import.anchor_message_id, serialized)
        self.assertNotIn(self.gmail_import.source_fingerprint, serialized)
        self.assertRegex(binding.generation, r"^[0-9a-f]{32}$")

    def test_monotonic_updates_reject_stale_attempt_source_and_generation(self):
        binding = self._start_progress()

        self.assertTrue(advance_gmail_analysis_progress(binding, STAGE_PREPARING))
        self.assertTrue(
            advance_gmail_analysis_progress(binding, STAGE_ANALYZING_WITH_AI)
        )
        self.assertFalse(
            advance_gmail_analysis_progress(binding, STAGE_FETCHING_MESSAGES)
        )
        self.assertFalse(
            advance_gmail_analysis_progress(
                replace(binding, attempt=binding.attempt + 1),
                STAGE_SAVING_RESULTS,
            )
        )
        self.assertFalse(
            advance_gmail_analysis_progress(
                replace(binding, source_fingerprint="b" * 64),
                STAGE_SAVING_RESULTS,
            )
        )
        self.assertFalse(
            advance_gmail_analysis_progress(
                replace(binding, generation="c" * 32),
                STAGE_SAVING_RESULTS,
            )
        )
        self.gmail_import.refresh_from_db()
        self.assertEqual(
            self.gmail_import.analysis_progress_stage,
            STAGE_ANALYZING_WITH_AI,
        )

    def test_stale_bindings_cannot_complete_or_fail_a_newer_generation(self):
        stale_binding = self._start_progress()
        self.gmail_import.analysis_attempts += 1
        self.gmail_import.source_fingerprint = "b" * 64
        current_binding = initialize_gmail_analysis_progress(self.gmail_import)
        self.gmail_import.save()

        self.assertFalse(
            finish_gmail_analysis_progress(
                self.gmail_import,
                stale_binding,
                succeeded=True,
            )
        )
        self.assertFalse(
            finish_gmail_analysis_progress(
                self.gmail_import,
                replace(current_binding, source_fingerprint="c" * 64),
                succeeded=True,
            )
        )
        self.assertFalse(
            finish_gmail_analysis_progress(
                self.gmail_import,
                replace(current_binding, generation="d" * 32),
                succeeded=False,
                error_category=ERROR_GMAIL_FETCH_FAILED,
            )
        )
        self.assertFalse(
            _mark_analysis_failed(
                self.gmail_import.pk,
                RuntimeError("private stale failure"),
                expected_attempt=stale_binding.attempt,
                expected_fingerprint=stale_binding.source_fingerprint,
                progress_binding=stale_binding,
                progress_error_category=ERROR_GMAIL_FETCH_FAILED,
            )
        )
        self.gmail_import.refresh_from_db()
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_ANALYZING)
        self.assertEqual(
            self.gmail_import.analysis_progress_generation,
            current_binding.generation,
        )
        self.assertEqual(self.gmail_import.analysis_progress_stage, "queued")

    def test_source_selection_change_clears_all_progress_binding_fields(self):
        self._start_progress()
        self.gmail_import.status = GmailInquiryImport.STATUS_REVIEW_REQUIRED
        self.gmail_import.save(update_fields=["status", "updated_at"])

        updated = update_gmail_inquiry_selection(
            self.gmail_import,
            self.staff,
            selected_message_ids=[],
            mode=GmailInquiryImport.MODE_AI_THREAD,
        )

        self.assertEqual(updated.analysis_progress_stage, "")
        self.assertEqual(updated.analysis_progress_attempt, 0)
        self.assertEqual(updated.analysis_progress_generation, "")
        self.assertEqual(updated.analysis_progress_error_category, "")
        self.assertIsNone(updated.analysis_progress_updated_at)

    def test_progress_endpoint_is_no_store_and_uses_existing_access_scope(self):
        binding = self._start_progress()
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analysis-progress",
            args=[self.gmail_import.pk],
        )

        response = client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(set(response.data), PROGRESS_KEYS)
        self.assertEqual(response.data["source_generation"], binding.generation)
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertEqual(response["Pragma"], "no-cache")
        other_client = APIClient()
        other_client.force_authenticate(self.other_staff)
        self.assertEqual(other_client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    @override_settings(QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=False)
    def test_flag_off_returns_404_and_serializer_exposes_no_progress_state(self):
        self._start_progress()
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analysis-progress",
            args=[self.gmail_import.pk],
        )

        response = client.get(url)
        payload = GmailInquiryImportSerializer(self.gmail_import).data

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIsNone(payload["analysis_progress"])

    def test_serializer_nests_only_the_safe_projection(self):
        binding = self._start_progress()

        payload = GmailInquiryImportSerializer(self.gmail_import).data

        self.assertEqual(set(payload["analysis_progress"]), PROGRESS_KEYS)
        self.assertEqual(
            payload["analysis_progress"]["source_generation"],
            binding.generation,
        )
        self.assertNotIn(
            self.gmail_import.source_fingerprint,
            json.dumps(payload["analysis_progress"], sort_keys=True),
        )

    def test_synchronous_pipeline_reports_safe_stages_and_completes_atomically(self):
        message = {
            "gmail_message_id": self.gmail_import.anchor_message_id,
            "gmail_thread_id": self.gmail_import.gmail_thread_id,
        }
        reported_stages = []

        def build_result(*_args, **kwargs):
            callback = kwargs["progress_callback"]
            for stage in (
                STAGE_FETCHING_ATTACHMENTS,
                STAGE_INSPECTING_DOCUMENTS,
                STAGE_ANALYZING_WITH_AI,
                STAGE_VALIDATING_EVIDENCE,
                STAGE_MATCHING_COMPANY_PRODUCTS,
            ):
                reported_stages.append(stage)
                callback(stage)
            return {
                "message_manifest": [],
                "attachment_manifest": [],
                "evidence": [],
                "candidates": {},
                "preview": {"lines": [], "warnings": [], "meta": {}},
                "ready_for_direct_quote": False,
                "warnings": [],
                "recommended_source_keys": [],
                "thread_analysis": {},
            }

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                return_value=(
                    self.gmail_import.gmail_thread_id,
                    [message],
                    [message],
                    {
                        "canonical_anchor_message_id": (
                            self.gmail_import.anchor_message_id
                        )
                    },
                ),
            ),
            patch(
                "quotations.gmail_inquiry_import._build_source_analysis",
                side_effect=build_result,
            ),
        ):
            analyzed = analyze_gmail_inquiry_import(
                self.gmail_import,
                self.staff,
                force=True,
            )

        self.assertEqual(
            reported_stages,
            [
                STAGE_FETCHING_ATTACHMENTS,
                STAGE_INSPECTING_DOCUMENTS,
                STAGE_ANALYZING_WITH_AI,
                STAGE_VALIDATING_EVIDENCE,
                STAGE_MATCHING_COMPANY_PRODUCTS,
            ],
        )
        self.assertEqual(analyzed.status, GmailInquiryImport.STATUS_REVIEW_REQUIRED)
        self.assertEqual(analyzed.analysis_progress_stage, "completed")
        self.assertEqual(analyzed.analysis_progress_error_category, "")
        self.assertEqual(analyzed.analysis_progress_attempt, analyzed.analysis_attempts)
        self.assertIsNotNone(analyzed.analyzed_at)
        self.assertIsNotNone(analyzed.analysis_progress_updated_at)

    def test_failure_exposes_only_stage_category_and_final_state_is_consistent(self):
        private_failure = GmailInquiryImportError(
            "buyer@example.com Private RFQ filename.xlsx progress-message"
        )
        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                side_effect=private_failure,
            ),
        ):
            with self.assertRaises(GmailInquiryImportError):
                analyze_gmail_inquiry_import(
                    self.gmail_import,
                    self.staff,
                    force=True,
                )

        self.gmail_import.refresh_from_db()
        payload = gmail_analysis_progress_projection(self.gmail_import)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(payload["stage"], "failed")
        self.assertEqual(payload["safe_error_category"], ERROR_GMAIL_FETCH_FAILED)
        self.assertTrue(payload["retryable"])
        serialized = json.dumps(payload, sort_keys=True)
        self.assertNotIn("buyer@example.com", serialized)
        self.assertNotIn("Private RFQ", serialized)
        self.assertNotIn("progress-message", serialized)

    def test_saving_progress_write_failure_terminalizes_as_safe_failure(self):
        result, fetched = self._simple_pipeline_values()

        def raising_advance(binding, stage):
            if stage == STAGE_SAVING_RESULTS:
                raise RuntimeError("private database detail")
            return advance_gmail_analysis_progress(binding, stage)

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                return_value=fetched,
            ),
            patch(
                "quotations.gmail_inquiry_import._build_source_analysis",
                return_value=result,
            ),
            patch(
                "quotations.gmail_inquiry_import.advance_gmail_analysis_progress",
                side_effect=raising_advance,
            ),
        ):
            with self.assertRaises(GmailInquiryImportError):
                analyze_gmail_inquiry_import(
                    self.gmail_import,
                    self.staff,
                    force=True,
                )

        self.gmail_import.refresh_from_db()
        payload = gmail_analysis_progress_projection(self.gmail_import)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertEqual(payload["state"], "failed")
        self.assertEqual(
            payload["safe_error_category"],
            ERROR_RESULT_PERSISTENCE_FAILED,
        )
        self.assertNotIn("private database detail", json.dumps(payload))

    @override_settings(QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=False)
    def test_flag_off_preserves_legacy_persistence_exception_and_state(self):
        result, fetched = self._simple_pipeline_values()
        original_save = GmailInquiryImport.save

        def fail_final_save(instance, *args, **kwargs):
            if instance.status in {
                GmailInquiryImport.STATUS_READY,
                GmailInquiryImport.STATUS_REVIEW_REQUIRED,
            }:
                raise RuntimeError("legacy persistence failure")
            return original_save(instance, *args, **kwargs)

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                return_value=fetched,
            ),
            patch(
                "quotations.gmail_inquiry_import._build_source_analysis",
                return_value=result,
            ),
            patch.object(GmailInquiryImport, "save", fail_final_save),
        ):
            with self.assertRaisesRegex(RuntimeError, "legacy persistence failure"):
                analyze_gmail_inquiry_import(
                    self.gmail_import,
                    self.staff,
                    force=True,
                )

        self.gmail_import.refresh_from_db()
        self.assertEqual(
            self.gmail_import.status,
            GmailInquiryImport.STATUS_ANALYZING,
        )
        self.assertEqual(self.gmail_import.errors, [])
        self.assertEqual(self.gmail_import.analysis_progress_stage, "")
        self.assertEqual(self.gmail_import.analysis_progress_generation, "")

    def test_disabling_flag_mid_analysis_still_persists_terminal_state(self):
        result, fetched = self._simple_pipeline_values()
        disabled = override_settings(
            QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=False
        )

        def disable_during_analysis(*_args, **_kwargs):
            disabled.enable()
            self.addCleanup(disabled.disable)
            return result

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                return_value=fetched,
            ),
            patch(
                "quotations.gmail_inquiry_import._build_source_analysis",
                side_effect=disable_during_analysis,
            ),
        ):
            analyzed = analyze_gmail_inquiry_import(
                self.gmail_import,
                self.staff,
                force=True,
            )

        self.assertEqual(
            analyzed.status,
            GmailInquiryImport.STATUS_REVIEW_REQUIRED,
        )
        self.assertEqual(analyzed.analysis_progress_stage, "completed")
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analysis-progress",
            args=[self.gmail_import.pk],
        )
        self.assertEqual(client.get(url).status_code, status.HTTP_404_NOT_FOUND)

    def test_all_failure_stage_mappings_are_allowlisted_and_content_free(self):
        stages = (
            "queued",
            STAGE_PREPARING,
            STAGE_FETCHING_MESSAGES,
            STAGE_FETCHING_ATTACHMENTS,
            STAGE_INSPECTING_DOCUMENTS,
            STAGE_ANALYZING_WITH_AI,
            STAGE_VALIDATING_EVIDENCE,
            STAGE_MATCHING_COMPANY_PRODUCTS,
            STAGE_SAVING_RESULTS,
            "buyer@example.com",
        )
        categories = {
            progress_failure_category_for_stage(stage) for stage in stages
        }
        self.assertIn(ERROR_UNEXPECTED_FAILURE, categories)
        serialized = json.dumps(sorted(categories))
        self.assertNotIn("buyer@example.com", serialized)
        self.assertTrue(all(len(category) <= 64 for category in categories))

    def test_valid_cache_reports_validation_and_invalid_cache_fallback_does_not(self):
        message_id = "cache-progress-message"
        source_key = "body:cache-progress"
        message = {
            "gmail_message_id": message_id,
            "newest_body_text": "Please quote 2 boxes sterile gauze.",
            "newest_body_html": "",
            "sent_at": timezone.now(),
            "subject": "RFQ",
            "sender": "Buyer <buyer@example.com>",
            "recipients": self.connection.email,
            "is_outbound": False,
        }
        source = {
            "source_key": source_key,
            "gmail_message_id": message_id,
            "kind": "email_body",
            "filename": "",
            "mime_type": "text/plain",
            "source_sha256": hashlib.sha256(
                message["newest_body_text"].encode("utf-8")
            ).hexdigest(),
            "rows": [],
        }
        availability = {
            "provider": "openai",
            "text_model": "gpt-progress-test",
            "vision_model": "gpt-progress-test",
        }
        with (
            patch(
                "quotations.gmail_inquiry_import.settings_ai_status",
                return_value={"status": "ai_available"},
            ),
            patch(
                "quotations.gmail_inquiry_import.get_ai_parse_availability",
                return_value=availability,
            ),
            patch(
                "quotations.gmail_inquiry_import.get_ai_parse_provider"
            ) as get_provider,
        ):
            get_provider.return_value.clean_rows.return_value = (
                _native_result(message_id, source_key),
                {},
            )
            _run_native_thread_analysis(
                [message],
                [source],
                [],
                self.gmail_import,
                self.staff,
            )
            cache_stages = []
            _run_native_thread_analysis(
                [message],
                [source],
                [],
                self.gmail_import,
                self.staff,
                progress_callback=cache_stages.append,
            )
            self.assertEqual(cache_stages, [STAGE_VALIDATING_EVIDENCE])

            cached = AIParseCache.objects.get()
            envelope = dict(cached.result)
            semantic_result = dict(envelope["semantic_result"])
            semantic_result["messages"] = []
            envelope["semantic_result"] = semantic_result
            cached.result = envelope
            cached.save(update_fields=["result"])
            get_provider.return_value.clean_rows.side_effect = AIParseError(
                "private provider failure"
            )
            invalid_cache_stages = []
            with self.assertRaises(AIParseError):
                _run_native_thread_analysis(
                    [message],
                    [source],
                    [],
                    self.gmail_import,
                    self.staff,
                    progress_callback=invalid_cache_stages.append,
                )
            self.assertEqual(invalid_cache_stages, [])


class GmailAnalysisProgressFeatureFlagTests(TestCase):
    def test_projection_requires_strict_true_and_is_independent(self):
        for value, expected in (
            (True, True),
            (False, False),
            (1, False),
            ("true", False),
        ):
            with self.subTest(value=value), override_settings(
                QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=value,
                QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED=False,
                QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED=True,
                QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED=True,
            ):
                features = quotation_workflow_features()
                self.assertIs(features["gmail_analysis_progress"], expected)
                self.assertIs(features["quotation_editor_progressive_load"], True)
                self.assertIs(features["gmail_chained_actions"], False)
