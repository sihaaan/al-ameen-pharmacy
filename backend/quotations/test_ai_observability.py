import json

from django.test import SimpleTestCase

from .ai_parsing import (
    AI_PARSE_OBSERVABILITY_VERSION,
    ai_parse_contract_descriptor,
    build_ai_parse_observation,
    sanitize_ai_provider_usage,
)


class AIParseObservabilityTests(SimpleTestCase):
    def test_provider_usage_is_numeric_allowlisted_and_cost_basis_is_normalized(self):
        usage = {
            "input_tokens": "120",
            "output_tokens": 40,
            "total_tokens": 160,
            "input_tokens_details": {
                "cached_tokens": 20,
                "customer_text": "PRIVATE CUSTOMER CONTENT",
            },
            "output_tokens_details": {
                "reasoning_tokens": 5,
                "provider_trace": "PRIVATE TRACE",
            },
            "customer_text": "PRIVATE CUSTOMER CONTENT",
        }

        sanitized = sanitize_ai_provider_usage(usage)
        observation = build_ai_parse_observation(
            route="manual",
            contract={"contract_sha256": "a" * 64},
            provider_usage=sanitized,
            timings_ms={"provider": 12.34, "total": 20, "private": "secret"},
            source_shape={"text_chars": 80, "input_rows": 2, "filename": "secret.xlsx"},
            provider_call_attempted=True,
        )

        self.assertEqual(
            sanitized,
            {
                "input_tokens": 120,
                "output_tokens": 40,
                "total_tokens": 160,
                "input_tokens_details": {"cached_tokens": 20},
                "output_tokens_details": {"reasoning_tokens": 5},
            },
        )
        self.assertEqual(observation["version"], AI_PARSE_OBSERVABILITY_VERSION)
        self.assertEqual(
            observation["cost_basis"],
            {
                "usage_reported": True,
                "input_tokens": 120,
                "cached_input_tokens": 20,
                "uncached_input_tokens": 100,
                "output_tokens": 40,
                "reasoning_output_tokens": 5,
                "total_tokens": 160,
            },
        )
        encoded = json.dumps(observation, sort_keys=True)
        self.assertNotIn("PRIVATE", encoded)
        self.assertNotIn("secret.xlsx", encoded)
        self.assertNotIn("private", observation["timings_ms"])
        self.assertNotIn("filename", observation["source_shape"])

    def test_contract_descriptor_fingerprints_without_storing_prompt_or_schema(self):
        first = ai_parse_contract_descriptor(
            pipeline_version="manual_ai_cleanup_v1",
            schema_name="quotation_import_parse",
            instructions="PRIVATE PROMPT ONE",
            schema={"type": "object", "properties": {"private": {"type": "string"}}},
        )
        prompt_changed = ai_parse_contract_descriptor(
            pipeline_version="manual_ai_cleanup_v1",
            schema_name="quotation_import_parse",
            instructions="PRIVATE PROMPT TWO",
            schema={"type": "object", "properties": {"private": {"type": "string"}}},
        )
        schema_changed = ai_parse_contract_descriptor(
            pipeline_version="manual_ai_cleanup_v1",
            schema_name="quotation_import_parse",
            instructions="PRIVATE PROMPT ONE",
            schema={"type": "object", "properties": {"changed": {"type": "number"}}},
        )

        self.assertNotEqual(first["contract_sha256"], prompt_changed["contract_sha256"])
        self.assertNotEqual(first["contract_sha256"], schema_changed["contract_sha256"])
        encoded = json.dumps(first, sort_keys=True)
        self.assertNotIn("PRIVATE PROMPT", encoded)
        self.assertNotIn("properties", encoded)
        for key in ("prompt_sha256", "schema_sha256", "contract_sha256"):
            self.assertEqual(len(first[key]), 64)

    def test_application_cache_hit_records_zero_provider_cost(self):
        observation = build_ai_parse_observation(
            route="manual",
            contract={"contract_sha256": "b" * 64},
            provider_usage={},
            timings_ms={"cache_lookup": 1, "total": 2},
            source_shape={"text_chars": 10, "output_rows": 1},
            provider_call_attempted=False,
            application_cache_hit=True,
            outcome="cache_hit",
        )

        self.assertTrue(observation["application_cache_hit"])
        self.assertFalse(observation["provider_call_attempted"])
        self.assertEqual(observation["outcome"], "cache_hit")
        self.assertFalse(observation["cost_basis"]["usage_reported"])
        self.assertEqual(observation["cost_basis"]["total_tokens"], 0)
