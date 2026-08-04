"""Durable, leased execution for Gmail inquiry analysis.

The job table is an orchestration ledger only. Gmail content and model output
remain in the existing import/cache/evidence stores, and workers call the
existing hardened analyzer after revalidating the employee-owned import.
"""

import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .gmail_analysis_progress import (
    ALL_STAGES,
    ERROR_UNEXPECTED_FAILURE,
    GENERATION_PATTERN,
    NON_RETRYABLE_ERROR_CATEGORIES,
    SAFE_ERROR_CATEGORIES,
    STAGE_COMPLETED,
    STAGE_FAILED,
    STAGE_QUEUED,
    GmailAnalysisProgressBinding,
    initialize_gmail_analysis_progress,
    progress_failure_category_for_stage,
)
from .gmail_workflow_metrics import (
    EVENT_ANALYSIS_REQUESTED,
    record_gmail_workflow_metric,
)
from .models import GmailInquiryAnalysisJob, GmailInquiryImport
from .workflow_features import gmail_background_analysis_enabled


DEFAULT_LEASE_SECONDS = 10 * 60
MIN_LEASE_SECONDS = 5 * 60
MAX_LEASE_SECONDS = 30 * 60
MAX_JOB_ATTEMPTS = 3


@dataclass(frozen=True)
class GmailAnalysisEnqueueResult:
    gmail_import: GmailInquiryImport
    job: GmailInquiryAnalysisJob | None
    queued: bool
    cache_hit: bool = False


def bounded_lease_seconds(value=None):
    try:
        value = int(value if value is not None else DEFAULT_LEASE_SECONDS)
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_LEASE_SECONDS
    return min(MAX_LEASE_SECONDS, max(MIN_LEASE_SECONDS, value))


def _safe_error_category(value):
    value = str(value or "")
    return value if value in SAFE_ERROR_CATEGORIES else ERROR_UNEXPECTED_FAILURE


def _iso_or_none(value):
    return value.isoformat() if value is not None else None


def gmail_analysis_job_projection(gmail_import):
    """Return the current allow-listed job state without lease credentials."""

    # This guard is also pre-migration compatibility: default-off old
    # deployments never touch the additive job table before migration 0041.
    if not gmail_background_analysis_enabled():
        return None
    source_fingerprint = str(gmail_import.source_fingerprint or "")
    generation = str(gmail_import.analysis_progress_generation or "")
    attempt = int(gmail_import.analysis_attempts or 0)
    jobs = GmailInquiryAnalysisJob.objects.filter(
        gmail_import_id=gmail_import.pk,
        analysis_attempt=attempt,
        source_generation=generation,
    ).filter(
        Q(source_fingerprint=source_fingerprint)
        | Q(result_source_fingerprint=source_fingerprint)
    )
    job = jobs.order_by("-created_at", "-pk").first()
    if job is None:
        return None
    if (
        job.status
        not in {
            value for value, _label in GmailInquiryAnalysisJob.STATUS_CHOICES
        }
        or not GENERATION_PATTERN.fullmatch(str(job.source_generation or ""))
    ):
        return None
    progress_stage = str(job.progress_stage or "")
    if progress_stage not in ALL_STAGES:
        progress_stage = ""
    error_category = (
        _safe_error_category(job.safe_error_category)
        if job.status == GmailInquiryAnalysisJob.STATUS_FAILED
        else ""
    )
    terminal = job.status in GmailInquiryAnalysisJob.TERMINAL_STATUSES
    return {
        "id": job.pk,
        "state": job.status,
        "analysis_attempt": job.analysis_attempt,
        "source_generation": job.source_generation,
        "progress_stage": progress_stage,
        "attempt_count": min(MAX_JOB_ATTEMPTS, int(job.attempt_count or 0)),
        "safe_error_category": error_category,
        "queued_at": _iso_or_none(job.queued_at),
        "started_at": _iso_or_none(job.started_at),
        "heartbeat_at": _iso_or_none(job.heartbeat_at),
        "completed_at": _iso_or_none(job.completed_at),
        "updated_at": _iso_or_none(job.updated_at),
        "terminal": terminal,
        "retryable": bool(
            job.status == GmailInquiryAnalysisJob.STATUS_FAILED
            and error_category not in NON_RETRYABLE_ERROR_CATEGORIES
        ),
    }


