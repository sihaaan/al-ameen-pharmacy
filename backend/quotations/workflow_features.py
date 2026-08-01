"""Server-owned, default-off quotation workflow feature projections."""

from django.conf import settings


WORKFLOW_FEATURE_SETTINGS = {
    "gmail_review_ui_v2": "QUOTATION_GMAIL_REVIEW_UI_V2_ENABLED",
}


def quotation_workflow_features():
    """Return strict Booleans; a missing setting is always safely disabled."""

    return {
        name: getattr(settings, setting_name, False) is True
        for name, setting_name in WORKFLOW_FEATURE_SETTINGS.items()
    }


def gmail_review_ui_v2_enabled():
    return quotation_workflow_features()["gmail_review_ui_v2"]
