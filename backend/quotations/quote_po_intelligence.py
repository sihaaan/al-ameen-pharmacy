import hashlib
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from .ai_parsing import AIParseError, clean_preview_with_ai
from .contract_intelligence import gmail_fetch_message, gmail_fetch_message_metadata, gmail_search_messages
from .import_parsers import parse_text_preview
from .models import GmailOAuthConnection, QuotationAuditLog, QuotationOutcomePOImport, QuotationPOEvidence
from .services import audit_log, build_po_outcome_suggestions, ensure_outcome_reviewable


PO_KEYWORDS = [
    "PO",
    "LPO",
    "\"purchase order\"",
    "\"local purchase order\"",
    "approved",
    "accepted",
    "\"order confirmation\"",
]


def _clean_query_text(value):
    value = re.sub(r"[\r\n\t]+", " ", value or "").strip()
    return re.sub(r"\s+", " ", value)


def _quote_term(value):
    value = _clean_query_text(value)
    if not value:
        return ""
    return f'"{value}"' if " " in value or "-" in value else value


def _email_domain(value):
    match = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", value or "")
    return match.group(1).lower() if match else ""


def _quote_after_date(quotation):
    candidate = quotation.sent_at or quotation.finalized_at or quotation.created_at
    if candidate:
        if timezone.is_aware(candidate):
            candidate = timezone.localtime(candidate)
        return candidate.date()
    return timezone.localdate()


def build_quote_gmail_queries(quotation):
    after = _quote_after_date(quotation).strftime("%Y/%m/%d")
    keyword_group = " OR ".join(PO_KEYWORDS)
    company = _quote_term(getattr(quotation.company, "name", ""))
    quote_number = _quote_term(quotation.quotation_number)
    contact_email = getattr(quotation.contact, "email", "") if quotation.contact_id else ""
    domain = _email_domain(contact_email) or _email_domain(getattr(quotation.company, "email", ""))

    queries = []
    if quote_number:
        queries.append(f"{quote_number} ({keyword_group}) after:{after}")
        queries.append(f"{quote_number} after:{after}")
    if domain:
        queries.append(f"from:{domain} ({keyword_group}) after:{after}")
        queries.append(f"({keyword_group}) {domain} after:{after}")
    if company:
        queries.append(f"{company} ({keyword_group}) after:{after}")

    seen = set()
    unique_queries = []
    for query in queries:
        normalized = query.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_queries.append(query)
    return unique_queries


def _candidate_score(quotation, payload, query):
    subject = payload.get("subject", "") or ""
    snippet = payload.get("snippet", "") or ""
    sender = payload.get("sender", "") or ""
    recipients = payload.get("recipients", "") or ""
    haystack = f"{subject} {snippet} {sender} {recipients}".lower()
    score = 25
    reasons = []

    quote_number = (quotation.quotation_number or "").lower()
    if quote_number and quote_number in haystack:
        score += 45
        reasons.append("quote number appears in the email")

    company_name = (quotation.company.name if quotation.company_id else "").lower()
    company_tokens = [token for token in re.split(r"[^a-z0-9]+", company_name) if len(token) >= 3]
    if company_tokens and any(token in haystack for token in company_tokens[:4]):
        score += 15
        reasons.append("customer name appears in the email")

    contact_email = getattr(quotation.contact, "email", "") if quotation.contact_id else ""
    domain = _email_domain(contact_email) or _email_domain(getattr(quotation.company, "email", ""))
    if domain and domain in haystack:
        score += 15
        reasons.append(f"customer email domain {domain} appears")

    keyword_hits = [
        keyword.strip('"').lower()
        for keyword in PO_KEYWORDS
        if keyword.strip('"').lower() in haystack
    ]
    if keyword_hits:
        score += 15
        reasons.append("PO/LPO or acceptance keyword appears")

    if not reasons:
        reasons.append(f"matched targeted Gmail query: {query}")
    return min(score, 98), "; ".join(reasons)