def _supersede_active_jobs_locked(gmail_import, *, at=None):
    """Supersede active jobs while holding the import lock (import -> job)."""

    at = at or timezone.now()
    jobs = list(
        GmailInquiryAnalysisJob.objects.select_for_update()
        .filter(
            gmail_import_id=gmail_import.pk,
            status__in=GmailInquiryAnalysisJob.ACTIVE_STATUSES,
        )
        .order_by("pk")
    )
    for job in jobs:
        job.status = GmailInquiryAnalysisJob.STATUS_SUPERSEDED
        job.progress_stage = ""
        job.safe_error_category = ""
        job.lease_owner = ""
        job.lease_token = ""
        job.lease_expires_at = None
        job.completed_at = at
        job.save(
            update_fields=[
                "status",
                "progress_stage",
                "safe_error_category",
                "lease_owner",
                "lease_token",
                "lease_expires_at",
                "completed_at",
                "updated_at",
            ]
        )
    return jobs


def supersede_active_gmail_analysis_jobs_locked(gmail_import):
    """Public integration point for source-selection mutation."""

    return _supersede_active_jobs_locked(gmail_import)


def _assert_claim_owner(gmail_import, actor):
    if not (
        actor
        and getattr(actor, "is_authenticated", False)
        and getattr(actor, "is_active", False)
        and getattr(actor, "is_staff", False)
        and gmail_import.claimed_by_id == actor.pk
    ):
        from .gmail_inquiry_import import GmailInquiryImportError

        raise GmailInquiryImportError(
            "This Gmail inquiry is not available to the current staff member."
        )


def _completed_job_for_current_import(locked, actor):
    fingerprint = str(locked.source_fingerprint or "")
    return (
        GmailInquiryAnalysisJob.objects.select_for_update()
        .filter(
            gmail_import_id=locked.pk,
            requested_by_id=actor.pk,
            status=GmailInquiryAnalysisJob.STATUS_COMPLETED,
            analysis_attempt=locked.analysis_attempts,
            source_generation=locked.analysis_progress_generation,
        )
        .filter(
            Q(source_fingerprint=fingerprint)
            | Q(result_source_fingerprint=fingerprint)
        )
        .order_by("-pk")
        .first()
    )


def _create_completed_legacy_job_locked(locked, actor):
    """Project immutable, staff-reviewed legacy results as a cache hit."""

    now = timezone.now()
    generation = secrets.token_hex(16)
    locked.analysis_progress_stage = STAGE_COMPLETED
    locked.analysis_progress_attempt = locked.analysis_attempts
    locked.analysis_progress_generation = generation
    locked.analysis_progress_error_category = ""
    locked.analysis_progress_updated_at = now
    locked.save(
        update_fields=[
            "analysis_progress_stage",
            "analysis_progress_attempt",
            "analysis_progress_generation",
            "analysis_progress_error_category",
            "analysis_progress_updated_at",
            "updated_at",
        ]
    )
    return GmailInquiryAnalysisJob.objects.create(
        gmail_import=locked,
        requested_by=actor,
        source_fingerprint=str(locked.source_fingerprint or ""),
        result_source_fingerprint=str(locked.source_fingerprint or ""),
        analysis_attempt=locked.analysis_attempts,
        source_generation=generation,
        status=GmailInquiryAnalysisJob.STATUS_COMPLETED,
        progress_stage=STAGE_COMPLETED,
        queued_at=now,
        started_at=locked.analysis_started_at or locked.analyzed_at or now,
        completed_at=locked.analyzed_at or now,
    )


