"""Privacy-safe, opt-in metrics for the Gmail quotation employee funnel.

The workflow row is linked to a ``GmailInquiryImport`` inside PostgreSQL, but
that foreign key is deliberately excluded from the exportable metric payload.
Only the bounded, allow-listed dimensions below can cross the telemetry
boundary.  This module must never accept arbitrary metadata or exception text.
"""

import logging
import math
import time

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction


logger = logging.getLogger(__name__)

GMAIL_WORKFLOW_METRICS_VERSION = "gmail_workflow_metrics_v1"
MAX_METRIC_DURATION_MS = 7 * 24 * 60 * 60 * 1000
MAX_METRIC_COUNT = 1_000_000

EVENT_HANDOFF_CREATED = "handoff_created"
EVENT_HANDOFF_CLAIMED = "handoff_claimed"
EVENT_ANALYSIS_REQUESTED = "analysis_requested"
EVENT_ANALYSIS_STARTED = "analysis_started"
EVENT_ANALYSIS_COMPLETED = "analysis_completed"
EVENT_ANALYSIS_FAILED = "analysis_failed"
EVENT_COMPANY_APPROVED = "company_approved"
EVENT_REVIEWED_ROWS_SAVED = "reviewed_rows_saved"
EVENT_QUOTATION_CREATED_OR_REUSED = "quotation_created_or_reused"
EVENT_PRICING_SAVED = "pricing_saved"
EVENT_EMAIL_PREVIEW_OPENED = "email_preview_opened"
EVENT_SEND_INITIATED = "send_initiated"
EVENT_SEND_CONFIRMED = "send_confirmed"
EVENT_SEND_FAILED = "send_failed"
EVENT_SEND_LEFT_UNKNOWN = "send_left_unknown"
EVENT_RECONCILIATION_COMPLETED = "reconciliation_completed"

GMAIL_WORKFLOW_EVENT_CHOICES = (
    (EVENT_HANDOFF_CREATED, "Add-on handoff created"),
    (EVENT_HANDOFF_CLAIMED, "Handoff claimed"),
    (EVENT_ANALYSIS_REQUESTED, "Analysis requested"),
    (EVENT_ANALYSIS_STARTED, "Analysis started"),
    (EVENT_ANALYSIS_COMPLETED, "Analysis completed"),
    (EVENT_ANALYSIS_FAILED, "Analysis failed"),
    (EVENT_COMPANY_APPROVED, "Company approved"),
    (EVENT_REVIEWED_ROWS_SAVED, "Reviewed rows saved"),
    (EVENT_QUOTATION_CREATED_OR_REUSED, "Quotation created or reused"),
    (EVENT_PRICING_SAVED, "Pricing saved"),
    (EVENT_EMAIL_PREVIEW_OPENED, "Email preview opened"),
    (EVENT_SEND_INITIATED, "Send initiated"),
    (EVENT_SEND_CONFIRMED, "Send confirmed"),
    (EVENT_SEND_FAILED, "Send failed"),
    (EVENT_SEND_LEFT_UNKNOWN, "Send left unknown"),
    (EVENT_RECONCILIATION_COMPLETED, "Reconciliation completed"),
)
GMAIL_WORKFLOW_EVENT_NAMES = frozenset(value for value, _label in GMAIL_WORKFLOW_EVENT_CHOICES)

SELECTION_MODE_CHOICES = (
    ("", "Not applicable"),
    ("current_message", "Current message"),
    ("selected_messages", "Selected messages"),
    ("ai_thread", "AI-assisted thread"),
)
SELECTION_MODES = frozenset(value for value, _label in SELECTION_MODE_CHOICES)

CACHE_STATE_CHOICES = (
    ("not_applicable", "Not applicable"),
    ("hit", "Hit"),
    ("miss", "Miss"),
    ("bypassed", "Bypassed"),
    ("unknown", "Unknown"),
)
CACHE_STATES = frozenset(value for value, _label in CACHE_STATE_CHOICES)

OUTCOME_CODE_CHOICES = (
    ("", "Not specified"),
    ("success", "Success"),
    ("failure", "Failure"),
    ("blocked", "Blocked"),
    ("created", "Created"),
    ("reused", "Reused"),
    ("ready", "Ready"),
    ("review_required", "Review required"),
    ("confirmed", "Confirmed"),
    ("unknown", "Unknown"),
    ("reconciled_sent", "Reconciled sent"),
    ("not_found", "Not found"),
)
OUTCOME_CODES = frozenset(value for value, _label in OUTCOME_CODE_CHOICES)

