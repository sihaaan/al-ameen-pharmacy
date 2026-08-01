"""Content-bound staff approval state for Gmail customer identity review."""

import json

from django.utils import timezone
from django.utils.crypto import salted_hmac


GMAIL_IDENTITY_APPROVAL_VERSION = "gmail_identity_approval_v1"
GMAIL_IDENTITY_MATCH_VERSION = "gmail_identity_v4"
GMAIL_IDENTITY_REVIEW_FINGERPRINT_SALT = (
    "quotations.gmail_identity_review_fingerprint.v1"
)
GMAIL_REVIEW_ROWS_FINGERPRINT_CONTRACT = "gmail_review_rows_v1"
GMAIL_REVIEW_ROWS_FINGERPRINT_SALT = "quotations.gmail_review_rows_fingerprint.v1"
TRUSTED_PHYSICAL_IDENTITY_SIGNALS = frozenset(
    {
        "exact_contact_email",
        "exact_company_email",
        "verified_email_domain",
        "company_name_domain_inference",
        "exact_company_name_signature",
    }
)


def _identity_projection(gmail_import):
    candidates = dict(gmail_import.candidates or {})
    analysis = dict(gmail_import.analysis or {})
    thread_analysis = dict(analysis.get("thread_analysis") or {})
    messages = []
    for message in gmail_import.message_manifest or []:
        if not isinstance(message, dict):
            continue
        messages.append(
            {
                "gmail_message_id": message.get("gmail_message_id"),
                "sender": message.get("sender"),
                "reply_to": message.get("reply_to"),
                "is_outbound": message.get("is_outbound"),
                "classification": message.get("classification"),
                "usage": message.get("usage"),
                "contains_unverified_forwarded_content": message.get(
                    "contains_unverified_forwarded_content"
                ),
            }
        )
    return {
        "gmail_import_id": gmail_import.pk,
        "source_fingerprint": gmail_import.source_fingerprint,
        "analysis_attempts": gmail_import.analysis_attempts,
        "analysis_version": analysis.get("version"),
        "content_fingerprint": analysis.get("content_fingerprint"),
        "selected_company_id": gmail_import.selected_company_id,
        "selected_contact_id": gmail_import.selected_contact_id,
        "identity_match_version": candidates.get("identity_match_version"),
        "identity_reanalysis_required": candidates.get(
            "identity_reanalysis_required"
        ),
        "identity_conflict": candidates.get("identity_conflict"),
        "recommended_company_id": candidates.get("recommended_company_id"),
        "recommended_contact_id": candidates.get("recommended_contact_id"),
        "exact_company_match": candidates.get("exact_company_match"),
        "sender_emails": candidates.get("sender_emails"),
        "verified_identity_sender_emails": candidates.get(
            "verified_identity_sender_emails"
        ),
        "identity_warnings": candidates.get("identity_warnings"),
        "unverified_forwarded_identity_source_keys": candidates.get(
            "unverified_forwarded_identity_source_keys"
        ),
        "ai_identity_unverified_forwarded": candidates.get(
            "ai_identity_unverified_forwarded"
        ),
        "ai_identity": candidates.get("ai_identity"),
        "companies": candidates.get("companies"),
        "contacts": candidates.get("contacts"),
        "thread_customer_identity": thread_analysis.get("customer_identity"),
        "messages": messages,
    }