def enqueue_gmail_inquiry_analysis(
    gmail_import,
    actor,
    *,
    selected_message_ids=None,
    mode=None,
    force=False,
    reanalyze=False,
):
    """Atomically reserve one generation or return its current durable job."""

    from .gmail_inquiry_import import (
        GMAIL_AI_PIPELINE_VERSION,
        GMAIL_AI_SCHEMA_NAME,
        GMAIL_SEMANTIC_CACHE_VERSION,
        GmailInquiryImportBusy,
        GmailInquiryImportError,
        GmailInquiryImportStale,
        update_gmail_inquiry_selection,
    )

    if not gmail_background_analysis_enabled():
        raise GmailInquiryImportError(
            "Durable Gmail analysis is not enabled."
        )
    force = bool(force or reanalyze)
    expected_binding = None
    if selected_message_ids is not None or mode is not None:
        selected_import = update_gmail_inquiry_selection(
            gmail_import,
            actor,
            selected_message_ids=selected_message_ids or [],
            mode=mode or GmailInquiryImport.MODE_SELECTED_MESSAGES,
        )
        expected_binding = (
            selected_import.pk,
            str(selected_import.source_fingerprint or ""),
            selected_import.mode,
            tuple(selected_import.selected_message_ids or []),
        )
        gmail_import = selected_import
        force = True

    queued = False
    cache_hit = False
    with transaction.atomic():
        locked = GmailInquiryImport.objects.select_for_update().get(
            pk=getattr(gmail_import, "pk", gmail_import)
        )
        _assert_claim_owner(locked, actor)
        if expected_binding is not None and expected_binding != (
            locked.pk,
            str(locked.source_fingerprint or ""),
            locked.mode,
            tuple(locked.selected_message_ids or []),
        ):
            raise GmailInquiryImportStale(
                "The Gmail message selection changed. Reload before analyzing."
            )
        if locked.status == GmailInquiryImport.STATUS_CONFIRMED:
            return GmailAnalysisEnqueueResult(locked, None, False, True)

        active = (
            GmailInquiryAnalysisJob.objects.select_for_update()
            .filter(
                gmail_import_id=locked.pk,
                status__in=GmailInquiryAnalysisJob.ACTIVE_STATUSES,
            )
            .order_by("pk")
            .first()
        )
        if active is not None:
            if (
                active.requested_by_id == actor.pk
                and active.source_fingerprint
                == str(locked.source_fingerprint or "")
                and active.analysis_attempt == locked.analysis_attempts
                and active.source_generation
                == locked.analysis_progress_generation
            ):
                return GmailAnalysisEnqueueResult(locked, active, False, False)
            _supersede_active_jobs_locked(locked)

        if not force:
            completed = _completed_job_for_current_import(locked, actor)
            if completed is not None:
                return GmailAnalysisEnqueueResult(locked, completed, False, True)
            if (
                locked.status
                in {
                    GmailInquiryImport.STATUS_READY,
                    GmailInquiryImport.STATUS_REVIEW_REQUIRED,
                }
                and bool((locked.analysis or {}).get("reviewed_at"))
            ):
                completed = _create_completed_legacy_job_locked(locked, actor)
                cache_hit = True
                return GmailAnalysisEnqueueResult(locked, completed, False, True)

        if (
            locked.status == GmailInquiryImport.STATUS_ANALYZING
            and locked.analysis_started_at
            and locked.analysis_started_at
            > timezone.now() - timedelta(minutes=10)
        ):
            # This can only be a synchronous request that began just before a
            # feature rollout; never race it with a durable worker.
            raise GmailInquiryImportBusy(
                "This Gmail inquiry is already being analyzed. Retry shortly."
            )

        now = timezone.now()
        locked.status = GmailInquiryImport.STATUS_ANALYZING
        locked.analysis_started_at = now
        locked.analysis_attempts += 1
        locked.errors = []
        progress_binding = initialize_gmail_analysis_progress(locked)
        if progress_binding is None:
            # Background analysis implies the progress projection, but retain
            # a fail-closed job-owned generation if settings are overridden in
            # an isolated service test.
            generation = secrets.token_hex(16)
            locked.analysis_progress_stage = STAGE_QUEUED
            locked.analysis_progress_attempt = locked.analysis_attempts
            locked.analysis_progress_generation = generation
            locked.analysis_progress_error_category = ""
            locked.analysis_progress_updated_at = now
        else:
            generation = progress_binding.generation
        if "identity_approval" in (locked.analysis or {}):
            from .gmail_review_state import clear_gmail_identity_approval

            locked.analysis = clear_gmail_identity_approval(locked.analysis)
        locked.save(
            update_fields=[
                "status",
                "analysis_started_at",
                "analysis_attempts",
                "errors",
                "analysis",
                "analysis_progress_stage",
                "analysis_progress_attempt",
                "analysis_progress_generation",
                "analysis_progress_error_category",
                "analysis_progress_updated_at",
                "updated_at",
            ]
        )
        try:
            job = GmailInquiryAnalysisJob.objects.create(
                gmail_import=locked,
                requested_by=actor,
                source_fingerprint=str(locked.source_fingerprint or ""),
                analysis_attempt=locked.analysis_attempts,
                source_generation=generation,
                force_requested=force,
                progress_stage=STAGE_QUEUED,
                queued_at=now,
            )
        except IntegrityError as exc:
            # The import lock serializes normal callers. A database-level
            # conflict is therefore a safe stale/concurrent response, not a
            # reason to create an unbound duplicate generation.
            raise GmailInquiryImportStale(
                "A Gmail analysis generation was queued concurrently. Reload it."
            ) from exc
        queued = True
        record_gmail_workflow_metric(
            locked,
            EVENT_ANALYSIS_REQUESTED,
            counts={
                "analysis_attempt_count": locked.analysis_attempts,
                "selected_message_count": len(
                    locked.selected_message_ids or []
                ),
            },
            cache_state="bypassed" if force else "unknown",
            outcome_code="success",
            feature_flags={
                "background_analysis": True,
            },
            contract_versions={
                "ai_pipeline": GMAIL_AI_PIPELINE_VERSION,
                "ai_schema": GMAIL_AI_SCHEMA_NAME,
                "semantic_cache": GMAIL_SEMANTIC_CACHE_VERSION,
            },
        )
    return GmailAnalysisEnqueueResult(locked, job, queued, cache_hit)


