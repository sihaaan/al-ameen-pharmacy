"""Server-owned quotation workflow feature projections."""

from django.conf import settings


WORKFLOW_FEATURE_SETTINGS = {
    "gmail_review_ui_v2": "QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED",
    "gmail_chained_actions": "QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED",
    "quotation_editor_progressive_load": (
        "QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED"
    ),
    "gmail_analysis_progress": "QUOTATION_GMAIL_ANALYSIS_PROGRESS_ENABLED",
    "gmail_unified_workspace": "QUOTATION_GMAIL_UNIFIED_WORKSPACE_ENABLED",
    "gmail_standard_editor_intake": (
        "QUOTATION_GMAIL_STANDARD_EDITOR_INTAKE_ENABLED"
    ),
    "gmail_background_analysis": (
        "QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED"
    ),
}


def quotation_workflow_features():
    """Return strict Booleans; a missing setting is always safely disabled."""

    features = {
        name: getattr(settings, setting_name, False) is True
        for name, setting_name in WORKFLOW_FEATURE_SETTINGS.items()
    }
    # Chained create/preview ergonomics build on the persisted, server-owned
    # identity acknowledgement from review UI V2. A partial rollout must fail
    # closed instead of falling back to browser-only legacy confirmation.
    features["gmail_chained_actions"] = bool(
        features["gmail_chained_actions"]
        and features["gmail_review_ui_v2"]
    )
    # Unified preparation depends on the persisted, evidence-bound company
    # approval introduced by review UI V2. Never expose a partially enabled
    # endpoint that could fall back to browser-only identity state.
    features["gmail_unified_workspace"] = bool(
        features["gmail_unified_workspace"]
        and features["gmail_review_ui_v2"]
    )
    # The standard editor intake retains the same persisted company-review
    # safety boundary. Frontends give this route precedence over the optional
    # unified workspace while both projections remain available for a
    # configuration-only rollback.
    features["gmail_standard_editor_intake"] = bool(
        features["gmail_standard_editor_intake"]
        and features["gmail_review_ui_v2"]
    )
    # Durable jobs always expose the same content-free progress contract so a
    # polling browser never has to reload full email evidence. This implication
    # is one-way: enabling standalone synchronous progress does not enqueue.
    features["gmail_analysis_progress"] = bool(
        features["gmail_analysis_progress"]
        or features["gmail_background_analysis"]
    )
    return features


def gmail_review_ui_v2_enabled():
    return quotation_workflow_features()["gmail_review_ui_v2"]


def gmail_chained_actions_enabled():
    return quotation_workflow_features()["gmail_chained_actions"]


def gmail_analysis_progress_enabled():
    return quotation_workflow_features()["gmail_analysis_progress"]


def gmail_unified_workspace_enabled():
    return quotation_workflow_features()["gmail_unified_workspace"]


def gmail_standard_editor_intake_enabled():
    return quotation_workflow_features()["gmail_standard_editor_intake"]


def gmail_background_analysis_enabled():
    return quotation_workflow_features()["gmail_background_analysis"]