ALLOWED_COUNT_KEYS = frozenset(
    {
        "analysis_attempt_count",
        "attachment_count",
        "included_row_count",
        "message_count",
        "priced_row_count",
        "reviewed_row_count",
        "selected_message_count",
        "send_attempt_count",
        "uncertain_row_count",
        "updated_row_count",
    }
)
ALLOWED_CONTRACT_VERSION_KEYS = frozenset(
    {
        "ai_observability",
        "ai_pipeline",
        "ai_prompt",
        "ai_schema",
        "email_preview",
        "outbound_snapshot",
        "quotation_review",
        "semantic_cache",
        "workflow_metrics",
    }
)
ALLOWED_FEATURE_FLAG_KEYS = frozenset(
    {
        "background_analysis",
        "chained_actions",
        "compact_schema_shadow",
        "gmail_analysis_progress",
        "gmail_parallel_fetch",
        "gmail_review_ui_v2",
        "gmail_unified_workspace",
        "progressive_editor",
        "workflow_metrics",
        "xlsx_preextract_shadow",
    }
)
KNOWN_CONTRACT_VERSIONS = {
    "ai_observability": frozenset({"ai_parse_observability_v1"}),
    "ai_pipeline": frozenset({"gmail_inquiry_v2"}),
    "ai_schema": frozenset({"gmail_inquiry_native_v2"}),
    "email_preview": frozenset({"quotation_email_preview_v1"}),
    "outbound_snapshot": frozenset({"quotation_email_outbound_v1"}),
    "quotation_review": frozenset({"quotation_editor_review_v1"}),
    "semantic_cache": frozenset({"gmail_semantic_cache_v1"}),
    "workflow_metrics": frozenset({GMAIL_WORKFLOW_METRICS_VERSION}),
}


def default_metric_contract_versions():
    return {"workflow_metrics": GMAIL_WORKFLOW_METRICS_VERSION}


def default_metric_feature_flags():
    return {"workflow_metrics": True}


def _bounded_nonnegative_integer(value, maximum):
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return min(int(round(number)), maximum)


def sanitize_metric_counts(value):
    value = value if isinstance(value, dict) else {}
    result = {}
    for key in ALLOWED_COUNT_KEYS:
        if key not in value:
            continue
        number = _bounded_nonnegative_integer(value[key], MAX_METRIC_COUNT)
        if number is not None:
            result[key] = number
    return result


def sanitize_metric_contract_versions(value):
    value = value if isinstance(value, dict) else {}
    result = default_metric_contract_versions()
    for key in ALLOWED_CONTRACT_VERSION_KEYS:
        if key == "workflow_metrics" or key not in value:
            continue
        version = str(value[key] or "")
        if key == "ai_prompt":
            if len(version) == 64 and all(character in "0123456789abcdef" for character in version):
                result[key] = version
            continue
        if version in KNOWN_CONTRACT_VERSIONS.get(key, frozenset()):
            result[key] = version
    return result


def sanitize_metric_feature_flags(value=None):
    value = value if isinstance(value, dict) else {}
    result = default_metric_feature_flags()
    for key in ALLOWED_FEATURE_FLAG_KEYS:
        if key == "workflow_metrics" or key not in value:
            continue
        if isinstance(value[key], bool):
            result[key] = value[key]
    return result


def validate_metric_counts(value):
    if sanitize_metric_counts(value) != value:
        raise ValidationError("Workflow metric counts must use bounded allow-listed integers.")


def validate_metric_contract_versions(value):
    if sanitize_metric_contract_versions(value) != value:
        raise ValidationError("Workflow metric contract versions are invalid.")


def validate_metric_feature_flags(value):
    if sanitize_metric_feature_flags(value) != value:
        raise ValidationError("Workflow metric feature flags are invalid.")


def build_gmail_workflow_metric_fields(
    gmail_import,
    event_name,
    *,
    duration_ms=None,
    counts=None,
    selection_mode=None,
    cache_state="not_applicable",
    outcome_code="",
    contract_versions=None,
    feature_flags=None,
):
    """Build model fields without accepting any free-form telemetry value."""

    if event_name not in GMAIL_WORKFLOW_EVENT_NAMES:
        raise ValidationError("Unsupported Gmail workflow metric event.")
    mode = str(selection_mode if selection_mode is not None else getattr(gmail_import, "mode", "") or "")
    if mode not in SELECTION_MODES:
        mode = ""
    cache_state = str(cache_state or "not_applicable")
    if cache_state not in CACHE_STATES:
        cache_state = "unknown"
    outcome_code = str(outcome_code or "")
    if outcome_code not in OUTCOME_CODES:
        outcome_code = "failure"
    safe_duration = _bounded_nonnegative_integer(duration_ms, MAX_METRIC_DURATION_MS)
    return {
        "gmail_import": gmail_import,
        "event_name": event_name,
        "duration_ms": safe_duration,
        "counts": sanitize_metric_counts(counts),
        "selection_mode": mode,
        "cache_state": cache_state,
        "feature_flags": sanitize_metric_feature_flags(feature_flags),
        "outcome_code": outcome_code,
        "contract_versions": sanitize_metric_contract_versions(contract_versions),
    }