def _job_claim_queryset(now):
    return GmailInquiryAnalysisJob.objects.filter(
        Q(status=GmailInquiryAnalysisJob.STATUS_QUEUED)
        | Q(
            status=GmailInquiryAnalysisJob.STATUS_RUNNING,
            lease_expires_at__lte=now,
        )
        | Q(
            status=GmailInquiryAnalysisJob.STATUS_RUNNING,
            lease_expires_at__isnull=True,
        ),
        attempt_count__lt=MAX_JOB_ATTEMPTS,
    ).order_by("queued_at", "pk")


def claim_next_gmail_analysis_job(worker_id, *, lease_seconds=None):
    """Claim one job in a short job-only transaction, before import locking."""

    worker_id = str(worker_id or "")[:128]
    if not worker_id:
        raise ValueError("A worker identity is required.")
    lease_seconds = bounded_lease_seconds(lease_seconds)
    now = timezone.now()
    with transaction.atomic():
        queryset = _job_claim_queryset(now)
        if connection.features.has_select_for_update_skip_locked:
            queryset = queryset.select_for_update(skip_locked=True)
        else:
            queryset = queryset.select_for_update()
        job = queryset.first()
        if job is None:
            return None, ""
        lease_token = secrets.token_hex(32)
        job.status = GmailInquiryAnalysisJob.STATUS_RUNNING
        job.attempt_count += 1
        job.lease_owner = worker_id
        job.lease_token = lease_token
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.heartbeat_at = now
        job.started_at = job.started_at or now
        job.safe_error_category = ""
        job.save(
            update_fields=[
                "status",
                "attempt_count",
                "lease_owner",
                "lease_token",
                "lease_expires_at",
                "heartbeat_at",
                "started_at",
                "safe_error_category",
                "updated_at",
            ]
        )
        job_id = job.pk
    return GmailInquiryAnalysisJob.objects.get(pk=job_id), lease_token


