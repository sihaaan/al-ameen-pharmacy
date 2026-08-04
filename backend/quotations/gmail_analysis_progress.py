"""Privacy-safe, generation-bound progress for synchronous Gmail analysis."""

import re
import secrets
from dataclasses import dataclass

from django.utils import timezone

from .models import GmailInquiryImport
from .workflow_features import gmail_analysis_progress_enabled


GMAIL_ANALYSIS_PROGRESS_VERSION = "gmail_analysis_progress_v1"

STAGE_QUEUED = "queued"
STAGE_PREPARING = "preparing"
STAGE_FETCHING_MESSAGES = "fetching_messages"
STAGE_FETCHING_ATTACHMENTS = "fetching_attachments"
STAGE_INSPECTING_DOCUMENTS = "inspecting_documents"
STAGE_ANALYZING_WITH_AI = "analyzing_with_ai"
STAGE_VALIDATING_EVIDENCE = "validating_evidence"
STAGE_MATCHING_COMPANY_PRODUCTS = "matching_company_products"
STAGE_SAVING_RESULTS = "saving_results"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

RUNNING_STAGES = (
    STAGE_QUEUED,
    STAGE_PREPARING,
    STAGE_FETCHING_MESSAGES,
    STAGE_FETCHING_ATTACHMENTS,
    STAGE_INSPECTING_DOCUMENTS,
    STAGE_ANALYZING_WITH_AI,
    STAGE_VALIDATING_EVIDENCE,
    STAGE_MATCHING_COMPANY_PRODUCTS,
    STAGE_SAVING_RESULTS,
)
TERMINAL_STAGES = (STAGE_COMPLETED, STAGE_FAILED)
ALL_STAGES = frozenset((*RUNNING_STAGES, *TERMINAL_STAGES))
STAGE_ORDER = {stage: index for index, stage in enumerate(RUNNING_STAGES)}

ERROR_PREPARATION_FAILED = "preparation_failed"
ERROR_GMAIL_FETCH_FAILED = "gmail_fetch_failed"
ERROR_ATTACHMENT_FETCH_FAILED = "attachment_fetch_failed"
ERROR_DOCUMENT_INSPECTION_FAILED = "document_inspection_failed"
ERROR_AI_ANALYSIS_FAILED = "ai_analysis_failed"
ERROR_EVIDENCE_VALIDATION_FAILED = "evidence_validation_failed"
ERROR_MATCHING_FAILED = "matching_failed"
ERROR_RESULT_PERSISTENCE_FAILED = "result_persistence_failed"
ERROR_UNEXPECTED_FAILURE = "unexpected_failure"
SAFE_ERROR_CATEGORIES = frozenset(
    {
        ERROR_PREPARATION_FAILED,
        ERROR_GMAIL_FETCH_FAILED,
        ERROR_ATTACHMENT_FETCH_FAILED,
        ERROR_DOCUMENT_INSPECTION_FAILED,
        ERROR_AI_ANALYSIS_FAILED,
        ERROR_EVIDENCE_VALIDATION_FAILED,
        ERROR_MATCHING_FAILED,
        ERROR_RESULT_PERSISTENCE_FAILED,
        ERROR_UNEXPECTED_FAILURE,
    }
)
NON_RETRYABLE_ERROR_CATEGORIES = frozenset(
    {ERROR_DOCUMENT_INSPECTION_FAILED}
)
GENERATION_PATTERN = re.compile(r"^[0-9a-f]{32}$")


@dataclass(frozen=True)
class GmailAnalysisProgressBinding:
    import_id: int
    attempt: int
    source_fingerprint: str
    generation: str


def clear_gmail_analysis_progress(gmail_import):
    """Clear progress whenever the authoritative source selection changes."""

    gmail_import.analysis_progress_stage = ""
    gmail_import.analysis_progress_attempt = 0
    gmail_import.analysis_progress_generation = ""
    gmail_import.analysis_progress_error_category = ""
    gmail_import.analysis_progress_updated_at = None
    return gmail_import


def initialize_gmail_analysis_progress(gmail_import):
    """Initialize one opaque progress generation on an already-locked import."""

    if not gmail_analysis_progress_enabled():
        return None
    now = timezone.now()
    generation = secrets.token_hex(16)
    gmail_import.analysis_progress_stage = STAGE_QUEUED
    gmail_import.analysis_progress_attempt = gmail_import.analysis_attempts
    gmail_import.analysis_progress_generation = generation
    gmail_import.analysis_progress_error_category = ""
    gmail_import.analysis_progress_updated_at = now
    return GmailAnalysisProgressBinding(
        import_id=gmail_import.pk,
        attempt=gmail_import.analysis_attempts,
        source_fingerprint=str(gmail_import.source_fingerprint or ""),
        generation=generation,
    )