def gmail_identity_evidence_fingerprint(gmail_import):
    """Hash identity evidence while deliberately excluding item-row state."""

    encoded = json.dumps(
        _identity_projection(gmail_import),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return salted_hmac(
        GMAIL_IDENTITY_REVIEW_FINGERPRINT_SALT,
        encoded,
        algorithm="sha256",
    ).hexdigest()


def gmail_review_rows_fingerprint(gmail_import):
    """Key the complete server-owned row state used by confirmation.

    The digest deliberately covers the full ordered preview-line dictionaries,
    rather than a client-selected subset, so additions, exclusions, review
    decisions, matches, evidence keys, quantities, units, or any future
    confirm-relevant line field invalidate an older chained request. Source and
    analysis-attempt bindings remain explicit fields in the API contract.
    """

    preview = dict((gmail_import.analysis or {}).get("preview") or {})
    lines = preview.get("lines") or []
    if not isinstance(lines, list):
        lines = []
    encoded = json.dumps(
        {
            "contract": GMAIL_REVIEW_ROWS_FINGERPRINT_CONTRACT,
            "gmail_import_id": gmail_import.pk,
            "lines": lines,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return salted_hmac(
        GMAIL_REVIEW_ROWS_FINGERPRINT_SALT,
        encoded,
        algorithm="sha256",
    ).hexdigest()


def clear_gmail_identity_approval(analysis):
    analysis = dict(analysis or {})
    analysis.pop("identity_approval", None)
    return analysis


def build_gmail_identity_approval(
    gmail_import,
    actor,
    *,
    suggested=False,
    request_fingerprint="",
):
    candidates = dict(gmail_import.candidates or {})
    analysis = dict(gmail_import.analysis or {})
    return {
        "version": GMAIL_IDENTITY_APPROVAL_VERSION,
        "company_id": gmail_import.selected_company_id,
        "contact_id": gmail_import.selected_contact_id,
        "approved_by_user_id": actor.pk,
        "approved_at": timezone.now().isoformat(),
        "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
            gmail_import
        ),
        "request_fingerprint": str(request_fingerprint or "")[:64],
        "analysis_attempt": gmail_import.analysis_attempts,
        "source_fingerprint": gmail_import.source_fingerprint,
        "content_fingerprint": analysis.get("content_fingerprint"),
        "identity_match_version": candidates.get("identity_match_version"),
        "suggested": bool(suggested),
        "forwarded_identity_risk": bool(
            candidates.get("ai_identity_unverified_forwarded")
        ),
    }


def gmail_identity_approval_is_current(gmail_import):
    candidates = dict(gmail_import.candidates or {})
    if (
        candidates.get("identity_match_version")
        != GMAIL_IDENTITY_MATCH_VERSION
        or candidates.get("identity_reanalysis_required")
    ):
        return False
    approval = dict((gmail_import.analysis or {}).get("identity_approval") or {})
    if approval.get("version") != GMAIL_IDENTITY_APPROVAL_VERSION:
        return False
    if not gmail_import.selected_company_id:
        return False
    return bool(
        approval.get("company_id") == gmail_import.selected_company_id
        and approval.get("contact_id") == gmail_import.selected_contact_id
        and approval.get("analysis_attempt") == gmail_import.analysis_attempts
        and approval.get("source_fingerprint") == gmail_import.source_fingerprint
        and approval.get("identity_review_fingerprint")
        == gmail_identity_evidence_fingerprint(gmail_import)
    )


def gmail_suggested_company_is_approvable(gmail_import):
    candidates = dict(gmail_import.candidates or {})
    recommended_id = candidates.get("recommended_company_id")
    if (
        candidates.get("identity_match_version") != GMAIL_IDENTITY_MATCH_VERSION
        or not recommended_id
        or candidates.get("identity_conflict")
        or candidates.get("identity_reanalysis_required")
    ):
        return False
    candidate = next(
        (
            row
            for row in candidates.get("companies") or []
            if isinstance(row, dict) and row.get("company_id") == recommended_id
        ),
        None,
    )
    if not candidate:
        return False
    if not candidates.get("ai_identity_unverified_forwarded"):
        return True
    signals = {
        str((evidence or {}).get("signal") or "")
        for evidence in candidate.get("evidence") or []
        if isinstance(evidence, dict)
    }
    return bool(signals & TRUSTED_PHYSICAL_IDENTITY_SIGNALS)


def gmail_identity_review_projection(gmail_import):
    approval = dict((gmail_import.analysis or {}).get("identity_approval") or {})
    approved = gmail_identity_approval_is_current(gmail_import)
    return {
        "approved": approved,
        "identity_review_fingerprint": gmail_identity_evidence_fingerprint(
            gmail_import
        ),
        "approved_company_id": approval.get("company_id") if approved else None,
        "approved_contact_id": approval.get("contact_id") if approved else None,
        "approved_at": approval.get("approved_at") if approved else None,
        "suggestion_approvable": gmail_suggested_company_is_approvable(
            gmail_import
        ),
    }