def heartbeat_gmail_analysis_job(
    job_id,
    lease_token,
    *,
    stage=None,
    lease_seconds=None,
):
    """Renew only an unexpired lease; a stale worker cannot revive itself."""

    now = timezone.now()
    updates = {
        "lease_expires_at": now
        + timedelta(seconds=bounded_lease_seconds(lease_seconds)),
        "heartbeat_at": now,
    }
    if stage:
        updates["progress_stage"] = str(stage)[:40]
    return (
        GmailInquiryAnalysisJob.objects.filter(
            pk=job_id,
            status=GmailInquiryAnalysisJob.STATUS_RUNNING,
            lease_token=str(lease_token or ""),
            lease_expires_at__gt=now,
        ).update(**updates)
        == 1
    )


def _background_job_validation_locked_import(
    gmail_import,
    *,
    job_id,
    lease_token,
    expected_attempt,
    expected_generation,
    allow_canonical_source=False,
):
    """Return locked job plus lease/binding/authorization decisions."""

    try:
        job = GmailInquiryAnalysisJob.objects.select_for_update().get(
            pk=job_id,
            gmail_import_id=gmail_import.pk,
        )
    except GmailInquiryAnalysisJob.DoesNotExist:
        return None, False, False, False
    now = timezone.now()
    lease_matches = bool(
        job.status == GmailInquiryAnalysisJob.STATUS_RUNNING
        and secrets.compare_digest(
            str(job.lease_token or ""), str(lease_token or "")
        )
        and job.lease_expires_at
        and job.lease_expires_at > now
    )
    source_matches = job.source_fingerprint == str(
        gmail_import.source_fingerprint or ""
    )
    if allow_canonical_source:
        source_matches = source_matches or bool(
            job.result_source_fingerprint
            and job.result_source_fingerprint
            == str(gmail_import.source_fingerprint or "")
        )
    binding_matches = bool(
        lease_matches
        and job.analysis_attempt == int(expected_attempt)
        and job.source_generation == str(expected_generation or "")
        and gmail_import.analysis_attempts == int(expected_attempt)
        and gmail_import.analysis_progress_generation
        == str(expected_generation or "")
        and source_matches
    )
    actor = (
        get_user_model()
        .objects.select_for_update()
        .only("is_active", "is_staff")
        .filter(pk=job.requested_by_id)
        .first()
    )
    authorized = bool(
        actor
        and actor.is_active
        and actor.is_staff
        and job.requested_by_id == gmail_import.claimed_by_id
    )
    return job, lease_matches, binding_matches, authorized


def background_job_matches_locked_import(
    gmail_import,
    *,
    job_id,
    lease_token,
    expected_attempt,
    expected_generation,
    allow_canonical_source=False,
):
    """Final import->job->user guard used inside analyzer transactions."""

    _job, lease_matches, binding_matches, authorized = (
        _background_job_validation_locked_import(
            gmail_import,
            job_id=job_id,
            lease_token=lease_token,
            expected_attempt=expected_attempt,
            expected_generation=expected_generation,
            allow_canonical_source=allow_canonical_source,
        )
    )
    return bool(lease_matches and binding_matches and authorized)


