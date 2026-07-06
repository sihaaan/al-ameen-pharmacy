import hashlib
import os
import re

from django.core.exceptions import ValidationError
from django.utils import timezone

from .ai_parsing import AIParseError, clean_preview_with_ai
from .contract_intelligence import gmail_fetch_message, gmail_fetch_message_metadata, gmail_search_messages
from .import_parsers import parse_text_preview
from .models import GmailOAuthConnection, Quotation, QuotationAuditLog, QuotationOutcomePOImport, QuotationPOEvidence
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

MIN_EVIDENCE_CONFIDENCE = 45
ORDER_DOCUMENT_TERMS = [
    "lpo",
    "mpo",
    "purchase order",
    "local purchase order",
    "order confirmation",
    "accepted order",
]
ACCEPTANCE_TERMS = ["approved", "accepted", "confirmed", "proceed", "go ahead"]
NEGATIVE_CONTEXT_TERMS = [
    "invoice",
    "overdue",
    "statement",
    "soa",
    "payment",
    "reminder",
    "receipt",
    "credit note",
]
PO_ATTACHMENT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}
PO_NUMBER_RE = re.compile(
    r"\b(?:lpo|mpo|purchase\s+order|local\s+purchase\s+order)\s*(?:no\.?|number|#|:|-)?\s*[A-Z0-9][A-Z0-9/_-]{2,}"
    r"|\bpo\s*(?:no\.?|number|#|:|-)\s*[A-Z0-9][A-Z0-9/_-]{2,}"
    r"|\bpo\s+[0-9][A-Z0-9/_-]{2,}",
    re.IGNORECASE,
)
PO_WORD_RE = re.compile(r"\bpo\b", re.IGNORECASE)


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


def _contains_any(value, terms):
    value = (value or "").lower()
    return [term for term in terms if term in value]


def _company_match_strength(company_name, haystack):
    company_name = (company_name or "").lower()
    haystack = (haystack or "").lower()
    if not company_name:
        return 0, ""
    compact_company = re.sub(r"[^a-z0-9]+", " ", company_name).strip()
    if compact_company and compact_company in haystack:
        return 8, "customer name appears"
    tokens = [token for token in re.split(r"[^a-z0-9]+", compact_company) if len(token) >= 4]
    if tokens and any(token in haystack for token in tokens[:3]):
        return 4, "part of the customer name appears"
    return 0, ""


def _attachment_hint(attachments, subject, snippet):
    candidates = []
    for attachment in attachments or []:
        filename = str((attachment or {}).get("filename") or "")
        extension = os.path.splitext(filename)[1].lower()
        filename_lower = filename.lower()
        if not filename or extension not in PO_ATTACHMENT_EXTENSIONS:
            continue
        if (
            PO_NUMBER_RE.search(filename_lower)
            or any(term in filename_lower for term in ["lpo", "purchase order", "order", "mpo"])
            or PO_WORD_RE.search(filename_lower)
        ):
            candidates.append(filename)
        elif _contains_any(f"{subject} {snippet}", ORDER_DOCUMENT_TERMS):
            candidates.append(filename)
    return candidates[:3]


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
    attachments = payload.get("attachments") or []
    haystack = f"{subject} {snippet} {sender} {recipients}".lower()
    subject_lower = subject.lower()
    snippet_lower = snippet.lower()
    score = 0
    reasons = []

    quote_number = (quotation.quotation_number or "").lower()
    if quote_number and quote_number in haystack:
        score += 45
        reasons.append("strong match: quote number appears")

    if PO_NUMBER_RE.search(subject_lower):
        score += 35
        reasons.append("strong PO/LPO signal in subject")
    elif _contains_any(subject_lower, ORDER_DOCUMENT_TERMS) or PO_WORD_RE.search(subject_lower):
        score += 25
        reasons.append("PO/LPO signal in subject")

    contact_email = getattr(quotation.contact, "email", "") if quotation.contact_id else ""
    domain = _email_domain(contact_email) or _email_domain(getattr(quotation.company, "email", ""))
    if domain and domain in haystack:
        score += 10
        reasons.append(f"customer email domain matched: {domain}")

    company_score, company_reason = _company_match_strength(quotation.company.name if quotation.company_id else "", haystack)
    if company_score:
        score += company_score
        reasons.append(company_reason)

    if PO_NUMBER_RE.search(snippet_lower):
        score += 20
        reasons.append("PO/LPO reference appears in preview")
    elif _contains_any(snippet_lower, ["purchase order", "local purchase order", "lpo", "mpo"]) or PO_WORD_RE.search(snippet_lower):
        score += 14
        reasons.append("PO/LPO wording appears in preview")

    accepted_hits = _contains_any(haystack, ACCEPTANCE_TERMS)
    if accepted_hits:
        score += 8
        reasons.append(f"acceptance wording found: {', '.join(accepted_hits[:2])}")

    attachment_matches = _attachment_hint(attachments, subject, snippet)
    if attachment_matches:
        score += 22
        reasons.append(f"likely PO/LPO attachment: {', '.join(attachment_matches)}")
    elif attachments and (PO_NUMBER_RE.search(haystack) or _contains_any(haystack, ORDER_DOCUMENT_TERMS)):
        score += 10
        reasons.append(f"{len(attachments)} attachment(s) on a PO/LPO-like email")

    negative_hits = _contains_any(subject_lower, NEGATIVE_CONTEXT_TERMS)
    if negative_hits and not (PO_NUMBER_RE.search(haystack) or quote_number and quote_number in haystack):
        score -= 25
        reasons.append(f"low priority context: {', '.join(negative_hits[:2])}")

    if not reasons:
        reasons.append(f"weak targeted Gmail match: {query}")
    return max(0, min(score, 98)), "; ".join(reasons)


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


