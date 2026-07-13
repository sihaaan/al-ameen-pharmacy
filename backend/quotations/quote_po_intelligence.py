import hashlib
import os
import re
from datetime import datetime
from email.utils import getaddresses

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from .ai_parsing import AIParseError, clean_preview_with_ai, prefer_safe_ai_preview
from .contract_intelligence import (
    gmail_fetch_message,
    gmail_fetch_message_metadata,
    gmail_search_messages,
    resolve_gmail_connection,
)
from .import_parsers import parse_text_preview
from .models import Quotation, QuotationAuditLog, QuotationLPO, QuotationOutcomePOImport, QuotationPOEvidence
from .services import audit_log, build_guarded_po_outcome_suggestions, ensure_outcome_reviewable


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
# ``limit`` is the total Gmail result budget for each generated query, not an
# invitation to walk an unbounded mailbox. We may consume multiple Gmail pages
# within this budget, but stale reconciliation is only safe when every query
# reports that no further page exists.
MAX_EVIDENCE_MESSAGES_PER_QUERY = 50
MAX_EVIDENCE_PAGES_PER_QUERY = 10
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
# Only formats the Gmail ingestion path can treat as business documents count
# as PO/LPO attachment evidence. Inline signature/logo images are common and
# must never strengthen a match merely because the email mentions an order.
PO_ATTACHMENT_EXTENSIONS = {".pdf", ".xlsx", ".xls", ".xlsb"}
PO_NUMBER_RE = re.compile(
    r"\b(?:lpo|mpo|purchase\s+order|local\s+purchase\s+order)\s*(?:no\.?|number|#|:|-)?\s*[A-Z0-9][A-Z0-9/_-]{2,}"
    r"|\bpo\s*(?:no\.?|number|#|:|-)\s*[A-Z0-9][A-Z0-9/_-]{2,}"
    r"|\bpo\s+[0-9][A-Z0-9/_-]{2,}",
    re.IGNORECASE,
)
PO_WORD_RE = re.compile(r"\bpo\b", re.IGNORECASE)
QUOTE_REFERENCE_RE = re.compile(
    r"\b(?:quotation|quote)\s*(?:(?:no\.?|number|ref(?:erence)?|#)\s*[:#-]?|[:#-])\s*([A-Z0-9][A-Z0-9/_.-]{3,})",
    re.IGNORECASE,
)
AUTO_QUOTE_REFERENCE_RE = re.compile(r"\bQT-[A-Z0-9][A-Z0-9/_.-]*\b", re.IGNORECASE)
PUBLIC_EMAIL_DOMAINS = {
    "aol.com",
    "gmail.com",
    "googlemail.com",
    "hotmail.com",
    "icloud.com",
    "live.com",
    "mail.com",
    "outlook.com",
    "proton.me",
    "protonmail.com",
    "yahoo.com",
    "yahoo.co.uk",
    "ymail.com",
}
COMPANY_TOKEN_STOPWORDS = {
    "company",
    "contracting",
    "general",
    "group",
    "holding",
    "limited",
    "llc",
    "ltd",
    "services",
    "trading",
}


class EvidenceLinkConflict(ValidationError):
    """Raised when an email cannot safely be linked to one quotation."""


def _clean_query_text(value):
    value = re.sub(r"[\r\n\t]+", " ", value or "").strip()
    return re.sub(r"\s+", " ", value)


def _quote_term(value):
    value = _clean_query_text(value)
    if not value:
        return ""
    return f'"{value}"' if " " in value or "-" in value else value


def _email_addresses(value):
    return {
        address.lower()
        for _name, address in getaddresses([str(value or "")])
        if address and "@" in address
    }


def _raw_email_domain(value):
    addresses = _email_addresses(value)
    if not addresses:
        match = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", value or "")
        return match.group(1).lower() if match else ""
    return next(iter(addresses)).rsplit("@", 1)[-1]


def _is_public_email_domain(domain):
    domain = (domain or "").lower()
    return any(domain == public or domain.endswith(f".{public}") for public in PUBLIC_EMAIL_DOMAINS)


def _email_domain(value):
    domain = _raw_email_domain(value)
    if not domain or _is_public_email_domain(domain):
        return ""
    return domain


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
    tokens = [
        token
        for token in re.split(r"[^a-z0-9]+", compact_company)
        if len(token) >= 4 and token not in COMPANY_TOKEN_STOPWORDS
    ]
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


def _document_attachments(attachments):
    return [
        attachment
        for attachment in attachments or []
        if os.path.splitext(str((attachment or {}).get("filename") or ""))[1].lower()
        in PO_ATTACHMENT_EXTENSIONS
    ]


def _quote_after_datetime(quotation):
    candidate = quotation.sent_at or quotation.finalized_at or quotation.created_at
    if not candidate:
        return timezone.now()
    if timezone.is_naive(candidate):
        candidate = timezone.make_aware(candidate, timezone.get_current_timezone())
    return candidate


def build_quote_gmail_queries(quotation):
    # Gmail accepts Unix epoch seconds for exact boundaries; date-only queries
    # searched the whole day before a quotation was actually sent.
    after = int(_quote_after_datetime(quotation).timestamp())
    boundary = f"after:{after} -from:me"
    keyword_group = " OR ".join(PO_KEYWORDS)
    company = _quote_term(getattr(quotation.company, "name", ""))
    quote_number = _quote_term(quotation.quotation_number)
    contact_email = getattr(quotation.contact, "email", "") if quotation.contact_id else ""
    company_email = getattr(quotation.company, "email", "")
    domain = _email_domain(contact_email) or _email_domain(company_email)

    queries = []
    if quote_number:
        queries.append(f"{quote_number} ({keyword_group}) {boundary}")
        queries.append(f"{quote_number} {boundary}")
    for customer_email in [contact_email, company_email]:
        customer_email = next(iter(_email_addresses(customer_email)), "")
        if customer_email:
            queries.append(f"from:{customer_email} ({keyword_group}) {boundary}")
    if domain:
        queries.append(f"from:{domain} ({keyword_group}) {boundary}")
    if company:
        queries.append(f"{company} ({keyword_group}) {boundary}")

    seen = set()
    unique_queries = []
    for query in queries:
        normalized = query.lower()
        if normalized not in seen:
            seen.add(normalized)
            unique_queries.append(query)
    return unique_queries