def assert_current_gmail_analysis_job(job_id, lease_token):
    """Revalidate ownership/generation at a stage boundary."""

    with transaction.atomic():
        ref = GmailInquiryAnalysisJob.objects.only("gmail_import_id").get(
            pk=job_id
        )
        gmail_import = GmailInquiryImport.objects.select_for_update().get(
            pk=ref.gmail_import_id
        )
        job = GmailInquiryAnalysisJob.objects.get(pk=job_id)
        return background_job_matches_locked_import(
            gmail_import,
            job_id=job_id,
            lease_token=lease_token,
            expected_attempt=job.analysis_attempt,
            expected_generation=job.source_generation,
        )


def bind_background_job_result_source_locked_import(
    gmail_import,
    *,
    job_id,
    lease_token,
    expected_attempt,
    expected_generation,
    result_source_fingerprint,
):
    """Bind the canonical result source inside analyzer persistence."""

    if not background_job_matches_locked_import(
        gmail_import,
        job_id=job_id,
        lease_token=lease_token,
        expected_attempt=expected_attempt,
        expected_generation=expected_generation,
    ):
        return False
    result_source_fingerprint = str(result_source_fingerprint or "")
    if not (
        len(result_source_fingerprint) == 64
        and all(
            character in "0123456789abcdef"
            for character in result_source_fingerprint
        )
    ):
        return False
    job = GmailInquiryAnalysisJob.objects.get(pk=job_id)
    job.result_source_fingerprint = result_source_fingerprint
    job.save(update_fields=["result_source_fingerprint", "updated_at"])
    return True


def _mark_job_terminal_locked(job, *, status, at, error_category=""):
    job.status = status
    job.progress_stage = (
        STAGE_COMPLETED
        if status == GmailInquiryAnalysisJob.STATUS_COMPLETED
        else STAGE_FAILED
        if status == GmailInquiryAnalysisJob.STATUS_FAILED
        else ""
    )
    job.safe_error_category = (
        _safe_error_category(error_category)
        if status == GmailInquiryAnalysisJob.STATUS_FAILED
        else ""
    )
    job.lease_owner = ""
    job.lease_token = ""
    job.lease_expires_at = None
    job.completed_at = at
    job.save(
        update_fields=[
            "status",
            "progress_stage",
            "safe_error_category",
            "lease_owner",
            "lease_token",
            "lease_expires_at",
            "completed_at",
            "updated_at",
        ]
    )


def _fail_matching_import_locked(gmail_import, job, *, at, error_category):
    """Terminalize only the exact still-running import generation."""

    if not (
        gmail_import.status == GmailInquiryImport.STATUS_ANALYZING
        and gmail_import.analysis_attempts == job.analysis_attempt
        and gmail_import.analysis_progress_generation == job.source_generation
    ):
        return False
    gmail_import.status = GmailInquiryImport.STATUS_FAILED
    gmail_import.analysis_progress_stage = STAGE_FAILED
    gmail_import.analysis_progress_error_category = _safe_error_category(
        error_category
    )
    gmail_import.analysis_progress_updated_at = at
    gmail_import.analyzed_at = at
    gmail_import.save(
        update_fields=[
            "status",
            "analysis_progress_stage",
            "analysis_progress_error_category",
            "analysis_progress_updated_at",
            "analyzed_at",
            "updated_at",
        ]
    )
    return True


def _finish_job_from_terminal_import_locked(gmail_import, job, *, at):
    """Reconcile the crash gap after analyzer persistence, without rerunning it."""

    if gmail_import.analysis_progress_attempt != job.analysis_attempt:
        return False, False
    if (
        gmail_import.status
        in {
            GmailInquiryImport.STATUS_READY,
            GmailInquiryImport.STATUS_REVIEW_REQUIRED,
            GmailInquiryImport.STATUS_CONFIRMED,
        }
        and gmail_import.analysis_progress_stage == STAGE_COMPLETED
    ):
        job.result_source_fingerprint = str(
            gmail_import.source_fingerprint or ""
        )
        _mark_job_terminal_locked(
            job,
            status=GmailInquiryAnalysisJob.STATUS_COMPLETED,
            at=at,
        )
        job.save(
            update_fields=["result_source_fingerprint", "updated_at"]
        )
        return True, True
    if (
        gmail_import.status == GmailInquiryImport.STATUS_FAILED
        and gmail_import.analysis_progress_stage == STAGE_FAILED
    ):
        category = str(gmail_import.analysis_progress_error_category or "")
        if category not in SAFE_ERROR_CATEGORIES:
            category = progress_failure_category_for_stage(job.progress_stage)
        _mark_job_terminal_locked(
            job,
            status=GmailInquiryAnalysisJob.STATUS_FAILED,
            at=at,
            error_category=category,
        )
        return True, False
    return False, False


