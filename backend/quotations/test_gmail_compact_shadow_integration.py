import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.conf import settings
from django.test import TestCase, override_settings

from quotations.gmail_compact_shadow import (
    GMAIL_COMPACT_CACHE_NAMESPACE,
    GMAIL_COMPACT_METRICS_VERSION,
    GMAIL_COMPACT_PIPELINE_VERSION,
    GMAIL_COMPACT_PROMPT_VERSION,
    GMAIL_COMPACT_SCHEMA_NAME,
)
from quotations.gmail_inquiry_import import (
    _maybe_run_compact_schema_shadow,
    _persist_compact_shadow_metric,
    _run_native_thread_analysis,
)
from quotations.models import AIParseCache, AIParseLog, GmailInquiryImport


class GmailCompactShadowIntegrationTests(TestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="compact-shadow-employee",
            password="local-test-only",
            is_staff=True,
        )
        self.messages = [
            {
                "gmail_message_id": "private-message-id",
                "newest_body_text": "PRIVATE ITEM CONTENT",
                "is_outbound": False,
            }
        ]
        self.sources = [
            {
                "source_key": "private-source-key",
                "gmail_message_id": "private-message-id",
                "kind": "email_body",
                "rows": [{"raw_name": "PRIVATE ITEM CONTENT"}],
                "line_count": 1,
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
            "rows": [
                {
                    "raw_name": "PRIVATE ITEM CONTENT",
                    "quantity": "2",
                    "unit": "BOX",
                    "operation": "added",
                    "parse_status": "parsed",
                    "unit_price": None,
                    "evidence": [
                        {
                            "source_key": "private-source-key",
                            "raw_text": "PRIVATE ITEM CONTENT",
                        }
                    ],
                }
            ],
            "warnings": [],
            "customer_identity": {
                "company_name": "PRIVATE COMPANY",
                "contact_name": "PRIVATE CONTACT",
                "contact_email": "private@example.test",
                "source_keys": ["private-source-key"],
            },
        }

    def test_rollout_setting_defaults_to_disabled(self):
        self.assertIs(
            settings.QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED,
            False,
        )

    @override_settings(
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=False,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import.run_compact_shadow")
    def test_default_off_never_calls_shadow_provider_or_writes_metric(
        self,
        run_shadow,
    ):
        provider_runner = Mock()

        result = _maybe_run_compact_schema_shadow(
            messages=self.messages,
            sources=self.sources,
            file_inputs=[],
            gmail_import=SimpleNamespace(mode="ai_thread"),
            actor=self.actor,
            baseline_result=self.baseline,
            provider_name="mock-provider",
            model="mock-model",
            provider_runner=provider_runner,
        )

        self.assertIsNone(result)
        run_shadow.assert_not_called()
        provider_runner.assert_not_called()
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False,
    )
    @patch("quotations.gmail_inquiry_import.run_compact_shadow")
    def test_enabled_shadow_receives_copies_and_return_is_not_authoritative(
        self,
        run_shadow,
    ):
        original_messages = copy.deepcopy(self.messages)
        original_sources = copy.deepcopy(self.sources)
        original_baseline = copy.deepcopy(self.baseline)

        def mutate_copies(**kwargs):
            kwargs["messages"][0]["newest_body_text"] = "MUTATED"
            kwargs["sources"][0]["rows"] = []
            kwargs["baseline_result"]["rows"] = []
            return {"status": "success"}

        run_shadow.side_effect = mutate_copies
        provider_runner = Mock()

        result = _maybe_run_compact_schema_shadow(
            messages=self.messages,
            sources=self.sources,
            file_inputs=[],
            gmail_import=SimpleNamespace(mode="ai_thread"),
            actor=self.actor,
            baseline_result=self.baseline,
            provider_name="mock-provider",
            model="mock-model",
            provider_runner=provider_runner,
        )

        self.assertEqual(result, {"status": "success"})
        self.assertEqual(self.messages, original_messages)
        self.assertEqual(self.sources, original_sources)
        self.assertEqual(self.baseline, original_baseline)
        provider_runner.assert_not_called()
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import.run_compact_shadow")
    def test_shadow_exception_is_non_authoritative(self, run_shadow):
        run_shadow.side_effect = RuntimeError(
            "PRIVATE ITEM CONTENT private@example.test"
        )

        result = _maybe_run_compact_schema_shadow(
            messages=self.messages,
            sources=self.sources,
            file_inputs=[],
            gmail_import=SimpleNamespace(mode="ai_thread"),
            actor=self.actor,
            baseline_result=self.baseline,
            provider_name="mock-provider",
            model="mock-model",
            provider_runner=Mock(),
        )

        self.assertIsNone(result)
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metric_persistence_resanitizes_and_stores_no_content(self):
        report = {
            "version": GMAIL_COMPACT_METRICS_VERSION,
            "status": "success",
            "failure_category": "",
            "cache_state": "bypassed",
            "provider_call_attempted": True,
            "cache_key": "a" * 64,
            "contract": {
                "pipeline_version": GMAIL_COMPACT_PIPELINE_VERSION,
                "schema_name": GMAIL_COMPACT_SCHEMA_NAME,
                "prompt_version": GMAIL_COMPACT_PROMPT_VERSION,
                "cache_namespace": GMAIL_COMPACT_CACHE_NAMESPACE,
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
            "timings_ms": {"provider": 50, "private": "PRIVATE TIMING"},
            "raw_result": "PRIVATE RAW RESULT",
        }

        _persist_compact_shadow_metric(
            report,
            actor=self.actor,
            provider_name="mock-provider",
            model="mock-model",
            has_native_files=False,
            binding_sha256="f" * 64,
        )

        log = AIParseLog.objects.get()
        encoded = json.dumps(log.usage, sort_keys=True)
        self.assertTrue(log.success)
        self.assertEqual(log.error, "")
        self.assertEqual(log.source_type, "gmail_compact_shadow")
        self.assertEqual(log.source_sha256, "f" * 64)
        self.assertEqual(log.context_hash, "d" * 64)
        self.assertEqual(
            log.usage["shadow_experiment"]["comparison"]["row_recall_bp"],
            10_000,
        )
        for prohibited in (
            "PRIVATE ITEM",
            "PRIVATE PROVIDER",
            "PRIVATE TIMING",
            "PRIVATE RAW",
            "private@example.test",
        ):
            self.assertNotIn(prohibited, encoded)

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False)
    def test_metric_flag_off_writes_nothing(self):
        _persist_compact_shadow_metric(
            {"status": "success"},
            actor=self.actor,
            provider_name="mock-provider",
            model="mock-model",
            has_native_files=False,
            binding_sha256="f" * 64,
        )
        self.assertFalse(AIParseLog.objects.exists())

    @override_settings(
        QUOTATION_GMAIL_COMPACT_SCHEMA_SHADOW_ENABLED=True,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False,
    )
    @patch("quotations.gmail_inquiry_import.run_compact_shadow")
    @patch("quotations.gmail_inquiry_import.get_ai_parse_provider")
    @patch("quotations.gmail_inquiry_import.get_ai_parse_availability")
    @patch("quotations.gmail_inquiry_import.settings_ai_status")
    def test_fresh_and_baseline_cache_hit_each_shadow_without_touching_cache(
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
        source = copy.deepcopy(self.sources[0])
        source.update(
            {
                "filename": "",
                "mime_type": "text/plain",
                "source_sha256": "e" * 64,
                "rows": [],
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
            "rows": [
                {
                    "item_name": "PRIVATE ITEM CONTENT",
                    "quantity": "2",
                    "unit": "BOX",
                    "customer_unit_price": "",
                    "customer_line_total": "",
                    "customer_vat": "",
                    "operation": "added",
                    "citations": [
                        {
                            "source_key": "private-source-key",
                            "page_number": "",
                            "sheet_name": "",
                            "cell_range": "",
                            "raw_source_text": "PRIVATE ITEM CONTENT | 2 | BOX",
                        }
                    ],
                    "confidence": 1.0,
                    "parse_status": "parsed",
                    "reason": "Complete row.",
                }
            ],
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
            "version": GMAIL_COMPACT_METRICS_VERSION,
            "status": "success",
        }
        progress = Mock()

        first = _run_native_thread_analysis(
            [copy.deepcopy(message)],
            [copy.deepcopy(source)],
            [],
            gmail_import,
            self.actor,
            progress_callback=progress,
        )
        cached = _run_native_thread_analysis(
            [copy.deepcopy(message)],
            [copy.deepcopy(source)],
            [],
            gmail_import,
            self.actor,
            progress_callback=progress,
        )

        self.assertEqual(first["rows"], cached["rows"])
        self.assertIsNone(first["rows"][0]["unit_price"])
        self.assertEqual(get_provider.return_value.clean_rows.call_count, 1)
        self.assertEqual(run_shadow.call_count, 2)
        self.assertEqual(
            [call.args[0] for call in progress.call_args_list].count(
                "analyzing_with_ai"
            ),
            4,
        )
        self.assertEqual(AIParseCache.objects.count(), 1)
        cache_payload = AIParseCache.objects.get().result
        self.assertEqual(cache_payload["cache_version"], "gmail_semantic_cache_v1")
        self.assertNotIn("shadow", json.dumps(cache_payload).lower())
        self.assertEqual(AIParseLog.objects.count(), 2)
        self.assertEqual(AIParseLog.objects.filter(cache_hit=True).count(), 1)
