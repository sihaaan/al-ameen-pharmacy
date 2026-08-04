import json
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from api.models import Product

from .gmail_inquiry_import import (
    GmailInquiryImportError,
    analyze_gmail_inquiry_import,
    claim_gmail_inquiry_handoff,
    issue_gmail_inquiry_handoff,
    update_gmail_inquiry_review_lines,
)
from .gmail_workflow_metrics import (
    ALLOWED_CONTRACT_VERSION_KEYS,
    ALLOWED_COUNT_KEYS,
    ALLOWED_FEATURE_FLAG_KEYS,
    EVENT_HANDOFF_CLAIMED,
    EVENT_HANDOFF_CREATED,
    EVENT_ANALYSIS_COMPLETED,
    EVENT_ANALYSIS_FAILED,
    EVENT_ANALYSIS_REQUESTED,
    EVENT_ANALYSIS_STARTED,
    EVENT_COMPANY_APPROVED,
    EVENT_PRICING_SAVED,
    EVENT_REVIEWED_ROWS_SAVED,
    GMAIL_WORKFLOW_EVENT_NAMES,
    KNOWN_CONTRACT_VERSIONS,
    MAX_METRIC_COUNT,
    MAX_METRIC_DURATION_MS,
    build_gmail_workflow_metric_fields,
    export_gmail_workflow_metric,
    gmail_import_for_quotation,
    record_gmail_workflow_metric,
    send_error_metric_classification,
)
from .models import (
    Company,
    GmailInquiryImport,
    GmailOAuthConnection,
    GmailWorkflowMetric,
    Quotation,
    QuotationLine,
)