def _reference_key(value):
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def _explicit_quote_references(value):
    def clean_reference(reference):
        reference = str(reference or "").upper().rstrip(".,;:")
        root, extension = os.path.splitext(reference)
        # The reference regex intentionally permits dots because some real
        # quote numbers contain them. Strip only document extensions we know
        # can follow a quote number in an attachment filename; arbitrary
        # suffixes remain part of the reference and therefore cannot produce
        # a false exact match.
        if extension.lower() in PO_ATTACHMENT_EXTENSIONS:
            reference = root
        return reference

    refs = {clean_reference(match.group(0)) for match in AUTO_QUOTE_REFERENCE_RE.finditer(value or "")}
    refs.update(clean_reference(match.group(1)) for match in QUOTE_REFERENCE_RE.finditer(value or ""))
    return {ref for ref in refs if _reference_key(ref) and re.search(r"\d", ref)}


def _payload_quote_reference_keys(payload):
    attachments = _document_attachments(payload.get("attachments") or [])
    attachment_names = " ".join(str((attachment or {}).get("filename") or "") for attachment in attachments)
    value = " ".join(
        [
            str(payload.get("subject") or ""),
            str(payload.get("snippet") or ""),
            str(payload.get("body_text") or "")[:5000],
            attachment_names,
        ]
    )
    return {_reference_key(reference) for reference in _explicit_quote_references(value)}


def _payload_exactly_references_quote(quotation, payload):
    quote_key = _reference_key(quotation.quotation_number)
    reference_keys = _payload_quote_reference_keys(payload)
    return bool(quote_key and reference_keys == {quote_key})


def _candidate_score(quotation, payload, query, *, mailbox_email=""):
    subject = payload.get("subject", "") or ""
    snippet = payload.get("snippet", "") or ""
    sender = payload.get("sender", "") or ""
    recipients = payload.get("recipients", "") or ""
    attachments = payload.get("attachments") or []
    document_attachments = _document_attachments(attachments)
    attachment_names = " ".join(
        str((attachment or {}).get("filename") or "") for attachment in document_attachments
    )
    body_text = payload.get("body_text", "") or ""
    raw_haystack = f"{subject} {snippet} {body_text[:5000]} {sender} {recipients} {attachment_names}"
    haystack = raw_haystack.lower()
    subject_lower = subject.lower()
    snippet_lower = snippet.lower()
    score = 0
    reasons = []

    sender_addresses = _email_addresses(sender)
    sender_names = " ".join(
        name
        for name, address in getaddresses([str(sender or "")])
        if name and address
    )
    private_sender_domains = " ".join(
        domain
        for domain in {_raw_email_domain(address) for address in sender_addresses}
        if domain and not _is_public_email_domain(domain)
    )
    if mailbox_email and mailbox_email.lower() in sender_addresses:
        return 0, "rejected: outbound message from the shared mailbox"

    sent_at = payload.get("sent_at")
    cutoff = _quote_after_datetime(quotation)
    if sent_at:
        if timezone.is_naive(sent_at):
            sent_at = timezone.make_aware(sent_at, timezone.get_current_timezone())
        if sent_at <= cutoff:
            return 0, "rejected: message predates the quotation send/finalize timestamp"

    references = _explicit_quote_references(raw_haystack)
    quote_key = _reference_key(quotation.quotation_number)
    reference_keys = {_reference_key(reference) for reference in references}
    quote_reference_match = bool(quote_key and quote_key in reference_keys)
    wrong_refs = sorted(ref for ref in references if _reference_key(ref) != quote_key)
    if wrong_refs:
        return 0, f"rejected: explicit reference belongs to another quotation ({', '.join(wrong_refs[:2])})"
    if quote_reference_match:
        score += 45
        reasons.append("strong match: quote number appears")

    if PO_NUMBER_RE.search(subject_lower):
        score += 35
        reasons.append("strong PO/LPO signal in subject")
    elif _contains_any(subject_lower, ORDER_DOCUMENT_TERMS) or PO_WORD_RE.search(subject_lower):
        score += 25
        reasons.append("PO/LPO signal in subject")

    contact_email = getattr(quotation.contact, "email", "") if quotation.contact_id else ""
    company_email = getattr(quotation.company, "email", "")
    expected_addresses = _email_addresses(contact_email) | _email_addresses(company_email)
    exact_sender_match = bool(sender_addresses & expected_addresses)
    if exact_sender_match:
        score += 18
        reasons.append("exact customer sender matched")
    domain = _email_domain(contact_email) or _email_domain(company_email)
    sender_domains = {_raw_email_domain(address) for address in sender_addresses}
    domain_match = bool(domain and domain in sender_domains)
    if domain_match:
        score += 10
        reasons.append(f"customer email domain matched: {domain}")

    company_identity_haystack = (
        f"{subject} {snippet} {body_text[:5000]} {sender_names} {private_sender_domains}"
    ).lower()
    company_score, company_reason = _company_match_strength(
        quotation.company.name if quotation.company_id else "",
        company_identity_haystack,
    )
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
    elif document_attachments and (PO_NUMBER_RE.search(haystack) or _contains_any(haystack, ORDER_DOCUMENT_TERMS)):
        score += 10
        reasons.append(f"{len(document_attachments)} document attachment(s) on a PO/LPO-like email")

    negative_hits = _contains_any(subject_lower, NEGATIVE_CONTEXT_TERMS)
    if negative_hits and not (PO_NUMBER_RE.search(haystack) or quote_reference_match):
        score -= 25
        reasons.append(f"low priority context: {', '.join(negative_hits[:2])}")

    document_signal = bool(
        PO_NUMBER_RE.search(haystack)
        or _contains_any(haystack, ORDER_DOCUMENT_TERMS)
        or attachment_matches
    )
    identity_signal = bool(
        quote_reference_match
        or exact_sender_match
        or domain_match
        or company_score
    )
    if not document_signal:
        return 0, "rejected: no PO/LPO or order-document signal"
    if not identity_signal:
        return 0, "rejected: no quotation or customer identity signal"
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
        digest.update(str(attachment.get("source_sha256", "")).encode("utf-8", errors="ignore"))
    return digest.hexdigest()


