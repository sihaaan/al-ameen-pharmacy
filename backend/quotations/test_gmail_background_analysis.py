import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from .gmail_analysis_jobs import (
    MAX_JOB_ATTEMPTS,
    claim_next_gmail_analysis_job,
    enqueue_gmail_inquiry_analysis,
    fail_exhausted_gmail_analysis_jobs,
    gmail_analysis_job_projection,
    heartbeat_gmail_analysis_job,
    process_claimed_gmail_analysis_job,
)
from .gmail_analysis_progress import (
    ERROR_GMAIL_FETCH_FAILED,
    ERROR_UNEXPECTED_FAILURE,
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_FETCHING_MESSAGES,
    STAGE_INSPECTING_DOCUMENTS,
    STAGE_ANALYZING_WITH_AI,
)
from .gmail_inquiry_import import (
    GmailInquiryImportError,
    gmail_inquiry_selection_fingerprint,
    update_gmail_inquiry_selection,
)
from .gmail_workflow_metrics import (
    EVENT_ANALYSIS_COMPLETED,
    EVENT_ANALYSIS_REQUESTED,
    EVENT_ANALYSIS_STARTED,
)
from .models import (
    GmailInquiryAnalysisJob,
    GmailInquiryImport,
    GmailOAuthConnection,
    GmailWorkflowMetric,
)
from .serializers import GmailInquiryImportSerializer
from .workflow_features import quotation_workflow_features


JOB_PROJECTION_KEYS = {
    "id",
    "state",
    "analysis_attempt",
    "source_generation",
    "progress_stage",
    "attempt_count",
    "safe_error_category",
    "queued_at",
    "started_at",
    "heartbeat_at",
    "completed_at",
    "updated_at",
    "terminal",
    "retryable",
}