def _validation_message(exc):
    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(message) for message in exc.messages)
    return str(exc)


def _mark_quote_po_scan(quotation, *, count=0, error=""):
    quotation.po_evidence_last_scanned_at = timezone.now()
    quotation.po_evidence_last_scan_count = max(0, int(count or 0))
    quotation.po_evidence_last_scan_error = (error or "")[:1000]
    quotation.save(
        update_fields=[
            "po_evidence_last_scanned_at",
            "po_evidence_last_scan_count",
            "po_evidence_last_scan_error",
            "updated_at",
        ]
    )


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
            if confidence < MIN_EVIDENCE_CONFIDENCE:
                continue
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
                    "attachments": payload.get("attachments") or [],
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
    _mark_quote_po_scan(quotation, count=evidence.count())
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        quotation,
        message=f"Found {evidence.count()} Gmail PO evidence candidate(s) for {quotation.quotation_number}.",
        changes={"evidence_ids": list(evidence.values_list("id", flat=True)), "queries": queries},
    )
    return {"queries": queries, "count": evidence.count(), "evidence": list(evidence)}


def scan_quote_po_evidence_batch(actor, *, quote_limit=5, message_limit=10, rescan=False):
    _get_gmail_connection(actor)
    quote_limit = max(1, min(int(quote_limit or 5), 20))
    message_limit = max(1, min(int(message_limit or 10), 50))
    queryset = Quotation.objects.filter(
        status__in=[Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT],
        is_historical_import=False,
    )
    if not rescan:
        queryset = queryset.filter(po_evidence_last_scanned_at__isnull=True)

    remaining_before = queryset.count()
    quotations = list(
        queryset.select_related("company", "contact")
        .order_by("-sent_at", "-finalized_at", "-created_at", "-id")[:quote_limit]
    )

    processed = 0
    candidates_found = 0
    errors = []
    scanned_quotes = []
    for quotation in quotations:
        processed += 1
        scanned_quotes.append(
            {
                "id": quotation.id,
                "quotation_number": quotation.quotation_number,
                "company_name": quotation.company.name if quotation.company_id else "",
            }
        )
        try:
            result = find_quote_po_evidence(quotation, actor, limit=message_limit)
            candidates_found += result["count"]
            scanned_quotes[-1]["candidate_count"] = result["count"]
            scanned_quotes[-1]["error"] = ""
        except ValidationError as exc:
            detail = _validation_message(exc)
            _mark_quote_po_scan(quotation, count=0, error=detail)
            scanned_quotes[-1]["candidate_count"] = 0
            scanned_quotes[-1]["error"] = detail
            errors.append(
                {
                    "quote_id": quotation.id,
                    "quotation_number": quotation.quotation_number,
                    "detail": detail,
                }
            )
        except Exception as exc:
            detail = f"Gmail PO evidence search failed. {str(exc)[:250]}"
            _mark_quote_po_scan(quotation, count=0, error=detail)
            scanned_quotes[-1]["candidate_count"] = 0
            scanned_quotes[-1]["error"] = detail
            errors.append(
                {
                    "quote_id": quotation.id,
                    "quotation_number": quotation.quotation_number,
                    "detail": detail,
                }
            )

    remaining_after = max(0, remaining_before - processed)
    return {
        "processed": processed,
        "candidates_found": candidates_found,
        "remaining": remaining_after,
        "done": remaining_after == 0,
        "errors": errors,
        "quotes": scanned_quotes,
        "quote_limit": quote_limit,
        "message_limit": message_limit,
        "rescan": bool(rescan),
    }


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