class GmailWorkflowMetricPrivacyTests(TransactionTestCase):
    def setUp(self):
        self.actor = get_user_model().objects.create_user(
            username="metric-staff",
            password="not-used",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.actor,
            is_shared=True,
            email="shared-mailbox@example.test",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email="shared-mailbox@example.test",
            gmail_thread_id="private-thread-id",
            anchor_message_id="private-message-id",
            source_fingerprint="a" * 64,
            mode=GmailInquiryImport.MODE_AI_THREAD,
        )

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False)
    def test_disabled_flag_writes_nothing(self):
        result = record_gmail_workflow_metric(
            self.gmail_import,
            EVENT_HANDOFF_CREATED,
            counts={"message_count": 1},
        )

        self.assertIsNone(result)
        self.assertFalse(GmailWorkflowMetric.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=False)
    def test_disabled_flag_does_not_query_for_a_quotation_workflow(self):
        with patch("quotations.models.GmailInquiryImport.objects.filter") as query:
            self.assertIsNone(gmail_import_for_quotation(object()))
        query.assert_not_called()

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_payload_is_allowlisted_bounded_and_contains_no_identifiers(self):
        customer_markers = (
            "PRIVATE CUSTOMER LTD",
            "buyer@example.test",
            "private-subject",
            "private-file.xlsx",
            "private-thread-id",
            "private-message-id",
            "Widget 100mg",
            "AED 42.00",
            "opaque-handoff-token",
            "oauth-token",
            "raw-model-output",
        )
        metric = record_gmail_workflow_metric(
            self.gmail_import,
            EVENT_HANDOFF_CREATED,
            duration_ms=MAX_METRIC_DURATION_MS * 2,
            counts={
                "message_count": MAX_METRIC_COUNT * 2,
                "filename": "private-file.xlsx",
                "subject": "private-subject",
                "price": "AED 42.00",
            },
            selection_mode=GmailInquiryImport.MODE_AI_THREAD,
            cache_state="not-a-real-cache-state",
            outcome_code="customer-specific-outcome",
            feature_flags={
                "workflow_metrics": False,
                "gmail_review_ui_v2": True,
                "customer_name": "PRIVATE CUSTOMER LTD",
            },
            contract_versions={
                "ai_pipeline": "gmail_inquiry_v2",
                "ai_prompt": "a" * 64,
                "ai_schema": "PRIVATE_CUSTOMER_LTD",
                "filename": "private-file.xlsx",
            },
        )

        payload = export_gmail_workflow_metric(metric)
        self.assertEqual(metric.gmail_import_id, self.gmail_import.pk)
        self.assertEqual(
            set(payload),
            {
                "event_name",
                "duration_ms",
                "counts",
                "selection_mode",
                "cache_state",
                "feature_flags",
                "outcome_code",
                "contract_versions",
            },
        )
        self.assertEqual(payload["duration_ms"], MAX_METRIC_DURATION_MS)
        self.assertEqual(payload["counts"], {"message_count": MAX_METRIC_COUNT})
        self.assertEqual(payload["cache_state"], "unknown")
        self.assertEqual(payload["outcome_code"], "failure")
        self.assertEqual(
            payload["feature_flags"],
            {"workflow_metrics": True, "gmail_review_ui_v2": True},
        )
        self.assertEqual(
            payload["contract_versions"],
            {
                "workflow_metrics": "gmail_workflow_metrics_v1",
                "ai_pipeline": "gmail_inquiry_v2",
                "ai_prompt": "a" * 64,
            },
        )
        encoded = json.dumps(payload, sort_keys=True)
        for marker in customer_markers:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, encoded)
        for identifier_key in (
            "id",
            "gmail_import",
            "gmail_import_id",
            "quotation_id",
            "delivery_id",
            "actor_id",
            "created_at",
        ):
            self.assertNotIn(identifier_key, payload)

    def test_only_documented_payload_dimensions_exist(self):
        contract_versions = {
            key: next(iter(values))
            for key, values in KNOWN_CONTRACT_VERSIONS.items()
        }
        contract_versions["ai_prompt"] = "b" * 64
        fields = build_gmail_workflow_metric_fields(
            self.gmail_import,
            EVENT_HANDOFF_CREATED,
            counts={key: 1 for key in ALLOWED_COUNT_KEYS},
            contract_versions=contract_versions,
            feature_flags={key: False for key in ALLOWED_FEATURE_FLAG_KEYS},
        )

        self.assertEqual(
            set(fields),
            {
                "gmail_import",
                "event_name",
                "duration_ms",
                "counts",
                "selection_mode",
                "cache_state",
                "feature_flags",
                "outcome_code",
                "contract_versions",
            },
        )
        self.assertEqual(set(fields["counts"]), ALLOWED_COUNT_KEYS)
        self.assertEqual(set(fields["contract_versions"]), ALLOWED_CONTRACT_VERSION_KEYS)
        self.assertEqual(set(fields["feature_flags"]), ALLOWED_FEATURE_FLAG_KEYS)

    def test_model_validation_rejects_non_allowlisted_json(self):
        metric = GmailWorkflowMetric(
            gmail_import=self.gmail_import,
            event_name=EVENT_HANDOFF_CREATED,
            counts={"subject": 1},
        )

        with self.assertRaises(ValidationError):
            metric.save()
        self.assertFalse(GmailWorkflowMetric.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metric_rows_reject_instance_and_bulk_mutation(self):
        metric = record_gmail_workflow_metric(
            self.gmail_import,
            EVENT_HANDOFF_CREATED,
        )

        metric.outcome_code = "success"
        with self.assertRaises(ValidationError):
            metric.save()
        with self.assertRaises(ValidationError):
            GmailWorkflowMetric.objects.filter(pk=metric.pk).update(
                outcome_code="success"
            )
        with self.assertRaises(ValidationError):
            GmailWorkflowMetric.objects.filter(pk=metric.pk).delete()

    def test_bulk_create_validates_and_export_resanitizes_corrupt_objects(self):
        with self.assertRaises(ValidationError):
            GmailWorkflowMetric.objects.bulk_create(
                [
                    GmailWorkflowMetric(
                        gmail_import=self.gmail_import,
                        event_name="private-subject",
                        counts={"subject": "buyer@example.test"},
                    )
                ]
            )

        corrupt = GmailWorkflowMetric(
            gmail_import=self.gmail_import,
            event_name="private-subject",
            duration_ms=MAX_METRIC_DURATION_MS * 2,
            counts={"subject": "buyer@example.test", "message_count": 2},
            selection_mode="private-thread-id",
            cache_state="private-file.xlsx",
            outcome_code="PRIVATE CUSTOMER LTD",
            feature_flags={"customer_name": True},
            contract_versions={"ai_schema": "Private item text"},
        )
        payload = export_gmail_workflow_metric(corrupt)

        self.assertEqual(payload["event_name"], EVENT_ANALYSIS_FAILED)
        self.assertEqual(payload["duration_ms"], MAX_METRIC_DURATION_MS)
        self.assertEqual(payload["counts"], {"message_count": 2})
        self.assertEqual(payload["selection_mode"], "")
        self.assertEqual(payload["cache_state"], "unknown")
        self.assertEqual(payload["outcome_code"], "failure")
        encoded = json.dumps(payload, sort_keys=True)
        for marker in (
            "private-subject",
            "buyer@example.test",
            "private-thread-id",
            "private-file.xlsx",
            "PRIVATE CUSTOMER LTD",
            "Private item text",
        ):
            self.assertNotIn(marker, encoded)

    def test_contract_values_are_key_specific_and_cannot_carry_customer_text(self):
        fields = build_gmail_workflow_metric_fields(
            self.gmail_import,
            EVENT_HANDOFF_CREATED,
            contract_versions={
                "ai_pipeline": "PRIVATE_CUSTOMER_LTD",
                "ai_schema": "private-file.xlsx",
                "ai_prompt": "buyer@example.test",
                "email_preview": "private-subject",
            },
        )

        self.assertEqual(
            fields["contract_versions"],
            {"workflow_metrics": "gmail_workflow_metrics_v1"},
        )

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metric_inside_transaction_is_written_only_after_commit(self):
        with transaction.atomic():
            result = record_gmail_workflow_metric(
                self.gmail_import,
                EVENT_HANDOFF_CREATED,
            )
            self.assertIsNone(result)
            self.assertFalse(GmailWorkflowMetric.objects.exists())

        self.assertTrue(GmailWorkflowMetric.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metric_inside_rolled_back_transaction_is_discarded(self):
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                record_gmail_workflow_metric(
                    self.gmail_import,
                    EVENT_HANDOFF_CREATED,
                )
                raise RuntimeError("rollback")

        self.assertFalse(GmailWorkflowMetric.objects.exists())

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_persistence_failure_is_non_blocking_and_log_is_content_free(self):
        with patch.object(
            GmailWorkflowMetric.objects,
            "create",
            side_effect=RuntimeError("buyer@example.test PRIVATE SUBJECT"),
        ), self.assertLogs(
            "quotations.gmail_workflow_metrics",
            level="WARNING",
        ) as captured:
            result = record_gmail_workflow_metric(
                self.gmail_import,
                EVENT_HANDOFF_CREATED,
            )

        self.assertIsNone(result)
        output = " ".join(captured.output)
        self.assertNotIn("buyer@example.test", output)
        self.assertNotIn("PRIVATE SUBJECT", output)

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_handoff_and_claim_emit_once_per_new_handoff(self):
        gmail_import, token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        claimed = claim_gmail_inquiry_handoff(token, self.actor)
        claim_gmail_inquiry_handoff(token, self.actor)

        events = list(
            claimed.workflow_metrics.values_list("event_name", flat=True)
        )
        self.assertEqual(
            events,
            [EVENT_HANDOFF_CREATED, EVENT_HANDOFF_CLAIMED],
        )

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_reissued_handoff_duration_uses_current_token_not_import_age(self):
        gmail_import, _first_token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        GmailInquiryImport.objects.filter(pk=gmail_import.pk).update(
            created_at=timezone.now() - timedelta(days=30)
        )
        _reused, current_token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )

        claimed = claim_gmail_inquiry_handoff(current_token, self.actor)
        claim_metric = claimed.workflow_metrics.get(event_name=EVENT_HANDOFF_CLAIMED)

        self.assertIsNotNone(claim_metric.duration_ms)
        self.assertLess(claim_metric.duration_ms, 10_000)

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_each_ledger_token_emits_at_most_one_claim_metric(self):
        gmail_import, token_a = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        claim_gmail_inquiry_handoff(token_a, self.actor)
        _reused, token_b = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )

        claim_gmail_inquiry_handoff(token_a, self.actor)
        claim_gmail_inquiry_handoff(token_b, self.actor)

        claims = gmail_import.workflow_metrics.filter(
            event_name=EVENT_HANDOFF_CLAIMED
        )
        self.assertEqual(claims.count(), 2)
        self.assertTrue(all(metric.duration_ms < 10_000 for metric in claims))

    def test_send_error_classification_reserves_unknown_for_ambiguous_result(self):
        self.assertEqual(
            send_error_metric_classification("unknown"),
            ("send_left_unknown", "unknown"),
        )
        self.assertEqual(
            send_error_metric_classification("failed"),
            ("send_failed", "failure"),
        )
        for status in ("", "prepared", "sending", "sent"):
            with self.subTest(status=status):
                self.assertEqual(
                    send_error_metric_classification(status),
                    ("send_failed", "blocked"),
                )

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    @patch("quotations.gmail_inquiry_import._build_source_analysis")
    @patch("quotations.gmail_inquiry_import._fetch_analysis_messages")
    @patch("quotations.gmail_inquiry_import._connected_mailbox_for_import")
    def test_successful_analysis_emits_bounded_lifecycle_metrics(
        self,
        mock_connection,
        mock_fetch,
        mock_build,
    ):
        gmail_import, token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="canonical-message",
            gmail_thread_id="canonical-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        gmail_import = claim_gmail_inquiry_handoff(token, self.actor)
        message = {"gmail_message_id": "canonical-message"}
        mock_connection.return_value = self.connection
        mock_fetch.return_value = ("canonical-thread", [message], [message])
        mock_build.return_value = {
            "message_manifest": [{"gmail_message_id": "canonical-message"}],
            "attachment_manifest": [],
            "preview": {
                "lines": [
                    {
                        "row_key": "a" * 32,
                        "raw_name": "Private item text",
                        "quantity": "2",
                        "unit": "PCS",
                        "operation": "uncertain",
                        "parse_status": "needs_review",
                        "included": True,
                    },
                    {
                        "row_key": "c" * 32,
                        "raw_name": "Excluded private item",
                        "quantity": "1",
                        "unit": "PCS",
                        "operation": "uncertain",
                        "parse_status": "needs_review",
                        "included": False,
                    }
                ]
            },
            "ready_for_direct_quote": False,
            "warnings": [],
            "recommended_source_keys": [],
            "thread_analysis": {"ai_usage": {}},
            "evidence": [],
            "candidates": {},
        }

        analyzed = analyze_gmail_inquiry_import(gmail_import, self.actor)

        lifecycle = list(
            analyzed.workflow_metrics.filter(
                event_name__in={
                    EVENT_ANALYSIS_REQUESTED,
                    EVENT_ANALYSIS_STARTED,
                    EVENT_ANALYSIS_COMPLETED,
                }
            ).order_by("created_at", "pk")
        )
        self.assertEqual(
            [metric.event_name for metric in lifecycle],
            [
                EVENT_ANALYSIS_REQUESTED,
                EVENT_ANALYSIS_STARTED,
                EVENT_ANALYSIS_COMPLETED,
            ],
        )
        completed = lifecycle[-1]
        self.assertEqual(completed.outcome_code, "review_required")
        self.assertEqual(completed.counts["message_count"], 1)
        self.assertEqual(completed.counts["included_row_count"], 1)
        self.assertEqual(completed.counts["uncertain_row_count"], 1)
        self.assertNotIn("Private item text", json.dumps(export_gmail_workflow_metric(completed)))

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    @patch("quotations.gmail_inquiry_import._fetch_analysis_messages")
    @patch("quotations.gmail_inquiry_import._connected_mailbox_for_import")
    def test_failed_analysis_emits_failed_without_exception_content(
        self,
        mock_connection,
        mock_fetch,
    ):
        gmail_import, token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="failure-message",
            gmail_thread_id="failure-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        gmail_import = claim_gmail_inquiry_handoff(token, self.actor)
        mock_connection.return_value = self.connection
        mock_fetch.side_effect = GmailInquiryImportError(
            "buyer@example.test private-file.xlsx"
        )

        with self.assertRaises(GmailInquiryImportError):
            analyze_gmail_inquiry_import(gmail_import, self.actor)

        failed = gmail_import.workflow_metrics.get(event_name=EVENT_ANALYSIS_FAILED)
        payload = json.dumps(export_gmail_workflow_metric(failed), sort_keys=True)
        self.assertEqual(failed.outcome_code, "failure")
        self.assertNotIn("buyer@example.test", payload)
        self.assertNotIn("private-file.xlsx", payload)

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_review_save_emits_only_row_counts(self):
        self.gmail_import.claimed_by = self.actor
        self.gmail_import.status = GmailInquiryImport.STATUS_REVIEW_REQUIRED
        self.gmail_import.analysis = {
            "preview": {
                "lines": [
                    {
                        "row_key": "b" * 32,
                        "raw_name": "Private reviewed item",
                        "raw_line": "Private reviewed item | 4 | PCS",
                        "quantity": "4",
                        "unit": "PCS",
                        "unit_price": None,
                        "vat_rate": "0.00",
                        "operation": "uncertain",
                        "parse_status": "needs_review",
                        "included": True,
                        "reviewed_by_user": False,
                        "_source_keys": ["body:private-source"],
                    }
                ]
            }
        }
        self.gmail_import.save(update_fields=["claimed_by", "status", "analysis", "updated_at"])

        updated = update_gmail_inquiry_review_lines(
            self.gmail_import,
            self.actor,
            review_lines=[
                {
                    "row_key": "b" * 32,
                    "raw_name": "Private reviewed item",
                    "quantity": 4,
                    "unit": "PCS",
                    "included": True,
                }
            ],
        )

        saved = updated.workflow_metrics.get(event_name=EVENT_REVIEWED_ROWS_SAVED)
        self.assertEqual(saved.counts, {"included_row_count": 1, "reviewed_row_count": 1})
        self.assertNotIn(
            "Private reviewed item",
            json.dumps(export_gmail_workflow_metric(saved), sort_keys=True),
        )

    def _set_confirmable_analysis(self, gmail_import, *, row_key):
        gmail_import.status = GmailInquiryImport.STATUS_REVIEW_REQUIRED
        gmail_import.analysis = {
            "preview": {
                "parse_method": "gmail_thread_deterministic_v2",
                "original_text": "Private inquiry body",
                "warnings": [],
                "meta": {},
                "lines": [
                    {
                        "row_key": row_key,
                        "raw_name": "Private item text",
                        "raw_line": "Private item text | 2 | PCS",
                        "quantity": "2",
                        "unit": "PCS",
                        "unit_price": None,
                        "vat_rate": "0.00",
                        "operation": "added",
                        "parse_status": "parsed",
                        "parse_confidence": 0.95,
                        "included": True,
                        "_source_keys": ["body:private-source"],
                    }
                ],
            }
        }
        gmail_import.message_manifest = [
            {
                "gmail_message_id": gmail_import.anchor_message_id,
                "subject": "Private subject",
                "sent_at": timezone.now().isoformat(),
            }
        ]
        gmail_import.save(
            update_fields=["status", "analysis", "message_manifest", "updated_at"]
        )
        return gmail_import

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    def test_confirm_route_attributes_created_and_reused_events_to_each_handoff(self):
        company = Company.objects.create(name="Private Customer")
        first_import, first_token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="confirm-message-a",
            gmail_thread_id="confirm-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        first_import = claim_gmail_inquiry_handoff(first_token, self.actor)
        self._set_confirmable_analysis(first_import, row_key="d" * 32)
        second_import, second_token = issue_gmail_inquiry_handoff(
            self.connection,
            anchor_message_id="confirm-message-b",
            gmail_thread_id="confirm-thread",
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
        )
        second_import = claim_gmail_inquiry_handoff(second_token, self.actor)
        self._set_confirmable_analysis(second_import, row_key="e" * 32)
        client = APIClient()
        client.force_authenticate(self.actor)

        first_response = client.post(
            reverse(
                "quotation-gmail-inquiry-import-confirm",
                args=[first_import.pk],
            ),
            {"company": company.pk},
            format="json",
        )
        second_response = client.post(
            reverse(
                "quotation-gmail-inquiry-import-confirm",
                args=[second_import.pk],
            ),
            {"company": company.pk},
            format="json",
        )

        self.assertEqual(first_response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(second_response.status_code, status.HTTP_200_OK)
        self.assertEqual(
            first_import.workflow_metrics.get(
                event_name="quotation_created_or_reused"
            ).outcome_code,
            "created",
        )
        self.assertEqual(
            second_import.workflow_metrics.get(
                event_name="quotation_created_or_reused"
            ).outcome_code,
            "reused",
        )
        self.assertEqual(
            second_import.workflow_metrics.filter(
                event_name=EVENT_COMPANY_APPROVED
            ).count(),
            1,
        )

    @override_settings(
        SECURE_SSL_REDIRECT=False,
        QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True,
    )
    def test_pricing_metric_requires_an_actual_unit_price_change(self):
        company = Company.objects.create(name="Pricing Customer")
        product = Product.objects.create(
            name="Pricing Product",
            price=Decimal("1.00"),
            status="draft",
        )
        quotation = Quotation.objects.create(
            company=company,
            created_by=self.actor,
        )
        line = QuotationLine.objects.create(
            quotation=quotation,
            product=product,
            item_name_snapshot=product.name,
            quantity=Decimal("1.000"),
            unit="PCS",
            unit_price=None,
            vat_rate=Decimal("0.00"),
            match_status=QuotationLine.MATCH_CONFIRMED,
        )
        self.gmail_import.quotation = quotation
        self.gmail_import.status = GmailInquiryImport.STATUS_CONFIRMED
        self.gmail_import.save(update_fields=["quotation", "status", "updated_at"])
        client = APIClient()
        client.force_authenticate(self.actor)
        url = reverse("quotation-bulk-update-lines", args=[quotation.pk])

        non_price = client.post(
            url,
            {"lines": [{"id": line.pk, "notes": "Internal note"}]},
            format="json",
        )
        self.assertEqual(non_price.status_code, status.HTTP_200_OK)
        self.assertFalse(
            self.gmail_import.workflow_metrics.filter(
                event_name=EVENT_PRICING_SAVED
            ).exists()
        )

        priced = client.post(
            url,
            {"lines": [{"id": line.pk, "unit_price": "12.000"}]},
            format="json",
        )
        self.assertEqual(priced.status_code, status.HTTP_200_OK)
        pricing_metric = self.gmail_import.workflow_metrics.get(
            event_name=EVENT_PRICING_SAVED
        )
        self.assertEqual(pricing_metric.counts["updated_row_count"], 1)

    def test_event_contract_covers_every_required_funnel_stage(self):
        self.assertEqual(
            GMAIL_WORKFLOW_EVENT_NAMES,
            {
                "handoff_created",
                "handoff_claimed",
                "analysis_requested",
                "analysis_started",
                "analysis_completed",
                "analysis_failed",
                "company_approved",
                "reviewed_rows_saved",
                "quotation_created_or_reused",
                "pricing_saved",
                "email_preview_opened",
                "send_initiated",
                "send_confirmed",
                "send_failed",
                "send_left_unknown",
                "reconciliation_completed",
            },
        )