def _get_gmail_connection(user):
    connection = resolve_gmail_connection(user)
    if not connection:
        raise ValidationError("Connect the shared Gmail mailbox before searching for PO evidence.")
    return connection


def _get_evidence_gmail_connection(evidence, user):
    if evidence.gmail_connection_id:
        origin = evidence.gmail_connection
        if origin.status == origin.STATUS_CONNECTED:
            return origin
        raise ValidationError(
            f"Reconnect the evidence mailbox ({evidence.mailbox_email or origin.email}) before reviewing this email."
        )
    connection = _get_gmail_connection(user)
    if evidence.mailbox_email and connection.email.lower() != evidence.mailbox_email.lower():
        raise ValidationError(
            f"This evidence belongs to {evidence.mailbox_email}, not the currently connected shared mailbox."
        )
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


def _message_scope_q(connection, mailbox_email):
    scope = Q(gmail_connection=connection)
    normalized_mailbox = str(mailbox_email or getattr(connection, "email", "") or "").strip().lower()
    if normalized_mailbox:
        # OAuth credentials can be replaced while the physical shared mailbox
        # remains the same. Arbitration is mailbox-wide, not FK-wide.
        scope |= Q(mailbox_email__iexact=normalized_mailbox)
        scope |= Q(mailbox_email="", gmail_connection__email__iexact=normalized_mailbox)
    # Evidence created before mailbox provenance was introduced belongs to the
    # one designated shared mailbox and still needs to participate in safety
    # arbitration during the migration period.
    scope |= Q(gmail_connection__isnull=True, mailbox_email="")
    return scope


def _message_evidence_queryset(connection, mailbox_email, message_id):
    return QuotationPOEvidence.objects.filter(
        _message_scope_q(connection, mailbox_email),
        gmail_message_id=message_id,
    )


def _locked_message_evidence_queryset(connection, mailbox_email, message_id):
    # The mailbox scope joins the nullable Gmail connection so legacy rows can
    # still participate in arbitration. PostgreSQL rejects FOR UPDATE when it
    # also targets that nullable outer join; only evidence rows need locking.
    return (
        _message_evidence_queryset(connection, mailbox_email, message_id)
        .select_for_update(of=("self",))
        .select_related("quotation")
        .order_by("id")
    )


def _eligible_same_customer_quote_ids(quotation, payload):
    if not quotation.company_id:
        return {quotation.id}
    message_at = payload.get("sent_at")
    if message_at and timezone.is_naive(message_at):
        message_at = timezone.make_aware(message_at, timezone.get_current_timezone())
    eligible = set()
    quotations = Quotation.objects.filter(
        company_id=quotation.company_id,
        status__in=[Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT],
        is_historical_import=False,
    ).only("id", "sent_at", "finalized_at", "created_at")
    for candidate in quotations:
        if not message_at or _quote_after_datetime(candidate) < message_at:
            eligible.add(candidate.id)
    eligible.add(quotation.id)
    return eligible


def _unreviewed_evidence_statuses():
    return [
        QuotationPOEvidence.STATUS_CANDIDATE,
        QuotationPOEvidence.STATUS_AMBIGUOUS,
        QuotationPOEvidence.STATUS_SUPERSEDED,
        QuotationPOEvidence.STATUS_FAILED,
    ]


def _discovery_defaults(connection, payload, actor, confidence, reason):
    return {
        "gmail_connection": connection,
        "mailbox_email": connection.email or "",
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
        "error": "",
        "created_by": actor if getattr(actor, "is_authenticated", False) else None,
    }


def _store_arbitrated_evidence(quotation, connection, payload, actor, confidence, reason):
    message_id = payload.get("gmail_message_id")
    exact_reference = _payload_exactly_references_quote(quotation, payload)
    potential_quote_ids = _eligible_same_customer_quote_ids(quotation, payload)
    defaults = _discovery_defaults(connection, payload, actor, confidence, reason)
    arbitration = {"ambiguous_ids": [], "superseded_ids": []}

    with transaction.atomic():
        # Use the same deterministic mailbox/message lock order as staff
        # approval so a background rescan cannot deadlock with an approval.
        locked_rows = list(
            _locked_message_evidence_queryset(
                connection,
                connection.email or "",
                message_id,
            )
        )
        existing = next((row for row in locked_rows if row.quotation_id == quotation.id), None)
        peers = [row for row in locked_rows if row.quotation_id != quotation.id]
        reviewed_peers = [
            peer
            for peer in peers
            if peer.status == QuotationPOEvidence.STATUS_PARSED or peer.link_approved_at
        ]
        competing_peers = [
            peer
            for peer in peers
            if peer.status != QuotationPOEvidence.STATUS_NOT_RELEVANT
        ]

        if exact_reference and not reviewed_peers:
            desired_status = QuotationPOEvidence.STATUS_CANDIDATE
            desired_error = ""
            peer_ids = [
                peer.id
                for peer in peers
                if peer.status in _unreviewed_evidence_statuses()
            ]
            if peer_ids:
                superseded_error = (
                    f"Superseded because this email explicitly references {quotation.quotation_number}."
                )
                QuotationPOEvidence.objects.filter(id__in=peer_ids).update(
                    status=QuotationPOEvidence.STATUS_SUPERSEDED,
                    error=superseded_error,
                    updated_at=timezone.now(),
                )
                arbitration["superseded_ids"].extend(peer_ids)
        else:
            is_ambiguous = bool(
                reviewed_peers
                or competing_peers
                or (not exact_reference and len(potential_quote_ids) > 1)
            )
            desired_status = (
                QuotationPOEvidence.STATUS_AMBIGUOUS
                if is_ambiguous
                else QuotationPOEvidence.STATUS_CANDIDATE
            )
            if reviewed_peers:
                desired_error = (
                    "Ambiguous: this Gmail message is already approved or parsed for another quotation."
                )
            elif is_ambiguous:
                desired_error = (
                    "Ambiguous: this Gmail message can match multiple quotations. "
                    "A staff member must choose the correct quotation explicitly."
                )
            else:
                desired_error = ""
            if is_ambiguous:
                peer_ids = [
                    peer.id
                    for peer in peers
                    if peer.status in _unreviewed_evidence_statuses()
                ]
                if peer_ids:
                    QuotationPOEvidence.objects.filter(id__in=peer_ids).update(
                        status=QuotationPOEvidence.STATUS_AMBIGUOUS,
                        error=desired_error,
                        updated_at=timezone.now(),
                    )
                    arbitration["ambiguous_ids"].extend(peer_ids)

        defaults["status"] = desired_status
        defaults["error"] = desired_error
        if existing:
            defaults.pop("created_by", None)
            if existing.gmail_connection_id:
                defaults.pop("gmail_connection", None)
            if existing.mailbox_email:
                defaults.pop("mailbox_email", None)
            if existing.status == QuotationPOEvidence.STATUS_PARSED:
                # Full parse provenance is richer than metadata discovery;
                # a rescan must not replace it with attachment stubs or alter
                # the completed staff review.
                defaults.pop("attachments", None)
                defaults.pop("source_sha256", None)
                defaults.pop("status", None)
                defaults.pop("error", None)
            elif existing.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
                # Explicit staff rejection is immutable across rescans.
                defaults.pop("status", None)
                defaults.pop("error", None)

        evidence, _ = QuotationPOEvidence.objects.update_or_create(
            quotation=quotation,
            gmail_message_id=message_id,
            defaults=defaults,
        )
        if evidence.status == QuotationPOEvidence.STATUS_AMBIGUOUS:
            arbitration["ambiguous_ids"].append(evidence.id)
        elif evidence.status == QuotationPOEvidence.STATUS_SUPERSEDED:
            arbitration["superseded_ids"].append(evidence.id)
    return evidence, arbitration