def _terminalize_claimed_job(job_id, lease_token, *, succeeded):
    now = timezone.now()
    with transaction.atomic():
        job_ref = GmailInquiryAnalysisJob.objects.only("gmail_import_id").get(
            pk=job_id
        )
        gmail_import = GmailInquiryImport.objects.select_for_update().get(
            pk=job_ref.gmail_import_id
        )
        job_snapshot = GmailInquiryAnalysisJob.objects.get(pk=job_id)
        job, lease_matches, binding_matches, authorized = (
            _background_job_validation_locked_import(
                gmail_import,
                job_id=job_id,
                lease_token=lease_token,
                expected_attempt=job_snapshot.analysis_attempt,
                expected_generation=job_snapshot.source_generation,
                allow_canonical_source=True,
            )
        )
        if not (job and lease_matches and binding_matches):
            return False
        handled, terminal_succeeded = _finish_job_from_terminal_import_locked(
            gmail_import,
            job,
            at=now,
        )
        if handled:
            return terminal_succeeded
        if not authorized:
            _fail_matching_import_locked(
                gmail_import,
                job,
                at=now,
                error_category=ERROR_UNEXPECTED_FAILURE,
            )
            _mark_job_terminal_locked(
                job,
                status=GmailInquiryAnalysisJob.STATUS_CANCELLED,
                at=now,
            )
            return False
        category = str(gmail_import.analysis_progress_error_category or "")
        if category not in SAFE_ERROR_CATEGORIES:
            category = progress_failure_category_for_stage(job.progress_stage)
        _fail_matching_import_locked(
            gmail_import,
            job,
            at=now,
            error_category=category,
        )
        _mark_job_terminal_locked(
            job,
            status=GmailInquiryAnalysisJob.STATUS_FAILED,
            at=now,
            error_category=category,
        )
        return True


