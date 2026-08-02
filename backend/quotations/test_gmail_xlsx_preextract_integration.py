import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from quotations.gmail_inquiry_import import (
    _maybe_run_xlsx_preextract_shadow,
    _persist_xlsx_preextract_shadow_metric,
    _run_native_thread_analysis,
)
from quotations.gmail_xlsx_preextract_runner import (
    GMAIL_XLSX_SHADOW_CACHE_NAMESPACE,
    GMAIL_XLSX_SHADOW_METRICS_VERSION,
    GMAIL_XLSX_SHADOW_PIPELINE_VERSION,
    GMAIL_XLSX_SHADOW_PROMPT_VERSION,
    GMAIL_XLSX_SHADOW_SCHEMA_NAME,
)
from quotations.gmail_xlsx_preextract_shadow import SCHEMA_VERSION
from quotations.models import AIParseCache, AIParseLog, GmailInquiryImport


class GmailXlsxPreextractIntegrationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="xlsx-shadow-employee",
            password="local-test-only",
            is_staff=True,
        )
        self.messages = [
            {
                "gmail_message_id": "private-message-id",
                "newest_body_text": "PRIVATE EMAIL CONTENT",
                "is_outbound": False,
            }
        ]
        self.sources = [
            {
                "source_key": "private-xlsx-source-key",
                "gmail_message_id": "private-message-id",
                "kind": "attachment",
                "filename": "private-request.xlsx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "source_sha256": "e" * 64,
                "rows": [],
                "line_count": 0,
            }
        ]
        self.file_inputs = [
            {
                "source_key": "private-xlsx-source-key",
                "filename": "private-request.xlsx",
                "mime_type": (
                    "application/vnd.openxmlformats-officedocument."
                    "spreadsheetml.sheet"
                ),
                "content": b"PK\x03\x04 PRIVATE XLSX",
                "size": 16,
            }
        ]
        self.baseline = {
            "messages": {
                "private-message-id": {
                    "classification": "initial_inquiry",
                    "usage": "used",
                    "reason": "PRIVATE REASON",
                    "confidence": 1.0,
                }
            },
            "rows": [],
            "warnings": [],
            "customer_identity": {
                "company_name": "",
                "contact_name": "",
                "contact_email": "",
                "source_keys": [],
            },
        }
        self.native_schema = {
            "type": "object",
            "properties": {"rows": {"type": "array"}},
        }

    def helper_kwargs(self, **overrides):
        values = {
            "messages": self.messages,
            "sources": self.sources,
            "file_inputs": self.file_inputs,
            "gmail_import": SimpleNamespace(
                pk=41,
                source_fingerprint="f" * 64,
                analysis_attempts=2,
                analysis_progress_generation="a" * 32,
            ),
            "actor": self.actor,
            "baseline_result": self.baseline,
            "provider_name": "mock-provider",
            "model": "mock-model",
            "baseline_instructions": "PRIVATE BASELINE INSTRUCTIONS",
            "baseline_text_context": "PRIVATE COMPLETE THREAD",
            "native_schema": self.native_schema,
            "provider_runner": Mock(),
        }
        values.update(overrides)
        return values

    def test_rollout_setting_defaults_to_disabled(self):
        self.assertIs(
            settings.QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED,
            False,
        )

    @override_settings(
        QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED=False,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import.run_xlsx_preextract_shadow")
    def test_default_off_does_no_shadow_work(self, run_shadow):
        provider_runner = Mock()

        result = _maybe_run_xlsx_preextract_shadow(
            **self.helper_kwargs(provider_runner=provider_runner)
        )

        self.assertIsNone(result)
        run_shadow.assert_not_called()
        provider_runner.assert_not_called()
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False,
    )
    @patch("quotations.gmail_inquiry_import._validate_native_thread_result")
    @patch("quotations.gmail_inquiry_import.run_xlsx_preextract_shadow")
    def test_enabled_helper_uses_copies_and_heartbeat(self, run_shadow, validate):
        original_messages = copy.deepcopy(self.messages)
        original_sources = copy.deepcopy(self.sources)
        original_files = copy.deepcopy(self.file_inputs)
        original_baseline = copy.deepcopy(self.baseline)
        original_schema = copy.deepcopy(self.native_schema)
        progress = Mock()

        def validate_copy(_raw, messages, sources):
            messages[0]["newest_body_text"] = "MUTATED"
            sources[0]["filename"] = "MUTATED"
            return copy.deepcopy(self.baseline)

        validate.side_effect = validate_copy

        def exercise_dependencies(**kwargs):
            kwargs["file_inputs"][0]["filename"] = "MUTATED"
            kwargs["baseline_result"]["rows"] = ["MUTATED"]
            kwargs["native_schema"]["properties"] = {}
            kwargs["native_validator"]({"rows": []})
            kwargs["heartbeat"]()
            return {"status": "success"}

        run_shadow.side_effect = exercise_dependencies

        result = _maybe_run_xlsx_preextract_shadow(
            **self.helper_kwargs(progress_callback=progress)
        )

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(self.messages, original_messages)
        self.assertEqual(self.sources, original_sources)
        self.assertEqual(self.file_inputs, original_files)
        self.assertEqual(self.baseline, original_baseline)
        self.assertEqual(self.native_schema, original_schema)
        progress.assert_called_once_with("analyzing_with_ai")

    @override_settings(
        QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import.run_xlsx_preextract_shadow")
    def test_shadow_exception_is_non_authoritative(self, run_shadow):
        run_shadow.side_effect = RuntimeError(
            "PRIVATE EMAIL CONTENT private@example.test"
        )

        result = _maybe_run_xlsx_preextract_shadow(**self.helper_kwargs())

        self.assertIsNone(result)
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metric_persistence_resanitizes_and_stores_no_content(self):
        report = {
            "version": GMAIL_XLSX_SHADOW_METRICS_VERSION,
            "status": "success",
            "decision": "compared",
            "failure_category": "",
            "provider_call_attempted": True,
            "contract": {
                "pipeline_version": GMAIL_XLSX_SHADOW_PIPELINE_VERSION,
                "schema_name": GMAIL_XLSX_SHADOW_SCHEMA_NAME,
                "prompt_version": GMAIL_XLSX_SHADOW_PROMPT_VERSION,
                "cache_namespace": GMAIL_XLSX_SHADOW_CACHE_NAMESPACE,
                "preextract_schema": SCHEMA_VERSION,
                "prompt_sha256": "b" * 64,
                "schema_sha256": "c" * 64,
                "contract_sha256": "d" * 64,
            },
            "comparison": {
                "row_recall_bp": 10_000,
                "raw_item": "PRIVATE ITEM CONTENT",
            },
            "usage": {
                "input_tokens": 123,
                "raw_output": "PRIVATE PROVIDER OUTPUT",
            },
            "raw_result": "PRIVATE RAW RESULT",
        }

        _persist_xlsx_preextract_shadow_metric(
            report,
            actor=self.actor,
            provider_name="mock-provider",
            model="mock-model",
            binding_sha256="f" * 64,
        )

        log = AIParseLog.objects.get()
        encoded = json.dumps(log.usage, sort_keys=True)
        self.assertTrue(log.success)
        self.assertEqual(log.error, "")
        self.assertEqual(log.source_type, "gmail_xlsx_preextract_shadow")
        self.assertEqual(log.source_sha256, "f" * 64)
        self.assertEqual(log.context_hash, "d" * 64)
        self.assertEqual(
            log.usage["shadow_experiment"]["comparison"]["row_recall_bp"],
            10_000,
        )
        for prohibited in (
            "PRIVATE ITEM",
            "PRIVATE PROVIDER",
            "PRIVATE RAW",
            "private@example.test",
        ):
            self.assertNotIn(prohibited, encoded)

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False)
    def test_metric_flag_off_writes_nothing(self):
        _persist_xlsx_preextract_shadow_metric(
            {"status": "success"},
            actor=self.actor,
            provider_name="mock-provider",
            model="mock-model",
            binding_sha256="f" * 64,
        )
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=False,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False,
    )
    @patch("quotations.gmail_inquiry_import.run_xlsx_preextract_shadow")
    @patch("quotations.gmail_inquiry_import.get_ai_parse_provider")
    @patch("quotations.gmail_inquiry_import.get_ai_parse_availability")
    @patch("quotations.gmail_inquiry_import.settings_ai_status")
    def test_fresh_and_baseline_cache_hit_each_run_xlsx_shadow(
        self,
        ai_status,
        availability,
        get_provider,
        run_shadow,
    ):
        gmail_import = GmailInquiryImport.objects.create(
            mailbox_email="mailbox@example.test",
            gmail_thread_id="private-thread",
            anchor_message_id="private-message-id",
            selected_message_ids=["private-message-id"],
            mode=GmailInquiryImport.MODE_AI_THREAD,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.actor,
        )
        message = copy.deepcopy(self.messages[0])
        message.update(
            {
                "subject": "Private subject",
                "sender": "Private Buyer <private@example.test>",
                "recipients": "mailbox@example.test",
                "newest_body_html": "",
            }
        )
        native_result = {
            "messages": [
                {
                    "gmail_message_id": "private-message-id",
                    "classification": "initial_inquiry",
                    "usage": "used",
                    "reason": "Initial request.",
                    "confidence": 1.0,
                }
            ],
            "rows": [],
            "customer_identity": {
                "company_name": "",
                "contact_name": "",
                "contact_email": "",
                "source_keys": [],
                "confidence": 0.0,
                "reason": "No reliable identity.",
            },
            "warnings": [],
            "thread_summary": "",
        }
        ai_status.return_value = {"status": "ai_available"}
        availability.return_value = {
            "provider": "mock-provider",
            "text_model": "mock-model",
            "vision_model": "mock-model",
        }
        get_provider.return_value.clean_rows.return_value = (
            native_result,
            {"input_tokens": 10},
        )
        run_shadow.return_value = {
            "version": GMAIL_XLSX_SHADOW_METRICS_VERSION,
            "status": "success",
        }

        first = _run_native_thread_analysis(
            [copy.deepcopy(message)],
            [copy.deepcopy(self.sources[0])],
            copy.deepcopy(self.file_inputs),
            gmail_import,
            self.actor,
        )
        cached = _run_native_thread_analysis(
            [copy.deepcopy(message)],
            [copy.deepcopy(self.sources[0])],
            copy.deepcopy(self.file_inputs),
            gmail_import,
            self.actor,
        )

        self.assertEqual(first["rows"], cached["rows"])
        self.assertEqual(get_provider.return_value.clean_rows.call_count, 1)
        self.assertEqual(run_shadow.call_count, 2)
        self.assertEqual(AIParseCache.objects.count(), 1)
        cache_payload = AIParseCache.objects.get().result
        self.assertNotIn("xlsx_preextract_shadow", json.dumps(cache_payload))
        self.assertEqual(AIParseLog.objects.count(), 2)
        self.assertEqual(AIParseLog.objects.filter(cache_hit=True).count(), 1)

    @override_settings(
        QUOTATION_GMAIL_XLSX_PREEXTRACT_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False,
    )
    def test_compact_failure_does_not_prevent_independent_xlsx_shadow(self):
        gmail_import = GmailInquiryImport.objects.create(
            mailbox_email="mailbox@example.test",
            gmail_thread_id="private-thread-independent",
            anchor_message_id="private-message-id",
            selected_message_ids=["private-message-id"],
            mode=GmailInquiryImport.MODE_AI_THREAD,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.actor,
        )
        provider = Mock()
        provider.clean_rows.return_value = ({"provider": "raw"}, {})
        with (
            patch(
                "quotations.gmail_inquiry_import.settings_ai_status",
                return_value={"status": "ai_available"},
            ),
            patch(
                "quotations.gmail_inquiry_import.get_ai_parse_availability",
                return_value={
                    "provider": "mock-provider",
                    "text_model": "mock-model",
                    "vision_model": "mock-model",
                },
            ),
            patch(
                "quotations.gmail_inquiry_import.get_ai_parse_provider",
                return_value=provider,
            ),
            patch(
                "quotations.gmail_inquiry_import._native_thread_context",
                return_value="PRIVATE COMPLETE THREAD",
            ),
            patch(
                "quotations.gmail_inquiry_import._native_thread_schema",
                return_value=copy.deepcopy(self.native_schema),
            ),
            patch(
                "quotations.gmail_inquiry_import._gmail_semantic_source_sha256",
                return_value="e" * 64,
            ),
            patch(
                "quotations.gmail_inquiry_import._validate_native_thread_result",
                return_value=copy.deepcopy(self.baseline),
            ),
            patch(
                "quotations.gmail_inquiry_import.run_compact_shadow",
                side_effect=RuntimeError("PRIVATE COMPACT FAILURE"),
            ) as compact_shadow,
            patch(
                "quotations.gmail_inquiry_import.run_xlsx_preextract_shadow",
                return_value={"status": "success"},
            ) as xlsx_shadow,
        ):
            result = _run_native_thread_analysis(
                copy.deepcopy(self.messages),
                copy.deepcopy(self.sources),
                copy.deepcopy(self.file_inputs),
                gmail_import,
                self.actor,
            )

        compact_shadow.assert_called_once()
        xlsx_shadow.assert_called_once()
        self.assertEqual(result["rows"], self.baseline["rows"])
        self.assertEqual(AIParseCache.objects.count(), 1)