def _supersede_stale_unreviewed_evidence(quotation, active_evidence_ids):
    stale = quotation.po_evidence.filter(
        status__in=[
            QuotationPOEvidence.STATUS_CANDIDATE,
            QuotationPOEvidence.STATUS_AMBIGUOUS,
            QuotationPOEvidence.STATUS_FAILED,
        ]
    )
    if active_evidence_ids:
        stale = stale.exclude(id__in=active_evidence_ids)
    stale_ids = list(stale.values_list("id", flat=True))
    if stale_ids:
        QuotationPOEvidence.objects.filter(id__in=stale_ids).update(
            status=QuotationPOEvidence.STATUS_SUPERSEDED,
            error=(
                "Superseded by the latest complete Gmail scan because the message "
                "was not returned as an active match."
            ),
            updated_at=timezone.now(),
        )
    return stale_ids


def _search_query_with_complete_flag(connection, query, *, max_messages):
    """Search within a bounded budget and report whether Gmail was exhausted."""
    messages = []
    page_token = ""
    seen_page_tokens = set()
    pages_fetched = 0

    while len(messages) < max_messages and pages_fetched < MAX_EVIDENCE_PAGES_PER_QUERY:
        remaining = max_messages - len(messages)
        result = gmail_search_messages(
            connection,
            query,
            max_messages=remaining,
            page_token=page_token,
        )
        pages_fetched += 1
        page_messages = list(result.get("messages") or [])
        messages.extend(page_messages[:remaining])
        next_page_token = str(result.get("next_page_token") or "")

        # A response that fits in the remaining budget and has no continuation
        # token is the only proof that this query was exhaustively fetched.
        if not next_page_token:
            return messages, len(page_messages) <= remaining
        if next_page_token in seen_page_tokens:
            return messages, False
        seen_page_tokens.add(next_page_token)
        if len(page_messages) > remaining or len(messages) >= max_messages:
            return messages, False
        page_token = next_page_token

    return messages, False


def find_quote_po_evidence(quotation, actor, *, limit=25):
    ensure_outcome_reviewable(quotation)
    connection = _get_gmail_connection(actor)
    limit = max(1, min(int(limit or 25), MAX_EVIDENCE_MESSAGES_PER_QUERY))
    queries = build_quote_gmail_queries(quotation)
    if not queries:
        raise ValidationError("This quotation does not have enough customer or quote details for Gmail search.")

    found_ids = set()
    evidence_ids = []
    ambiguous_ids = []
    superseded_ids = []
    incomplete_queries = []
    for query in queries:
        messages, query_complete = _search_query_with_complete_flag(
            connection,
            query,
            max_messages=limit,
        )
        if not query_complete:
            incomplete_queries.append(query)
        for message in messages:
            message_id = message.get("id")
            if not message_id or message_id in found_ids:
                continue
            found_ids.add(message_id)
            payload = gmail_fetch_message_metadata(connection, message_id)
            confidence, reason = _candidate_score(
                quotation,
                payload,
                query,
                mailbox_email=connection.email,
            )
            if confidence < MIN_EVIDENCE_CONFIDENCE:
                continue
            payload["gmail_message_id"] = payload.get("gmail_message_id") or message_id
            evidence, arbitration = _store_arbitrated_evidence(
                quotation,
                connection,
                payload,
                actor,
                confidence,
                reason,
            )
            ambiguous_ids.extend(arbitration["ambiguous_ids"])
            superseded_ids.extend(arbitration["superseded_ids"])
            if evidence.status in {
                QuotationPOEvidence.STATUS_CANDIDATE,
                QuotationPOEvidence.STATUS_AMBIGUOUS,
                QuotationPOEvidence.STATUS_PARSED,
            }:
                evidence_ids.append(evidence.id)

    scan_complete = not incomplete_queries
    if scan_complete:
        stale_ids = _supersede_stale_unreviewed_evidence(quotation, evidence_ids)
        superseded_ids.extend(stale_ids)
    evidence = quotation.po_evidence.filter(id__in=evidence_ids).order_by("-confidence", "-sent_at", "-created_at")
    candidate_count = evidence.filter(
        status__in=[
            QuotationPOEvidence.STATUS_CANDIDATE,
            QuotationPOEvidence.STATUS_PARSED,
        ]
    ).count()
    ambiguous_count = evidence.filter(status=QuotationPOEvidence.STATUS_AMBIGUOUS).count()
    evidence_count = evidence.count()
    scan_warning = ""
    if not scan_complete:
        query_label = "query" if len(incomplete_queries) == 1 else "queries"
        scan_warning = (
            f"Partial Gmail scan: {len(incomplete_queries)} search {query_label} could not be "
            f"exhausted within the {limit}-message safety cap. Existing evidence was preserved."
        )
    _mark_quote_po_scan(quotation, count=candidate_count, error=scan_warning)
    audit_log(
        actor,
        QuotationAuditLog.ACTION_UPDATED,
        quotation,
        message=(
            f"Found {candidate_count} Gmail PO evidence candidate(s) and "
            f"{ambiguous_count} ambiguous link(s) for {quotation.quotation_number}."
        ),
        changes={
            "evidence_ids": list(evidence.values_list("id", flat=True)),
            "ambiguous_ids": sorted(set(ambiguous_ids)),
            "superseded_ids": sorted(set(superseded_ids)),
            "queries": queries,
            "scan_complete": scan_complete,
            "incomplete_queries": incomplete_queries,
        },
    )
    return {
        "queries": queries,
        "count": candidate_count,
        "ambiguous_count": ambiguous_count,
        "evidence_count": evidence_count,
        "scan_complete": scan_complete,
        "incomplete_queries": incomplete_queries,
        "scan_warning": scan_warning,
        "evidence": list(evidence),
    }


