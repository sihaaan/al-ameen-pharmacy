"""Server-owned, default-off quotation workflow feature projections."""

from django.conf import settings


WORKFLOW_FEATURE_SETTINGS = {
    "gmail_review_ui_v2": "QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED",
    "gmail_chained_actions": "QUOTATION_GMAIL_CHAINED_ACTIONS_ENABLED",
    "quotation_editor_progressive_load": (
        "QUOTATION_EDITOR_PROGRESSIVE_LOAD_ENABLED"
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
    return features


def gmail_review_ui_v2_enabled():
    return quotation_workflow_features()["gmail_review_ui_v2"]


def gmail_chained_actions_enabled():
    return quotation_workflow_features()["gmail_chained_actions"]