def process_claimed_gmail_analysis_job(
    job,
    lease_token,
    *,
    lease_seconds=None,
):
    """Run one claimed job through the existing analyzer and safe cache path."""

    from .gmail_inquiry_import import analyze_gmail_inquiry_import

    job_id = getattr(job, "pk", job)
    with transaction.atomic():
        job_ref = GmailInquiryAnalysisJob.objects.only("gmail_import_id").get(
            pk=job_id
        )
        gmail_import = GmailInquiryImport.objects.select_for_update().get(
            pk=job_ref.gmail_import_id
        )
        locked_job = GmailInquiryAnalysisJob.objects.select_for_update().get(
            pk=job_id
        )
        now = timezone.now()
        (
            _validated_job,
            current_lease,
            current_binding,
            authorized,
        ) = _background_job_validation_locked_import(
            gmail_import,
            job_id=job_id,
            lease_token=lease_token,
            expected_attempt=locked_job.analysis_attempt,
            expected_generation=locked_job.source_generation,
            allow_canonical_source=True,
        )
        if not current_lease:
            # A stale caller may be holding the previous token after this job
            # was reclaimed. It must never mutate the replacement lease.
            return False
        if not current_binding:
            _mark_job_terminal_locked(
                locked_job,
                status=GmailInquiryAnalysisJob.STATUS_SUPERSEDED,
                at=now,
            )
            return False
        handled, terminal_succeeded = _finish_job_from_terminal_import_locked(
            gmail_import,
            _validated_job,
            at=now,
        )
        if handled:
            return terminal_succeeded
        actor = locked_job.requested_by
        if not authorized:
            _fail_matching_import_locked(
                gmail_import,
                locked_job,
                at=timezone.now(),
                error_category=ERROR_UNEXPECTED_FAILURE,
            )
            _mark_job_terminal_locked(
                locked_job,
                status=GmailInquiryAnalysisJob.STATUS_CANCELLED,
                at=timezone.now(),
            )
            return False
        attempt = locked_job.analysis_attempt
        generation = locked_job.source_generation
        force = locked_job.force_requested

    def heartbeat(stage=None):
        renewed = heartbeat_gmail_analysis_job(
            job_id,
            lease_token,
            stage=stage,
            lease_seconds=lease_seconds,
        )
        current = bool(
            renewed
            and assert_current_gmail_analysis_job(job_id, lease_token)
        )
        if not current:
            from .gmail_inquiry_import import GmailInquiryImportStale

            raise GmailInquiryImportStale(
                "The durable Gmail analysis lease is no longer current."
            )

    try:
        heartbeat(STAGE_QUEUED)
        analyze_gmail_inquiry_import(
            gmail_import,
            actor,
            force=force,
            _background_job_id=job_id,
            _background_lease_token=lease_token,
            _background_attempt=attempt,
            _background_generation=generation,
            _background_heartbeat=heartbeat,
        )
    except Exception:
        _terminalize_claimed_job(job_id, lease_token, succeeded=False)
        return False
    return _terminalize_claimed_job(job_id, lease_token, succeeded=True)


def fail_exhausted_gmail_analysis_jobs():
    """Fail expired jobs after bounded crash recovery, using import->job locks."""

    now = timezone.now()
    candidate_ids = list(
        GmailInquiryAnalysisJob.objects.filter(
            status=GmailInquiryAnalysisJob.STATUS_RUNNING,
            lease_expires_at__lte=now,
            attempt_count__gte=MAX_JOB_ATTEMPTS,
        )
        .order_by("pk")
        .values_list("pk", flat=True)[:100]
    )
    failed = 0
    for job_id in candidate_ids:
        with transaction.atomic():
            ref = GmailInquiryAnalysisJob.objects.only("gmail_import_id").get(
                pk=job_id
            )
            gmail_import = GmailInquiryImport.objects.select_for_update().get(
                pk=ref.gmail_import_id
            )
            job = GmailInquiryAnalysisJob.objects.select_for_update().get(
                pk=job_id
            )
            if not (
                job.status == GmailInquiryAnalysisJob.STATUS_RUNNING
                and job.lease_expires_at
                and job.lease_expires_at <= now
                and job.attempt_count >= MAX_JOB_ATTEMPTS
            ):
                continue
            _mark_job_terminal_locked(
                job,
                status=GmailInquiryAnalysisJob.STATUS_FAILED,
                at=now,
                error_category=ERROR_UNEXPECTED_FAILURE,
            )
            if (
                gmail_import.status == GmailInquiryImport.STATUS_ANALYZING
                and gmail_import.analysis_attempts == job.analysis_attempt
                and gmail_import.analysis_progress_generation
                == job.source_generation
            ):
                gmail_import.status = GmailInquiryImport.STATUS_FAILED
                gmail_import.analysis_progress_stage = STAGE_FAILED
                gmail_import.analysis_progress_error_category = (
                    ERROR_UNEXPECTED_FAILURE
                )
                gmail_import.analysis_progress_updated_at = now
                gmail_import.analyzed_at = now
                gmail_import.save(
                    update_fields=[
                        "status",
                        "analysis_progress_stage",
                        "analysis_progress_error_category",
                        "analysis_progress_updated_at",
                        "analyzed_at",
                        "updated_at",
                    ]
                )
            failed += 1
    return failed