def _parse_rescan_cutoff(value):
    if not value:
        return timezone.now()
    if hasattr(value, "isoformat"):
        cutoff = value
    else:
        cutoff = parse_datetime(str(value)) or timezone.now()
    if timezone.is_naive(cutoff):
        cutoff = timezone.make_aware(cutoff, timezone.get_current_timezone())
    return cutoff


def scan_quote_po_evidence_batch(actor, *, quote_limit=5, message_limit=10, rescan=False, rescan_before=None):
    _get_gmail_connection(actor)
    quote_limit = max(1, min(int(quote_limit or 5), 20))
    message_limit = max(1, min(int(message_limit or 10), MAX_EVIDENCE_MESSAGES_PER_QUERY))
    queryset = Quotation.objects.filter(
        status__in=[Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT],
        is_historical_import=False,
    )
    cutoff = None
    if rescan:
        cutoff = _parse_rescan_cutoff(rescan_before)
        queryset = queryset.filter(
            Q(po_evidence_last_scanned_at__isnull=True) |
            Q(po_evidence_last_scanned_at__lt=cutoff)
        )
    else:
        queryset = queryset.filter(po_evidence_last_scanned_at__isnull=True)

    remaining_before = queryset.count()
    quotations = list(
        queryset.select_related("company", "contact")
        .order_by("-sent_at", "-finalized_at", "-created_at", "-id")[:quote_limit]
    )

    processed = 0
    candidates_found = 0
    ambiguous_found = 0
    incomplete_scans = 0
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
            ambiguous_found += result["ambiguous_count"]
            scanned_quotes[-1]["candidate_count"] = result["count"]
            scanned_quotes[-1]["ambiguous_count"] = result["ambiguous_count"]
            scanned_quotes[-1]["scan_complete"] = result["scan_complete"]
            scanned_quotes[-1]["error"] = result["scan_warning"]
            if not result["scan_complete"]:
                incomplete_scans += 1
        except ValidationError as exc:
            detail = _validation_message(exc)
            _mark_quote_po_scan(quotation, count=0, error=detail)
            scanned_quotes[-1]["candidate_count"] = 0
            scanned_quotes[-1]["ambiguous_count"] = 0
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
            scanned_quotes[-1]["ambiguous_count"] = 0
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
        "ambiguous_found": ambiguous_found,
        "incomplete_scans": incomplete_scans,
        "remaining": remaining_after,
        "done": remaining_after == 0,
        "errors": errors,
        "quotes": scanned_quotes,
        "quote_limit": quote_limit,
        "message_limit": message_limit,
        "rescan": bool(rescan),
        "rescan_before": cutoff.isoformat() if cutoff else "",
    }


def _attachment_relevance_score(attachment, quotation):
    filename = str((attachment or {}).get("filename") or "")
    lowered = filename.lower()
    score = 0
    if PO_NUMBER_RE.search(lowered):
        score += 100
    elif PO_WORD_RE.search(lowered) or any(term in lowered for term in ["lpo", "mpo", "purchase order"]):
        score += 80
    elif "order" in lowered:
        score += 45
    if quotation.quotation_number and quotation.quotation_number.lower() in lowered:
        score += 60
    return score


def _select_primary_po_attachment(payload, quotation):
    parsed = [
        attachment
        for attachment in payload.get("attachments") or []
        if (attachment or {}).get("status") == "parsed" and (attachment or {}).get("lines")
    ]
    if not parsed:
        return None, []
    ranked = sorted(
        ((attachment, _attachment_relevance_score(attachment, quotation)) for attachment in parsed),
        key=lambda value: value[1],
        reverse=True,
    )
    best, best_score = ranked[0]
    warnings = []
    if best_score <= 0:
        if len(ranked) == 1 and (
            PO_NUMBER_RE.search(f"{payload.get('subject', '')} {payload.get('snippet', '')}")
            or _contains_any(f"{payload.get('subject', '')} {payload.get('snippet', '')}", ORDER_DOCUMENT_TERMS)
        ):
            return best, warnings
        warnings.append("No attachment was selected because multiple generic files could not be identified as the PO/LPO document.")
        return None, warnings
    for attachment, _score in ranked[1:]:
        warnings.append(
            f"{attachment.get('filename', 'Attachment')}: not merged; only the strongest PO/LPO attachment was parsed."
        )
    return best, warnings


def _po_relevance_context(evidence, payload, reason):
    quotation = evidence.quotation
    return {
        "workflow": "staff-reviewed Gmail PO/LPO evidence",
        "expected_quotation_number": quotation.quotation_number,
        "expected_customer": quotation.company.name if quotation.company_id else "",
        "mailbox_email": evidence.mailbox_email,
        "gmail_message_id": payload.get("gmail_message_id") or evidence.gmail_message_id,
        "sender": payload.get("sender") or evidence.sender,
        "subject": payload.get("subject") or evidence.subject,
        "relevance_reason": reason,
        "instruction": "Clean only rows from the selected PO/LPO document; do not combine rows from other attachments or quoted replies.",
    }