def _source_hash(payload):
    digest = hashlib.sha256()
    for key in ["gmail_message_id", "gmail_thread_id", "subject", "sender", "sent_at", "snippet"]:
        digest.update(str(payload.get(key, "") or "").encode("utf-8", errors="ignore"))
    for attachment in payload.get("attachments") or []:
        digest.update(str(attachment.get("filename", "")).encode("utf-8", errors="ignore"))
        digest.update(str(attachment.get("size", "")).encode("utf-8", errors="ignore"))
        digest.update(str(attachment.get("source_file_ref", "")).encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def _get_gmail_connection(user):
    try:
        connection = user.quotation_gmail_connection
    except GmailOAuthConnection.DoesNotExist as exc:
        raise ValidationError("Connect Gmail before searching for PO evidence.") from exc
    if connection.status != GmailOAuthConnection.STATUS_CONNECTED:
        raise ValidationError("Reconnect Gmail before searching for PO evidence.")
    return connection


def find_quote_po_evidence(quotation, actor, *, limit=25):
    ensure_outcome_reviewable(quotation)
    connection = _get_gmail_connection(actor)
    limit = max(1, min(int(limit or 25), 50))
    queries = build_quote_gmail_queries(quotation)
    if not queries:
        raise ValidationError("This quotation does not have enough customer or quote details for Gmail search.")

    found_ids = set()
    evidence_ids = []
    for query in queries:
        result = gmail_search_messages(connection, query, max_messages=limit)
        for message in result.get("messages") or []:
            message_id = message.get("id")
            if not message_id or message_id in found_ids:
                continue
            found_ids.add(message_id)
            payload = gmail_fetch_message_metadata(connection, message_id)
            confidence, reason = _candidate_score(quotation, payload, query)
            evidence, _ = QuotationPOEvidence.objects.update_or_create(
                quotation=quotation,
                gmail_message_id=payload.get("gmail_message_id") or message_id,
                defaults={
                    "gmail_thread_id": payload.get("gmail_thread_id", ""),
                    "sender": payload.get("sender", "")[:500],
                    "recipients": payload.get("recipients", ""),
                    "subject": payload.get("subject", "")[:500],
                    "sent_at": payload.get("sent_at"),
                    "snippet": payload.get("snippet", ""),
                    "source_sha256": _source_hash(payload),
                    "matching_reason": reason,
                    "confidence": confidence,
                    "status": QuotationPOEvidence.STATUS_CANDIDATE,
                    "error": "",
                    "created_by": actor if getattr(actor, "is_authenticated", False) else None,
                },
            )
            evidence_ids.append(evidence.id)

    evidence = quotation.po_evidence.filter(id__in=evidence_ids).order_by("-confidence", "-sent_at", "-created_at")
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        quotation,
        message=f"Found {evidence.count()} Gmail PO evidence candidate(s) for {quotation.quotation_number}.",
        changes={"evidence_ids": list(evidence.values_list("id", flat=True)), "queries": queries},
    )
    return {"queries": queries, "count": evidence.count(), "evidence": list(evidence)}


def _preview_from_gmail_payload(payload):
    warnings = []
    rows = []
    parsed_attachment_count = 0
    for attachment in payload.get("attachments") or []:
        status = attachment.get("status")
        if status == "parsed":
            parsed_attachment_count += 1
            for row in attachment.get("lines") or []:
                enriched = dict(row)
                enriched.setdefault("source_filename", attachment.get("filename", ""))
                rows.append(enriched)
            warnings.extend(attachment.get("warnings") or [])
        elif status in {"failed", "skipped"}:
            warnings.append(f"{attachment.get('filename', 'Attachment')}: {attachment.get('reason', status)}")

    if rows:
        return {
            "source_filename": payload.get("subject") or "Gmail PO evidence",
            "source_sha256": _source_hash(payload),
            "source_file_ref": f"gmail:{payload.get('gmail_message_id', '')}",
            "parse_method": "gmail_attachment_bundle",
            "lines": rows,
            "warnings": warnings,
            "parsed_attachment_count": parsed_attachment_count,
        }

    text = payload.get("body_text") or payload.get("snippet") or ""
    preview = parse_text_preview(text)
    preview["source_filename"] = payload.get("subject") or "Gmail PO evidence"
    preview["source_sha256"] = _source_hash(payload)
    preview["source_file_ref"] = f"gmail:{payload.get('gmail_message_id', '')}"
    preview["parse_method"] = "gmail_body_text"
    preview["warnings"] = list(preview.get("warnings") or []) + warnings
    preview["parsed_attachment_count"] = 0
    return preview