def record_gmail_workflow_metric(gmail_import, event_name, **dimensions):
    """Persist one metric without ever making the employee workflow fail."""

    if not bool(getattr(settings, "QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED", False)):
        return None
    if not getattr(gmail_import, "pk", None):
        return None
    try:
        fields = build_gmail_workflow_metric_fields(
            gmail_import,
            event_name,
            **dimensions,
        )
    except Exception:
        logger.warning("Gmail workflow metric was not accepted.")
        return None

    def persist():
        try:
            from .models import GmailWorkflowMetric

            # A savepoint prevents a telemetry database error from marking a
            # surrounding workflow transaction as broken.
            with transaction.atomic():
                return GmailWorkflowMetric.objects.create(**fields)
        except Exception:
            # Never log exception text here: future database/provider errors
            # can contain identifiers. The fixed message is content-free.
            logger.warning("Gmail workflow metric was not persisted.")
            return None

    connection = transaction.get_connection()
    if connection.in_atomic_block:
        # Keep optional telemetry outside Gmail/quotation lock critical
        # sections. A rollback naturally discards the scheduled event.
        transaction.on_commit(persist, robust=True)
        return None
    return persist()


def gmail_import_for_quotation(quotation):
    """Resolve the internal Gmail workflow without exposing its identifiers."""

    if not bool(getattr(settings, "QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED", False)):
        return None
    quotation_id = getattr(quotation, "pk", None)
    if not quotation_id:
        return None
    try:
        from .models import GmailInquiryImport

        return GmailInquiryImport.objects.filter(quotation_id=quotation_id).first()
    except Exception:
        logger.warning("Gmail workflow metric binding was not resolved.")
        return None


def record_quotation_gmail_workflow_metric(
    quotation,
    event_name,
    *,
    dimensions_factory=None,
    **dimensions,
):
    """Resolve and record optional quotation metrics without affecting work."""

    if not bool(getattr(settings, "QUOTATION_GMAIL_WORKFLOW_METRICS_ENABLED", False)):
        return None
    try:
        gmail_import = gmail_import_for_quotation(quotation)
        if not gmail_import:
            return None
        if dimensions_factory is not None:
            generated = dimensions_factory(gmail_import)
            if isinstance(generated, dict):
                dimensions.update(generated)
        return record_gmail_workflow_metric(
            gmail_import,
            event_name,
            **dimensions,
        )
    except Exception:
        logger.warning("Gmail quotation workflow metric was not recorded.")
        return None


def workflow_elapsed_ms(started_at):
    try:
        return max(0, round((time.perf_counter() - started_at) * 1000))
    except (TypeError, ValueError, OverflowError):
        return None


def send_error_metric_classification(delivery_status):
    """Separate provider ambiguity/failure from safe preflight lockouts."""

    status = str(delivery_status or "")
    if status == "unknown":
        return EVENT_SEND_LEFT_UNKNOWN, "unknown"
    if status == "failed":
        return EVENT_SEND_FAILED, "failure"
    return EVENT_SEND_FAILED, "blocked"


def export_gmail_workflow_metric(metric):
    """Return the only supported telemetry envelope; all record IDs stay private."""

    event_name = str(getattr(metric, "event_name", "") or "")
    if event_name not in GMAIL_WORKFLOW_EVENT_NAMES:
        event_name = EVENT_ANALYSIS_FAILED
    selection_mode = str(getattr(metric, "selection_mode", "") or "")
    if selection_mode not in SELECTION_MODES:
        selection_mode = ""
    cache_state = str(getattr(metric, "cache_state", "") or "")
    if cache_state not in CACHE_STATES:
        cache_state = "unknown"
    outcome_code = str(getattr(metric, "outcome_code", "") or "")
    if outcome_code not in OUTCOME_CODES:
        outcome_code = "failure"
    return {
        "event_name": event_name,
        "duration_ms": _bounded_nonnegative_integer(
            getattr(metric, "duration_ms", None),
            MAX_METRIC_DURATION_MS,
        ),
        "counts": sanitize_metric_counts(getattr(metric, "counts", None)),
        "selection_mode": selection_mode,
        "cache_state": cache_state,
        "feature_flags": sanitize_metric_feature_flags(
            getattr(metric, "feature_flags", None)
        ),
        "outcome_code": outcome_code,
        "contract_versions": sanitize_metric_contract_versions(
            getattr(metric, "contract_versions", None)
        ),
    }