def _preview_from_gmail_payload(payload, evidence, *, relevance_reason=""):
    warnings = []
    rows = []
    selected_attachment, selection_warnings = _select_primary_po_attachment(payload, evidence.quotation)
    warnings.extend(selection_warnings)
    for attachment in payload.get("attachments") or []:
        status = attachment.get("status")
        if status == "parsed" and attachment is selected_attachment:
            for row in attachment.get("lines") or []:
                enriched = dict(row)
                enriched.setdefault("source_filename", attachment.get("filename", ""))
                enriched.setdefault("source_file_ref", attachment.get("source_file_ref", ""))
                enriched.setdefault(
                    "source_gmail_attachment_id",
                    attachment.get("attachment_id") or attachment.get("part_id") or "",
                )
                rows.append(enriched)
            warnings.extend(attachment.get("warnings") or [])
        elif status in {"failed", "skipped"}:
            warnings.append(f"{attachment.get('filename', 'Attachment')}: {attachment.get('reason', status)}")

    relevance_context = _po_relevance_context(evidence, payload, relevance_reason)
    if rows:
        return {
            "source_type": QuotationLPO.SOURCE_GMAIL,
            "source_filename": selected_attachment.get("filename") or payload.get("subject") or "Gmail PO evidence",
            "source_sha256": _source_hash(payload),
            "source_file_ref": selected_attachment.get("source_file_ref") or f"gmail:{payload.get('gmail_message_id', '')}",
            "source_file_size": int(selected_attachment.get("size") or 0),
            "parse_method": "gmail_primary_attachment",
            "original_text": payload.get("body_text") or "",
            "lines": rows,
            "warnings": warnings,
            "parsed_attachment_count": 1,
            "relevance_context": relevance_context,
            "meta": {
                "gmail_message_id": payload.get("gmail_message_id", ""),
                "gmail_thread_id": payload.get("gmail_thread_id", ""),
                "gmail_subject": payload.get("subject", ""),
                "selected_attachment_id": selected_attachment.get("attachment_id") or selected_attachment.get("part_id") or "",
                "selected_attachment_filename": selected_attachment.get("filename") or "",
            },
        }

    text = payload.get("body_text") or payload.get("snippet") or ""
    preview = parse_text_preview(text)
    preview["source_type"] = QuotationLPO.SOURCE_GMAIL
    preview["source_filename"] = payload.get("subject") or "Gmail PO evidence"
    preview["source_sha256"] = _source_hash(payload)
    preview["source_file_ref"] = f"gmail:{payload.get('gmail_message_id', '')}"
    preview["parse_method"] = "gmail_body_text"
    preview["warnings"] = list(preview.get("warnings") or []) + warnings
    preview["parsed_attachment_count"] = 0
    preview["relevance_context"] = relevance_context
    preview["meta"] = {
        **(preview.get("meta") or {}),
        "gmail_message_id": payload.get("gmail_message_id", ""),
        "gmail_thread_id": payload.get("gmail_thread_id", ""),
        "gmail_subject": payload.get("subject", ""),
    }
    return preview


def _extract_gmail_lpo_details(preview):
    meta = dict(preview.get("meta") or {})
    text_chunks = [
        str(preview.get("original_text") or ""),
        str(preview.get("source_filename") or ""),
        str(meta.get("gmail_subject") or ""),
    ]
    for row in preview.get("lines") or []:
        text_chunks.extend(
            str(row.get(key) or "")
            for key in ("raw_line", "raw_name", "requested_item_name", "item_name")
        )
    text = "\n".join(text_chunks)

    def clean_number(value):
        candidate = re.sub(r"\s+", "", str(value or "").strip().strip(" .:-#")).upper()
        candidate = re.sub(r"\.(?:PDF|XLSX?|XLSB)$", "", candidate)
        return candidate[:120] if candidate and re.search(r"\d", candidate) else ""

    lpo_number = ""
    for key in ("lpo_number", "po_number", "purchase_order_number", "document_number"):
        candidate = clean_number(meta.get(key))
        if candidate:
            lpo_number = candidate
            break
    if not lpo_number:
        match = re.search(
            r"\b(?:LPO|MPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*(?:NO\.?|NUMBER|#|:|-)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/_.-]{2,})",
            text,
            re.IGNORECASE,
        )
        if match:
            lpo_number = clean_number(match.group(1))
    if not lpo_number:
        match = re.search(
            r"\b(?:PO[_-])?PO(?P<number>\d{3}_\d{5,})(?!\d)",
            text,
            re.IGNORECASE,
        )
        if match:
            lpo_number = clean_number(match.group("number"))
    if not lpo_number:
        match = re.search(
            r"\b(?:LPO|MPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*[-#:]?\s*(\d[A-Z0-9/_.-]{2,})",
            text,
            re.IGNORECASE,
        )
        if match:
            lpo_number = clean_number(match.group(1))

    lpo_date = None
    for key in ("lpo_date", "po_date", "purchase_order_date", "document_date", "date"):
        lpo_date = parse_date(str(meta.get(key) or ""))
        if lpo_date:
            break
    if not lpo_date:
        match = re.search(
            r"\b(?:LPO|MPO|PO|P\.O\.|PURCHASE\s+ORDER)\s*DATE\s*[:#-]?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{2,4})",
            text,
            re.IGNORECASE,
        )
        if match:
            raw = match.group(1)
            lpo_date = parse_date(raw)
            if not lpo_date:
                for pattern in ("%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y"):
                    try:
                        lpo_date = datetime.strptime(raw, pattern).date()
                        break
                    except ValueError:
                        continue
    return {
        "lpo_number": lpo_number,
        "lpo_date": lpo_date,
        "parsed_meta": {
            **meta,
            "detected_lpo_number": lpo_number,
            "detected_lpo_date": lpo_date.isoformat() if lpo_date else "",
        },
    }