def parse_quote_po_evidence(evidence, actor, *, use_ai=True):
    ensure_outcome_reviewable(evidence.quotation)
    connection = _get_gmail_connection(actor)
    try:
        payload = gmail_fetch_message(connection, evidence.gmail_message_id, include_attachments=True)
        preview = _preview_from_gmail_payload(payload)
        warnings = list(preview.get("warnings") or [])
        if use_ai:
            try:
                preview = clean_preview_with_ai(preview, actor=actor, requested_mode="auto", allow_vision=True)
            except AIParseError as exc:
                warnings.append(str(exc))

        suggestions, unmatched, missing_line_ids = build_po_outcome_suggestions(evidence.quotation, preview)
        po_import = QuotationOutcomePOImport.objects.create(
            quotation=evidence.quotation,
            gmail_evidence=evidence,
            source_type=QuotationOutcomePOImport.SOURCE_GMAIL,
            source_filename=preview.get("source_filename", evidence.subject or ""),
            source_sha256=preview.get("source_sha256", evidence.source_sha256 or ""),
            source_file_ref=preview.get("source_file_ref", f"gmail:{evidence.gmail_message_id}"),
            parse_method=preview.get("parse_method", "gmail_evidence"),
            parsed_rows=preview.get("lines") or [],
            suggestions=suggestions,
            unmatched_po_rows=unmatched,
            missing_quote_line_ids=missing_line_ids,
            warnings=warnings,
            created_by=actor if getattr(actor, "is_authenticated", False) else None,
        )
        evidence.gmail_thread_id = payload.get("gmail_thread_id", evidence.gmail_thread_id)
        evidence.sender = payload.get("sender", evidence.sender)[:500]
        evidence.recipients = payload.get("recipients", evidence.recipients)
        evidence.subject = payload.get("subject", evidence.subject)[:500]
        evidence.sent_at = payload.get("sent_at") or evidence.sent_at
        evidence.snippet = payload.get("snippet", evidence.snippet)
        evidence.extracted_text = (payload.get("body_text") or "")[:20000]
        evidence.attachments = payload.get("attachments") or []
        evidence.source_sha256 = preview.get("source_sha256", evidence.source_sha256)
        evidence.status = QuotationPOEvidence.STATUS_PARSED
        evidence.error = ""
        evidence.save(
            update_fields=[
                "gmail_thread_id",
                "sender",
                "recipients",
                "subject",
                "sent_at",
                "snippet",
                "extracted_text",
                "attachments",
                "source_sha256",
                "status",
                "error",
                "updated_at",
            ]
        )
        audit_log(
            actor,
            QuotationAuditLog.ACTION_UPDATED,
            evidence.quotation,
            message=f"Parsed Gmail PO evidence for {evidence.quotation.quotation_number}.",
            changes={
                "evidence_id": evidence.id,
                "po_import_id": po_import.id,
                "suggestions": len(suggestions),
                "unmatched": len(unmatched),
            },
        )
        return po_import
    except Exception as exc:
        evidence.status = QuotationPOEvidence.STATUS_FAILED
        evidence.error = str(exc)[:1000]
        evidence.save(update_fields=["status", "error", "updated_at"])
        raise