@override_settings(
    QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED=True,
    QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=False,
)
class GmailBackgroundAnalysisTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="gmail-background-owner",
            is_staff=True,
        )
        self.other_staff = User.objects.create_user(
            username="gmail-background-other",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email="background@example.com",
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id="background-thread",
            anchor_message_id="background-message",
            selected_message_ids=["background-message"],
            mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
            source_fingerprint="a" * 64,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.staff,
            claimed_at=timezone.now(),
        )

    def _enqueue(self, **kwargs):
        return enqueue_gmail_inquiry_analysis(
            self.gmail_import,
            self.staff,
            **kwargs,
        )

    def _pipeline(self, *, canonical_message_id=None):
        message_id = canonical_message_id or self.gmail_import.anchor_message_id
        message = {
            "gmail_message_id": message_id,
            "gmail_thread_id": self.gmail_import.gmail_thread_id,
        }
        fetched = (
            self.gmail_import.gmail_thread_id,
            [message],
            [message],
            {
                "canonical_anchor_message_id": message_id,
                "total_count": 1,
                "returned_count": 1,
                "limit": 50,
                "truncated": False,
            },
        )
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
        return fetched, result

    def _process_with_pipeline(self, job, lease_token, *, canonical=None):
        fetched, result = self._pipeline(canonical_message_id=canonical)
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
        ):
            return process_claimed_gmail_analysis_job(job, lease_token)

    def test_background_flag_implies_progress_and_analyze_returns_202_job(self):
        features = quotation_workflow_features()
        self.assertTrue(features["gmail_background_analysis"])
        self.assertTrue(features["gmail_analysis_progress"])
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analyze",
            args=[self.gmail_import.pk],
        )

        response = client.post(url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["status"], GmailInquiryImport.STATUS_ANALYZING)
        self.assertEqual(set(response.data["analysis_job"]), JOB_PROJECTION_KEYS)
        self.assertEqual(response.data["analysis_job"]["state"], "queued")
        self.assertRegex(
            response.data["analysis_job"]["source_generation"],
            r"^[0-9a-f]{32}$",
        )
        progress_url = reverse(
            "quotation-gmail-inquiry-import-analysis-progress",
            args=[self.gmail_import.pk],
        )
        progress = client.get(progress_url)
        self.assertEqual(progress.status_code, status.HTTP_200_OK)
        self.assertEqual(progress.data["stage"], "queued")

    @override_settings(QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED=True)
    def test_metrics_split_enqueue_from_worker_start_and_completion(self):
        with self.captureOnCommitCallbacks(execute=True):
            self._enqueue()

        requested = list(
            GmailWorkflowMetric.objects.filter(
                gmail_import=self.gmail_import
            ).order_by("pk")
        )
        self.assertEqual(
            [metric.event_name for metric in requested],
            [EVENT_ANALYSIS_REQUESTED],
        )
        self.assertEqual(
            requested[0].feature_flags.get("background_analysis"),
            True,
        )
        job, token = claim_next_gmail_analysis_job("metrics-worker")
        with self.captureOnCommitCallbacks(execute=True):
            self.assertTrue(self._process_with_pipeline(job, token))

        events = list(
            GmailWorkflowMetric.objects.filter(
                gmail_import=self.gmail_import
            )
            .order_by("pk")
            .values_list("event_name", flat=True)
        )
        self.assertEqual(
            events,
            [
                EVENT_ANALYSIS_REQUESTED,
                EVENT_ANALYSIS_STARTED,
                EVENT_ANALYSIS_COMPLETED,
            ],
        )

    def test_repeated_post_returns_same_active_job_and_attempt(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analyze",
            args=[self.gmail_import.pk],
        )

        first = client.post(url, {}, format="json")
        second = client.post(url, {}, format="json")

        self.assertEqual(first.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(second.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(
            first.data["analysis_job"]["id"],
            second.data["analysis_job"]["id"],
        )
        self.assertEqual(GmailInquiryAnalysisJob.objects.count(), 1)
        self.gmail_import.refresh_from_db()
        self.assertEqual(self.gmail_import.analysis_attempts, 1)

    def test_import_retrieve_resumes_same_safe_job_projection(self):
        result = self._enqueue()
        _claimed, lease_token = claim_next_gmail_analysis_job(
            "resume-worker"
        )
        client = APIClient()
        client.force_authenticate(self.staff)

        response = client.get(
            reverse(
                "quotation-gmail-inquiry-import-detail",
                args=[self.gmail_import.pk],
            )
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["analysis_job"]["id"], result.job.pk)
        self.assertNotIn(lease_token, json.dumps(response.data))
        other = APIClient()
        other.force_authenticate(self.other_staff)
        self.assertEqual(
            other.get(
                reverse(
                    "quotation-gmail-inquiry-import-detail",
                    args=[self.gmail_import.pk],
                )
            ).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    def test_claim_heartbeat_and_expired_lease_recovery_are_bounded(self):
        result = self._enqueue()
        job, first_token = claim_next_gmail_analysis_job(
            "worker-one",
            lease_seconds=60,
        )
        self.assertEqual(job.pk, result.job.pk)
        self.assertEqual(job.attempt_count, 1)
        self.assertTrue(
            heartbeat_gmail_analysis_job(
                job.pk,
                first_token,
                stage=STAGE_FETCHING_MESSAGES,
                lease_seconds=60,
            )
        )
        GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )

        reclaimed, second_token = claim_next_gmail_analysis_job(
            "worker-two",
            lease_seconds=60,
        )

        self.assertEqual(reclaimed.pk, job.pk)
        self.assertNotEqual(first_token, second_token)
        self.assertEqual(reclaimed.attempt_count, 2)
        self.assertFalse(
            heartbeat_gmail_analysis_job(job.pk, first_token, lease_seconds=60)
        )

    def test_old_reclaimed_token_cannot_supersede_replacement_worker(self):
        self._enqueue()
        job, old_token = claim_next_gmail_analysis_job("worker-old")
        GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        _reclaimed, current_token = claim_next_gmail_analysis_job("worker-new")

        self.assertFalse(process_claimed_gmail_analysis_job(job, old_token))

        job.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, current_token)
        self.gmail_import.refresh_from_db()
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_ANALYZING)

    def test_message_fetch_heartbeat_stops_immediately_after_lease_loss(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("message-heartbeat-worker")

        def lose_lease(_gmail_import, _connection, **kwargs):
            GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                lease_owner="replacement-worker",
                lease_token="replacement-token",
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
            kwargs["coordinator_heartbeat"](STAGE_FETCHING_MESSAGES)
            raise AssertionError("lease-loss heartbeat must stop the fetch")

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                side_effect=lose_lease,
            ),
            patch(
                "quotations.gmail_inquiry_import._build_source_analysis"
            ) as build,
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        build.assert_not_called()
        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, "replacement-token")
        self.assertEqual(self.gmail_import.analysis, {})

    def test_attachment_reduction_heartbeat_stops_after_lease_loss(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job(
            "attachment-heartbeat-worker"
        )
        fetched, _result = self._pipeline()

        def lose_lease_during_attachments(*_args, **kwargs):
            GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                lease_owner="replacement-worker",
                lease_token="replacement-token",
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
            kwargs["coordinator_heartbeat"](STAGE_INSPECTING_DOCUMENTS)
            raise AssertionError("lease-loss heartbeat must stop reduction")

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
                side_effect=lose_lease_during_attachments,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, "replacement-token")
        self.assertEqual(self.gmail_import.analysis, {})

    def test_reclaimed_job_reconciles_completed_import_without_rerunning_ai(self):
        for index, terminal_status in enumerate(
            (
                GmailInquiryImport.STATUS_READY,
                GmailInquiryImport.STATUS_REVIEW_REQUIRED,
                GmailInquiryImport.STATUS_CONFIRMED,
            ),
            start=1,
        ):
            with self.subTest(terminal_status=terminal_status):
                gmail_import = GmailInquiryImport.objects.create(
                    gmail_connection=self.connection,
                    mailbox_email=self.connection.email,
                    gmail_thread_id=f"crash-success-thread-{index}",
                    anchor_message_id=f"crash-success-message-{index}",
                    selected_message_ids=[f"crash-success-message-{index}"],
                    mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
                    source_fingerprint=f"{index:064x}",
                    status=GmailInquiryImport.STATUS_CLAIMED,
                    claimed_by=self.staff,
                    claimed_at=timezone.now(),
                )
                enqueue_gmail_inquiry_analysis(gmail_import, self.staff)
                job, token = claim_next_gmail_analysis_job(
                    f"crash-success-worker-{index}"
                )
                canonical_fingerprint = f"{index + 10:064x}"
                GmailInquiryImport.objects.filter(pk=gmail_import.pk).update(
                    status=terminal_status,
                    source_fingerprint=canonical_fingerprint,
                    analysis_progress_stage=STAGE_COMPLETED,
                )
                GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                    result_source_fingerprint=canonical_fingerprint,
                    lease_expires_at=timezone.now() - timedelta(seconds=1),
                )
                gmail_import.refresh_from_db()
                self.assertEqual(
                    gmail_import.analysis_progress_attempt,
                    job.analysis_attempt,
                )
                reclaimed, reclaimed_token = claim_next_gmail_analysis_job(
                    f"crash-success-reclaimer-{index}"
                )

                with patch(
                    "quotations.gmail_inquiry_import.analyze_gmail_inquiry_import"
                ) as analyze:
                    self.assertTrue(
                        process_claimed_gmail_analysis_job(
                            reclaimed,
                            reclaimed_token,
                        )
                    )

                analyze.assert_not_called()
                job.refresh_from_db()
                self.assertEqual(
                    job.status,
                    GmailInquiryAnalysisJob.STATUS_COMPLETED,
                )
                self.assertEqual(
                    job.result_source_fingerprint,
                    canonical_fingerprint,
                )

    def test_reclaimed_job_reconciles_failed_import_without_rerunning_ai(self):
        self._enqueue()
        job, _token = claim_next_gmail_analysis_job("crash-failure-worker")
        GmailInquiryImport.objects.filter(pk=self.gmail_import.pk).update(
            status=GmailInquiryImport.STATUS_FAILED,
            analysis_progress_stage=STAGE_FAILED,
            analysis_progress_error_category=ERROR_GMAIL_FETCH_FAILED,
        )
        GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
            lease_expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.gmail_import.refresh_from_db()
        self.assertEqual(
            self.gmail_import.analysis_progress_attempt,
            job.analysis_attempt,
        )
        reclaimed, reclaimed_token = claim_next_gmail_analysis_job(
            "crash-failure-reclaimer"
        )

        with patch(
            "quotations.gmail_inquiry_import.analyze_gmail_inquiry_import"
        ) as analyze:
            self.assertFalse(
                process_claimed_gmail_analysis_job(
                    reclaimed,
                    reclaimed_token,
                )
            )

        analyze.assert_not_called()
        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_FAILED)
        self.assertEqual(job.safe_error_category, ERROR_GMAIL_FETCH_FAILED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)

    def test_worker_completes_with_canonical_source_fingerprint(self):
        self.gmail_import.anchor_message_id = "msg-f:add-on-alias"
        self.gmail_import.selected_message_ids = [self.gmail_import.anchor_message_id]
        self.gmail_import.save(
            update_fields=["anchor_message_id", "selected_message_ids", "updated_at"]
        )
        result = self._enqueue()
        job, token = claim_next_gmail_analysis_job("canonical-worker")

        self.assertTrue(
            self._process_with_pipeline(
                job,
                token,
                canonical="canonical-gmail-message",
            )
        )

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_COMPLETED)
        self.assertEqual(
            job.result_source_fingerprint,
            self.gmail_import.source_fingerprint,
        )
        self.assertNotEqual(job.source_fingerprint, job.result_source_fingerprint)
        self.assertEqual(
            self.gmail_import.status,
            GmailInquiryImport.STATUS_REVIEW_REQUIRED,
        )
        projection = gmail_analysis_job_projection(self.gmail_import)
        self.assertEqual(projection["id"], result.job.pk)
        self.assertEqual(projection["state"], "completed")

    def test_nonforce_completed_analysis_returns_200_without_new_job(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("complete-worker")
        self.assertTrue(self._process_with_pipeline(job, token))
        client = APIClient()
        client.force_authenticate(self.staff)
        url = reverse(
            "quotation-gmail-inquiry-import-analyze",
            args=[self.gmail_import.pk],
        )

        response = client.post(url, {}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["analysis_job"]["id"], job.pk)
        self.assertEqual(response.data["analysis_job"]["state"], "completed")
        self.assertEqual(GmailInquiryAnalysisJob.objects.count(), 1)

    def test_fast_worker_completion_is_serialized_as_coherent_200_snapshot(self):
        real_enqueue = enqueue_gmail_inquiry_analysis

        def enqueue_then_finish(*args, **kwargs):
            queued = real_enqueue(*args, **kwargs)
            job, token = claim_next_gmail_analysis_job("fast-worker")
            self.assertTrue(self._process_with_pipeline(job, token))
            return queued

        client = APIClient()
        client.force_authenticate(self.staff)
        with patch(
            "quotations.views.enqueue_gmail_inquiry_analysis",
            side_effect=enqueue_then_finish,
        ):
            response = client.post(
                reverse(
                    "quotation-gmail-inquiry-import-analyze",
                    args=[self.gmail_import.pk],
                ),
                {},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "review_required")
        self.assertEqual(response.data["analysis_job"]["state"], "completed")
        self.assertEqual(response.data["analysis_progress"]["state"], "completed")

    def test_force_after_completed_analysis_creates_new_generation(self):
        self._enqueue()
        first, token = claim_next_gmail_analysis_job("complete-worker")
        self.assertTrue(self._process_with_pipeline(first, token))
        old_generation = first.source_generation
        client = APIClient()
        client.force_authenticate(self.staff)

        response = client.post(
            reverse(
                "quotation-gmail-inquiry-import-analyze",
                args=[self.gmail_import.pk],
            ),
            {"force": True},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertNotEqual(
            response.data["analysis_job"]["source_generation"],
            old_generation,
        )
        self.assertEqual(GmailInquiryAnalysisJob.objects.count(), 2)

    def test_failure_persists_only_safe_category(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("failure-worker")
        private_error = GmailInquiryImportError(
            "buyer@example.com Private RFQ.xlsx background-message"
        )
        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                side_effect=private_error,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_FAILED)
        self.assertEqual(job.safe_error_category, ERROR_GMAIL_FETCH_FAILED)
        self.assertNotIn("buyer@example.com", json.dumps(gmail_analysis_job_projection(self.gmail_import)))
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)

    def test_setup_exception_still_terminalizes_matching_import_and_job(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("setup-failure-worker")
        with patch(
            "quotations.gmail_inquiry_import.analyze_gmail_inquiry_import",
            side_effect=RuntimeError("private setup detail"),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_FAILED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertNotIn(
            "private setup detail",
            json.dumps(gmail_analysis_job_projection(self.gmail_import)),
        )

    def test_stolen_lease_before_success_persistence_cannot_mutate_import(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("old-worker")
        fetched, result = self._pipeline()

        def steal_lease(*_args, **_kwargs):
            GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                lease_owner="replacement-worker",
                lease_token="replacement-token",
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
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
                side_effect=steal_lease,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, "replacement-token")
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_ANALYZING)
        self.assertEqual(self.gmail_import.analysis, {})

    def test_stolen_lease_before_failure_cannot_fail_import(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("old-worker")

        def steal_and_fail(*_args, **_kwargs):
            GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                lease_owner="replacement-worker",
                lease_token="replacement-token",
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
            raise GmailInquiryImportError("private source failure")

        with (
            patch(
                "quotations.gmail_inquiry_import._connected_mailbox_for_import",
                return_value=self.connection,
            ),
            patch(
                "quotations.gmail_inquiry_import._fetch_analysis_messages",
                side_effect=steal_and_fail,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, "replacement-token")
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_ANALYZING)
        self.assertEqual(self.gmail_import.errors, [])

    def test_first_analyzer_heartbeat_rejection_does_not_mask_or_mutate(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("old-worker")
        calls = 0

        def heartbeat(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                return True
            GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
                lease_owner="replacement-worker",
                lease_token="replacement-token",
                lease_expires_at=timezone.now() + timedelta(minutes=5),
            )
            return False

        with patch(
            "quotations.gmail_analysis_jobs.heartbeat_gmail_analysis_job",
            side_effect=heartbeat,
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertGreaterEqual(calls, 2)
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_RUNNING)
        self.assertEqual(job.lease_token, "replacement-token")
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_ANALYZING)
        self.assertEqual(self.gmail_import.errors, [])

    def test_deactivated_owner_cancels_job_and_terminalizes_import(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("owner-worker")
        self.staff.is_active = False
        self.staff.save(update_fields=["is_active"])

        self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_CANCELLED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertEqual(self.gmail_import.analysis_progress_stage, "failed")
        self.assertEqual(
            self.gmail_import.analysis_progress_error_category,
            ERROR_UNEXPECTED_FAILURE,
        )

    def test_deactivation_at_provider_boundary_prevents_provider_and_results(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("provider-auth-worker")
        fetched, _result = self._pipeline()
        provider_reached = False

        def build(*_args, **kwargs):
            nonlocal provider_reached
            self.staff.is_active = False
            self.staff.save(update_fields=["is_active"])
            kwargs["progress_callback"](STAGE_ANALYZING_WITH_AI)
            provider_reached = True
            raise AssertionError("provider should not run")

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
                side_effect=build,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertFalse(provider_reached)
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_CANCELLED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertEqual(self.gmail_import.analysis, {})

    def test_deactivation_before_persistence_prevents_result_write(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("persist-auth-worker")
        fetched, result = self._pipeline()

        def build(*_args, **_kwargs):
            self.staff.is_active = False
            self.staff.save(update_fields=["is_active"])
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
                side_effect=build,
            ),
        ):
            self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_CANCELLED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)
        self.assertEqual(self.gmail_import.analysis, {})

    def test_claim_owner_change_cancels_job_and_terminalizes_import(self):
        self._enqueue()
        job, token = claim_next_gmail_analysis_job("owner-worker")
        GmailInquiryImport.objects.filter(pk=self.gmail_import.pk).update(
            claimed_by=self.other_staff
        )

        self.assertFalse(process_claimed_gmail_analysis_job(job, token))

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_CANCELLED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)

    def test_selection_change_supersedes_job_and_clears_generation(self):
        result = self._enqueue()

        updated = update_gmail_inquiry_selection(
            self.gmail_import,
            self.staff,
            selected_message_ids=[],
            mode=GmailInquiryImport.MODE_AI_THREAD,
        )

        result.job.refresh_from_db()
        self.assertEqual(
            result.job.status,
            GmailInquiryAnalysisJob.STATUS_SUPERSEDED,
        )
        self.assertEqual(updated.status, GmailInquiryImport.STATUS_CLAIMED)
        self.assertEqual(updated.analysis_progress_generation, "")

    def test_deduplicated_selection_does_not_strand_superseded_original(self):
        result = self._enqueue()
        target_fingerprint = gmail_inquiry_selection_fingerprint(
            mailbox_email=self.connection.email,
            gmail_thread_id=self.gmail_import.gmail_thread_id,
            anchor_message_id=self.gmail_import.anchor_message_id,
            mode=GmailInquiryImport.MODE_AI_THREAD,
            selected_message_ids=[],
        )
        existing = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=self.connection.email,
            gmail_thread_id=self.gmail_import.gmail_thread_id,
            anchor_message_id="other-anchor",
            selected_message_ids=[],
            mode=GmailInquiryImport.MODE_AI_THREAD,
            source_fingerprint=target_fingerprint,
            status=GmailInquiryImport.STATUS_CLAIMED,
            claimed_by=self.staff,
            claimed_at=timezone.now(),
        )

        selected = update_gmail_inquiry_selection(
            self.gmail_import,
            self.staff,
            selected_message_ids=[],
            mode=GmailInquiryImport.MODE_AI_THREAD,
        )

        self.assertEqual(selected.pk, existing.pk)
        result.job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(result.job.status, GmailInquiryAnalysisJob.STATUS_SUPERSEDED)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_CLAIMED)
        self.assertEqual(self.gmail_import.analysis_progress_generation, "")

    def test_projection_fails_closed_on_poisoned_stage_and_generation(self):
        result = self._enqueue()
        GmailInquiryAnalysisJob.objects.filter(pk=result.job.pk).update(
            progress_stage="buyer@example.com Private RFQ.xlsx",
        )
        self.gmail_import.refresh_from_db()

        payload = gmail_analysis_job_projection(self.gmail_import)

        self.assertEqual(payload["progress_stage"], "")
        self.assertNotIn("buyer@example.com", json.dumps(payload))
        GmailInquiryAnalysisJob.objects.filter(pk=result.job.pk).update(
            source_generation="buyer@example.com",
        )
        GmailInquiryImport.objects.filter(pk=self.gmail_import.pk).update(
            analysis_progress_generation="buyer@example.com",
        )
        self.gmail_import.refresh_from_db()
        self.assertIsNone(gmail_analysis_job_projection(self.gmail_import))

    def test_exhausted_crash_recovery_fails_current_generation_safely(self):
        self._enqueue()
        job, _token = claim_next_gmail_analysis_job("crash-worker")
        GmailInquiryAnalysisJob.objects.filter(pk=job.pk).update(
            attempt_count=MAX_JOB_ATTEMPTS,
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )

        self.assertEqual(fail_exhausted_gmail_analysis_jobs(), 1)

        job.refresh_from_db()
        self.gmail_import.refresh_from_db()
        self.assertEqual(job.status, GmailInquiryAnalysisJob.STATUS_FAILED)
        self.assertEqual(job.safe_error_category, ERROR_UNEXPECTED_FAILURE)
        self.assertEqual(self.gmail_import.status, GmailInquiryImport.STATUS_FAILED)

    @override_settings(
        QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED=False,
        QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED=False,
    )
    def test_flag_off_preserves_synchronous_200_and_no_job_projection(self):
        client = APIClient()
        client.force_authenticate(self.staff)
        with patch(
            "quotations.views.analyze_gmail_inquiry_import",
            return_value=self.gmail_import,
        ) as analyze:
            response = client.post(
                reverse(
                    "quotation-gmail-inquiry-import-analyze",
                    args=[self.gmail_import.pk],
                ),
                {},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["analysis_job"])
        analyze.assert_called_once()
        self.assertEqual(GmailInquiryAnalysisJob.objects.count(), 0)

    @override_settings(QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED=False)
    def test_worker_command_refuses_to_run_when_flag_is_off(self):
        with self.assertRaises(CommandError):
            call_command("run_gmail_inquiry_worker", once=True)

    def test_worker_command_once_closes_cleanly_without_jobs(self):
        with patch(
            "quotations.management.commands.run_gmail_inquiry_worker.claim_next_gmail_analysis_job",
            return_value=(None, ""),
        ):
            call_command("run_gmail_inquiry_worker", once=True)

    def test_serializer_never_projects_lease_credentials(self):
        result = self._enqueue()
        job, token = claim_next_gmail_analysis_job("private-worker")
        self.gmail_import.refresh_from_db()

        payload = GmailInquiryImportSerializer(self.gmail_import).data
        serialized = json.dumps(payload["analysis_job"], sort_keys=True)

        self.assertEqual(payload["analysis_job"]["id"], result.job.pk)
        self.assertNotIn(token, serialized)
        self.assertNotIn("private-worker", serialized)
        self.assertNotIn(self.gmail_import.source_fingerprint, serialized)