def _reviewed_message_conflicts(evidence, connection):
    mailbox_email = evidence.mailbox_email or connection.email or ""
    return list(
        _message_evidence_queryset(connection, mailbox_email, evidence.gmail_message_id)
        .exclude(pk=evidence.pk)
        .filter(Q(status=QuotationPOEvidence.STATUS_PARSED) | Q(link_approved_at__isnull=False))
        .select_related("quotation")
    )


def _preflight_evidence_approval(evidence, connection, payload):
    evidence.refresh_from_db(fields=["status", "link_approved_at", "error"])
    if evidence.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
        raise EvidenceLinkConflict("This email was explicitly marked not relevant and cannot be approved.")
    if (
        evidence.status == QuotationPOEvidence.STATUS_SUPERSEDED
        and not _payload_exactly_references_quote(evidence.quotation, payload)
    ):
        raise EvidenceLinkConflict(
            "This email link was superseded by a newer scan and cannot be approved for this quotation."
        )
    conflicts = _reviewed_message_conflicts(evidence, connection)
    if conflicts:
        quote_numbers = ", ".join(conflict.quotation.quotation_number for conflict in conflicts[:3])
        raise EvidenceLinkConflict(
            "This Gmail message is already approved or parsed for another quotation"
            f" ({quote_numbers}). Contact support or an administrator before approving this one."
        )


def _lock_and_resolve_evidence_approval(evidence, connection, payload):
    mailbox_email = evidence.mailbox_email or connection.email or ""
    # Lock every competing row in the same primary-key order. Locking only the
    # chosen row first lets simultaneous A->B and B->A approvals deadlock when
    # each request later tries to update the other's peer row.
    locked_rows = list(
        _locked_message_evidence_queryset(
            connection,
            mailbox_email,
            evidence.gmail_message_id,
        )
    )
    locked_by_id = {row.id: row for row in locked_rows}
    try:
        locked_evidence = locked_by_id[evidence.pk]
    except KeyError as exc:
        raise EvidenceLinkConflict(
            "This Gmail evidence is no longer available for the connected mailbox."
        ) from exc
    if locked_evidence.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
        raise EvidenceLinkConflict("This email was explicitly marked not relevant and cannot be approved.")
    if (
        locked_evidence.status == QuotationPOEvidence.STATUS_SUPERSEDED
        and not _payload_exactly_references_quote(locked_evidence.quotation, payload)
    ):
        raise EvidenceLinkConflict(
            "This email link was superseded by a newer scan and cannot be approved for this quotation."
        )

    peers = [row for row in locked_rows if row.pk != locked_evidence.pk]
    reviewed_conflicts = [
        row
        for row in peers
        if row.status == QuotationPOEvidence.STATUS_PARSED or row.link_approved_at
    ]
    if reviewed_conflicts:
        quote_numbers = ", ".join(conflict.quotation.quotation_number for conflict in reviewed_conflicts[:3])
        raise EvidenceLinkConflict(
            "This Gmail message is already approved or parsed for another quotation"
            f" ({quote_numbers}). Contact support or an administrator before approving this one."
        )

    unreviewed_statuses = set(_unreviewed_evidence_statuses())
    peer_ids = [row.id for row in peers if row.status in unreviewed_statuses]
    if peer_ids:
        QuotationPOEvidence.objects.filter(id__in=peer_ids).update(
            status=QuotationPOEvidence.STATUS_SUPERSEDED,
            error=(
                f"Superseded by explicit staff approval of this Gmail message for "
                f"{locked_evidence.quotation.quotation_number}."
            ),
            updated_at=timezone.now(),
        )
    return locked_evidence, peer_ids