def advance_gmail_analysis_progress(binding, stage):
    """Advance monotonically if attempt, source, and generation still match."""

    # A live flag gates creation/exposure, not an already-started generation.
    # In-flight work must still reach a terminal state during instant rollback.
    if binding is None:
        return False
    if stage not in STAGE_ORDER:
        raise ValueError("Gmail analysis progress stage is invalid.")
    allowed_previous = [
        candidate
        for candidate in RUNNING_STAGES
        if STAGE_ORDER[candidate] <= STAGE_ORDER[stage]
    ]
    updated = GmailInquiryImport.objects.filter(
        pk=binding.import_id,
        status=GmailInquiryImport.STATUS_ANALYZING,
        analysis_attempts=binding.attempt,
        source_fingerprint=binding.source_fingerprint,
        analysis_progress_attempt=binding.attempt,
        analysis_progress_generation=binding.generation,
        analysis_progress_stage__in=allowed_previous,
    ).update(
        analysis_progress_stage=stage,
        analysis_progress_error_category="",
        analysis_progress_updated_at=timezone.now(),
    )
    return updated == 1


def progress_failure_category_for_stage(stage):
    """Map internal execution position to one content-free public category."""

    return {
        STAGE_QUEUED: ERROR_PREPARATION_FAILED,
        STAGE_PREPARING: ERROR_PREPARATION_FAILED,
        STAGE_FETCHING_MESSAGES: ERROR_GMAIL_FETCH_FAILED,
        STAGE_FETCHING_ATTACHMENTS: ERROR_ATTACHMENT_FETCH_FAILED,
        STAGE_INSPECTING_DOCUMENTS: ERROR_DOCUMENT_INSPECTION_FAILED,
        STAGE_ANALYZING_WITH_AI: ERROR_AI_ANALYSIS_FAILED,
        STAGE_VALIDATING_EVIDENCE: ERROR_EVIDENCE_VALIDATION_FAILED,
        STAGE_MATCHING_COMPANY_PRODUCTS: ERROR_MATCHING_FAILED,
        STAGE_SAVING_RESULTS: ERROR_RESULT_PERSISTENCE_FAILED,
    }.get(str(stage or ""), ERROR_UNEXPECTED_FAILURE)


def finish_gmail_analysis_progress(
    gmail_import,
    binding,
    *,
    succeeded,
    error_category="",
    at=None,
):
    """Mutate a locked import so terminal progress saves with final state."""

    # See ``advance_gmail_analysis_progress``: a binding that was issued while
    # enabled remains authoritative until terminal completion or failure.
    if binding is None:
        return False
    if not (
        gmail_import.pk == binding.import_id
        and gmail_import.status == GmailInquiryImport.STATUS_ANALYZING
        and gmail_import.analysis_attempts == binding.attempt
        and str(gmail_import.source_fingerprint or "")
        == binding.source_fingerprint
        and gmail_import.analysis_progress_attempt == binding.attempt
        and gmail_import.analysis_progress_generation == binding.generation
        and gmail_import.analysis_progress_stage in RUNNING_STAGES
    ):
        return False
    gmail_import.analysis_progress_stage = (
        STAGE_COMPLETED if succeeded else STAGE_FAILED
    )
    gmail_import.analysis_progress_error_category = (
        ""
        if succeeded
        else (
            error_category
            if error_category in SAFE_ERROR_CATEGORIES
            else ERROR_UNEXPECTED_FAILURE
        )
    )
    gmail_import.analysis_progress_updated_at = at or timezone.now()
    return True


def _iso_or_none(value):
    return value.isoformat() if value is not None else None


def gmail_analysis_progress_projection(gmail_import):
    """Return the exact allow-listed, content-free browser projection."""

    stage = str(gmail_import.analysis_progress_stage or "")
    generation = str(gmail_import.analysis_progress_generation or "")
    try:
        attempt = max(0, int(gmail_import.analysis_progress_attempt or 0))
    except (TypeError, ValueError, OverflowError):
        attempt = 0
    valid_binding = bool(
        stage in ALL_STAGES
        and GENERATION_PATTERN.fullmatch(generation)
        and attempt == gmail_import.analysis_attempts
    )
    if not valid_binding:
        stage = ""
        generation = ""
        attempt = 0
    if stage == STAGE_COMPLETED:
        state = "completed"
    elif stage == STAGE_FAILED:
        state = "failed"
    elif stage in RUNNING_STAGES:
        state = "running"
    else:
        state = "idle"
    safe_error_category = ""
    if state == "failed":
        stored_category = str(
            gmail_import.analysis_progress_error_category or ""
        )
        safe_error_category = (
            stored_category
            if stored_category in SAFE_ERROR_CATEGORIES
            else ERROR_UNEXPECTED_FAILURE
        )
    terminal = state in {"completed", "failed"}
    retryable = bool(
        state == "failed"
        and safe_error_category not in NON_RETRYABLE_ERROR_CATEGORIES
    )
    return {
        "version": GMAIL_ANALYSIS_PROGRESS_VERSION,
        "state": state,
        "stage": stage,
        "attempt": attempt,
        "source_generation": generation,
        "safe_error_category": safe_error_category,
        "started_at": (
            _iso_or_none(gmail_import.analysis_started_at)
            if state != "idle"
            else None
        ),
        "updated_at": (
            _iso_or_none(gmail_import.analysis_progress_updated_at)
            if state != "idle"
            else None
        ),
        "completed_at": (
            _iso_or_none(gmail_import.analyzed_at) if terminal else None
        ),
        "retryable": retryable,
    }