def parse_quote_po_evidence(evidence, actor, *, use_ai=True, link_approved=False):
    ensure_outcome_reviewable(evidence.quotation)
    if not link_approved:
        raise ValidationError(
            "Explicit staff approval is required before linking this Gmail message to the quotation."
        )
    connection = _get_evidence_gmail_connection(evidence, actor)
    try:
        payload = gmail_fetch_message(connection, evidence.gmail_message_id, include_attachments=True)
        confidence, relevance_reason = _candidate_score(
            evidence.quotation,
            payload,
            "staff review",
            mailbox_email=connection.email,
        )
        if confidence < MIN_EVIDENCE_CONFIDENCE:
            evidence.status = QuotationPOEvidence.STATUS_SUPERSEDED
            evidence.error = relevance_reason[:1000]
            evidence.save(update_fields=["status", "error", "updated_at"])
            raise EvidenceLinkConflict(f"This email cannot be linked to the quotation: {relevance_reason}")

        _preflight_evidence_approval(evidence, connection, payload)

        preview = _preview_from_gmail_payload(
            payload,
            evidence,
            relevance_reason=relevance_reason,
        )
        deterministic_preview = preview
        warnings = list(preview.get("warnings") or [])
        if use_ai:
            try:
                ai_preview = clean_preview_with_ai(
                    deterministic_preview,
                    actor=actor,
                    requested_mode="auto",
                    allow_vision=True,
                )
                preview = prefer_safe_ai_preview(deterministic_preview, ai_preview)
            except AIParseError as exc:
                warnings.append(str(exc))
        warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
        preview["warnings"] = warnings

        preview, suggestions, unmatched, missing_line_ids = build_guarded_po_outcome_suggestions(
            evidence.quotation,
            deterministic_preview,
            preview,
        )
        warnings = list(dict.fromkeys([*warnings, *(preview.get("warnings") or [])]))
        preview["warnings"] = warnings
        details = _extract_gmail_lpo_details(preview)
        with transaction.atomic():
            locked_evidence, superseded_peer_ids = _lock_and_resolve_evidence_approval(
                evidence,
                connection,
                payload,
            )
            evidence_mailbox_email = locked_evidence.mailbox_email or connection.email or ""
            po_import = (
                QuotationOutcomePOImport.objects.filter(gmail_evidence=locked_evidence)
                .order_by("-created_at", "-id")
                .first()
            )
            import_values = {
                "quotation": locked_evidence.quotation,
                "source_type": QuotationOutcomePOImport.SOURCE_GMAIL,
                "source_filename": preview.get("source_filename", locked_evidence.subject or ""),
                "source_sha256": preview.get("source_sha256", locked_evidence.source_sha256 or ""),
                "source_file_ref": preview.get(
                    "source_file_ref",
                    f"gmail:{evidence_mailbox_email}:{locked_evidence.gmail_message_id}",
                ),
                "parse_method": preview.get("parse_method", "gmail_evidence"),
                "parsed_rows": preview.get("lines") or [],
                "suggestions": suggestions,
                "unmatched_po_rows": unmatched,
                "missing_quote_line_ids": missing_line_ids,
                "warnings": warnings,
            }
            if po_import:
                for field, value in import_values.items():
                    setattr(po_import, field, value)
                po_import.save(update_fields=[*import_values.keys(), "updated_at"])
            else:
                po_import = QuotationOutcomePOImport.objects.create(
                    gmail_evidence=locked_evidence,
                    created_by=actor if getattr(actor, "is_authenticated", False) else None,
                    **import_values,
                )

            lpo = QuotationLPO.objects.filter(gmail_evidence=locked_evidence).first()
            lpo_values = {
                "quotation": locked_evidence.quotation,
                "source_type": QuotationLPO.SOURCE_GMAIL,
                "source_filename": preview.get("source_filename", locked_evidence.subject or ""),
                "source_sha256": preview.get("source_sha256", locked_evidence.source_sha256 or ""),
                "source_file_ref": preview.get(
                    "source_file_ref",
                    f"gmail:{evidence_mailbox_email}:{locked_evidence.gmail_message_id}",
                ),
                "source_file_size": int(preview.get("source_file_size") or 0),
                "parse_method": preview.get("parse_method", "gmail_evidence"),
                "lpo_number": details["lpo_number"],
                "lpo_date": details["lpo_date"],
                "parsed_meta": {
                    **details["parsed_meta"],
                    "gmail_evidence_id": locked_evidence.id,
                    "gmail_message_id": locked_evidence.gmail_message_id,
                    "mailbox_email": evidence_mailbox_email,
                },
                "parsed_rows": preview.get("lines") or [],
                "warnings": warnings,
                "gmail_message_id": locked_evidence.gmail_message_id,
                "mailbox_email": evidence_mailbox_email,
            }
            if lpo:
                previous_status = lpo.status
                for field, value in lpo_values.items():
                    setattr(lpo, field, value)
                if previous_status != QuotationLPO.STATUS_CONFIRMED:
                    lpo.status = QuotationLPO.STATUS_NEEDS_REVIEW
                lpo.save(update_fields=[*lpo_values.keys(), "status", "updated_at"])
            else:
                lpo = QuotationLPO.objects.create(
                    gmail_evidence=locked_evidence,
                    status=QuotationLPO.STATUS_NEEDS_REVIEW,
                    received_by=actor if getattr(actor, "is_authenticated", False) else None,
                    **lpo_values,
                )

            locked_evidence.gmail_connection = locked_evidence.gmail_connection or connection
            locked_evidence.mailbox_email = evidence_mailbox_email
            locked_evidence.gmail_thread_id = payload.get("gmail_thread_id", locked_evidence.gmail_thread_id)
            locked_evidence.sender = payload.get("sender", locked_evidence.sender)[:500]
            locked_evidence.recipients = payload.get("recipients", locked_evidence.recipients)
            locked_evidence.subject = payload.get("subject", locked_evidence.subject)[:500]
            locked_evidence.sent_at = payload.get("sent_at") or locked_evidence.sent_at
            locked_evidence.snippet = payload.get("snippet", locked_evidence.snippet)
            locked_evidence.extracted_text = (payload.get("body_text") or "")[:20000]
            locked_evidence.attachments = payload.get("attachments") or []
            locked_evidence.source_sha256 = preview.get("source_sha256", locked_evidence.source_sha256)
            locked_evidence.matching_reason = relevance_reason
            locked_evidence.confidence = confidence
            locked_evidence.status = QuotationPOEvidence.STATUS_PARSED
            locked_evidence.error = ""
            if not locked_evidence.link_approved_at:
                locked_evidence.link_approved_at = timezone.now()
                locked_evidence.link_approved_by = (
                    actor if getattr(actor, "is_authenticated", False) else None
                )
            locked_evidence.save(
                update_fields=[
                    "gmail_connection",
                    "mailbox_email",
                    "gmail_thread_id",
                    "sender",
                    "recipients",
                    "subject",
                    "sent_at",
                    "snippet",
                    "extracted_text",
                    "attachments",
                    "source_sha256",
                    "matching_reason",
                    "confidence",
                    "status",
                    "error",
                    "link_approved_at",
                    "link_approved_by",
                    "updated_at",
                ]
            )
            evidence = locked_evidence
        audit_log(
            actor,
            QuotationAuditLog.ACTION_UPDATED,
            evidence.quotation,
            message=f"Parsed Gmail PO evidence for {evidence.quotation.quotation_number}.",
            changes={
                "evidence_id": evidence.id,
                "po_import_id": po_import.id,
                "lpo_id": lpo.id,
                "superseded_evidence_ids": superseded_peer_ids,
                "suggestions": len(suggestions),
                "unmatched": len(unmatched),
            },
        )
        return po_import
    except Exception as exc:
        # Do not erase a completed or explicitly rejected review because a
        # later retry failed (for example, a temporary Gmail/API outage).
        current_status = QuotationPOEvidence.objects.filter(pk=evidence.pk).values_list("status", flat=True).first()
        if isinstance(exc, EvidenceLinkConflict):
            if current_status not in {
                QuotationPOEvidence.STATUS_PARSED,
                QuotationPOEvidence.STATUS_NOT_RELEVANT,
                QuotationPOEvidence.STATUS_SUPERSEDED,
            }:
                evidence.status = QuotationPOEvidence.STATUS_AMBIGUOUS
            else:
                evidence.status = current_status
        elif current_status not in {
            QuotationPOEvidence.STATUS_PARSED,
            QuotationPOEvidence.STATUS_NOT_RELEVANT,
        }:
            evidence.status = QuotationPOEvidence.STATUS_FAILED
        else:
            evidence.status = current_status
        evidence.error = str(exc)[:1000]
        evidence.save(update_fields=["status", "error", "updated_at"])
        raise
