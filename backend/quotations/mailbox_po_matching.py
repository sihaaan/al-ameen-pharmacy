"""Pure, fail-closed ranking of an inbound PO message against quotations.

This module deliberately knows nothing about Django models.  Callers provide a
canonical message and the sent/finalised quotations that are eligible for the
mailbox search.  The result is advisory: it contains an explainable ranking and
never mutates a quotation, a line outcome, or an LPO record.

The PO's rows are the coverage denominator.  This is important because a
customer may order only a subset of a quotation.  Quote coverage is still
reported and contributes to the final automatic-decision safety gate, but
missing quote rows are not treated as a conflict.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import getaddresses
from functools import lru_cache
from typing import Any, Iterable, Mapping, Sequence

from rapidfuzz.fuzz import ratio as rapidfuzz_ratio


AUTOMATIC = "automatic"
AMBIGUOUS = "ambiguous"
UNMATCHED = "unmatched"

MAX_RETURNED_CANDIDATES = 3
DEFAULT_AUTOMATIC_THRESHOLD = 70.0
DEFAULT_AUTOMATIC_MARGIN = 12.0

_ORDER_SIGNAL_RE = re.compile(
    r"\b(?:lpo|mpo|local\s+purchase\s+order|purchase\s+order|order\s+confirmation)\b"
    r"|\bpo\s*(?:no\.?|number|#|:|-)\s*[a-z0-9]"
    r"|\b(?:accepted|approved)\s+(?:quote|quotation)\b"
    r"|\b(?:please\s+proceed|go\s+ahead)\b",
    re.IGNORECASE,
)
_AUTO_QUOTE_REFERENCE_RE = re.compile(r"\bQT[-_/][A-Z0-9][A-Z0-9/_.-]*\b", re.IGNORECASE)
_LABELLED_QUOTE_REFERENCE_RE = re.compile(
    r"\b(?:quotation|quote)\s*(?:(?:no\.?|number|ref(?:erence)?|#)\s*[:#-]?|[:#-])"
    r"\s*([A-Z0-9][A-Z0-9/_.-]{3,})",
    re.IGNORECASE,
)
_TOKEN_RE = re.compile(r"[a-z]+\d+[a-z0-9]*|\d+(?:\.\d+)?|[a-z]+|%")
_MEASUREMENT_RE = re.compile(
    r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*"
    r"(mcg|ug|mg|gm|g|kg|ml|ltr|lt|litres?|liters?|l|mm|cm|metres?|meters?|m|"
    r"iu|units?|%)(?![a-z])",
    re.IGNORECASE,
)
_DIMENSION_RE = re.compile(
    r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*[x×]\s*(\d+(?:\.\d+)?)"
    r"(?:\s*[x×]\s*(\d+(?:\.\d+)?))?\s*(mm|cm|m)?(?![a-z])",
    re.IGNORECASE,
)
_SIZE_RE = re.compile(r"\bsize\s*[:#-]?\s*([a-z]?\d+(?:\.\d+)?|[a-z]+)\b", re.IGNORECASE)
_PACK_RE = re.compile(
    r"(?:\b(?:pack|box|packet|pkt|strip)\s*(?:of|x)?|(?<![a-z0-9])x)\s*(\d+)\b",
    re.IGNORECASE,
)

_TOKEN_ALIASES = {
    "ug": "mcg",
    "gm": "g",
    "ltr": "l",
    "lt": "l",
    "litre": "l",
    "litres": "l",
    "liter": "l",
    "liters": "l",
    "metre": "m",
    "metres": "m",
    "meter": "m",
    "meters": "m",
    "pcs": "piece",
    "pc": "piece",
    "nos": "piece",
    "no": "piece",
    "units": "unit",
    "tabs": "tablet",
    "tablets": "tablet",
    "caps": "capsule",
    "capsules": "capsule",
    "bottles": "bottle",
    "boxes": "box",
    "packs": "pack",
    "packets": "pack",
    "pkts": "pack",
    "pieces": "piece",
    "pairs": "pair",
    "rolls": "roll",
    "strips": "strip",
    "tubes": "tube",
    "gloves": "glove",
    "bandages": "bandage",
    "gauzes": "gauze",
}
_CORE_NOISE = {
    "a",
    "an",
    "and",
    "each",
    "for",
    "of",
    "per",
    "the",
    "unit",
    "piece",
    "pcs",
    "nos",
}
_PUBLIC_EMAIL_DOMAINS = {
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
_COMPANY_NOISE = {
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


@dataclass(frozen=True)
class MailboxPOLine:
    line_id: Any = ""
    name: str = ""
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    unit: str = ""
    source: str = "attachment"


@dataclass(frozen=True)
class CanonicalMailboxMessage:
    message_id: str = ""
    sender: str = ""
    recipients: tuple[str, ...] = ()
    subject: str = ""
    body: str = ""
    received_at: datetime | None = None
    parsed_rows: tuple[MailboxPOLine, ...] = ()
    lpo_references: tuple[str, ...] = ()
    quotation_references: tuple[str, ...] = ()
    # Reconciliation can scope quotation references to one attachment.  When
    # that provenance is available, re-scanning the surrounding email body
    # would re-introduce references belonging to sibling attachments.
    quotation_references_are_authoritative: bool = False
    # AI-vision references remain useful reviewer provenance, but they must not
    # hard-filter candidates or receive the exact-reference score unless a
    # deterministic source independently corroborates them.
    quotation_references_are_review_only: bool = False
    company_name: str = ""
    document_total: Decimal | None = None
    # Parser warnings must travel with the exact document variant that produced
    # the rows.  ``material_warnings`` is available for callers that distinguish
    # a harmless parser note from a warning that makes the extracted commercial
    # data unsafe.  Both are fail-closed for automatic matching; staff can still
    # review the ranked candidate.
    parser_warnings: tuple[str, ...] = ()
    material_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class EligibleQuoteLine:
    line_id: Any = ""
    name: str = ""
    description: str = ""
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    line_total: Decimal | None = None
    unit: str = ""


@dataclass(frozen=True)
class EligibleQuotation:
    quote_id: Any
    quotation_number: str
    sent_at: datetime | None = None
    finalized_at: datetime | None = None
    created_at: datetime | None = None
    company_name: str = ""
    customer_emails: tuple[str, ...] = ()
    lines: tuple[EligibleQuoteLine, ...] = ()
    grand_total: Decimal | None = None


@dataclass(frozen=True)
class ScoreComponent:
    signal: str
    score: float
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"signal": self.signal, "score": round(self.score, 3), "detail": self.detail}


@dataclass(frozen=True)
class MatchedLine:
    po_line_id: Any
    quote_line_id: Any
    po_name: str
    quote_name: str
    name_similarity: float
    quantity_result: str
    price_result: str
    total_result: str
    unit_result: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "po_line_id": self.po_line_id,
            "quote_line_id": self.quote_line_id,
            "po_name": self.po_name,
            "quote_name": self.quote_name,
            "name_similarity": round(self.name_similarity, 3),
            "quantity_result": self.quantity_result,
            "price_result": self.price_result,
            "total_result": self.total_result,
            "unit_result": self.unit_result,
        }


@dataclass(frozen=True)
class RankedQuotationCandidate:
    quote_id: Any
    quotation_number: str
    score: float
    components: tuple[ScoreComponent, ...]
    matched_lines: tuple[MatchedLine, ...]
    po_line_count: int
    quote_line_count: int
    item_coverage: float
    quote_coverage: float
    average_name_similarity: float
    exact_quote_reference: bool
    exact_sender: bool
    quantity_exact_count: int
    quantity_reduced_count: int
    quantity_conflict_count: int
    price_exact_count: int
    price_conflict_count: int
    total_exact_count: int
    total_conflict_count: int
    spec_conflict_count: int
    unit_conflict_count: int
    document_total_result: str = "unknown"
    document_total_provided: bool = False
    commercial_exact_row_count: int = 0
    commercial_row_coverage: float = 0.0
    commercial_corroboration_result: str = "insufficient"
    parser_warnings: tuple[str, ...] = ()
    material_warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "quote_id": self.quote_id,
            "quotation_number": self.quotation_number,
            "score": round(self.score, 3),
            "components": [component.as_dict() for component in self.components],
            "matched_lines": [match.as_dict() for match in self.matched_lines],
            "po_line_count": self.po_line_count,
            "quote_line_count": self.quote_line_count,
            "item_coverage": round(self.item_coverage, 4),
            "quote_coverage": round(self.quote_coverage, 4),
            "average_name_similarity": round(self.average_name_similarity, 4),
            "exact_quote_reference": self.exact_quote_reference,
            "exact_sender": self.exact_sender,
            "quantity_exact_count": self.quantity_exact_count,
            "quantity_reduced_count": self.quantity_reduced_count,
            "quantity_conflict_count": self.quantity_conflict_count,
            "price_exact_count": self.price_exact_count,
            "price_conflict_count": self.price_conflict_count,
            "total_exact_count": self.total_exact_count,
            "total_conflict_count": self.total_conflict_count,
            "spec_conflict_count": self.spec_conflict_count,
            "unit_conflict_count": self.unit_conflict_count,
            "document_total_result": self.document_total_result,
            "document_total_provided": self.document_total_provided,
            "commercial_exact_row_count": self.commercial_exact_row_count,
            "commercial_row_coverage": round(self.commercial_row_coverage, 4),
            "commercial_corroboration_result": self.commercial_corroboration_result,
            "parser_warnings": list(self.parser_warnings),
            "material_warnings": list(self.material_warnings),
        }


@dataclass(frozen=True)
class MailboxMatchResult:
    status: str
    candidates: tuple[RankedQuotationCandidate, ...] = ()
    automatic_winner: RankedQuotationCandidate | None = None
    ambiguity_margin: float | None = None
    evaluated_count: int = 0
    rejected_count: int = 0
    rejection_summary: tuple[tuple[str, int], ...] = ()
    reason: str = ""
    automatic_blockers: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "automatic_winner": self.automatic_winner.as_dict() if self.automatic_winner else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "ambiguity_margin": (
                round(self.ambiguity_margin, 3) if self.ambiguity_margin is not None else None
            ),
            "evaluated_count": self.evaluated_count,
            "rejected_count": self.rejected_count,
            "rejection_summary": [
                {"reason": reason, "count": count} for reason, count in self.rejection_summary
            ],
            "reason": self.reason,
            "automatic_blockers": list(self.automatic_blockers),
        }


@dataclass(frozen=True)
class _TextIdentity:
    normalized_name: str
    normalized_combined: str
    core_name: str
    core_combined: str
    measurements: frozenset[str]
    dimensions: frozenset[str]
    sizes: frozenset[str]
    packs: frozenset[str]
    models: frozenset[str]


@dataclass(frozen=True)
class _Edge:
    po_index: int
    quote_index: int
    priority: float
    similarity: float
    quantity_result: str
    price_result: str
    total_result: str
    unit_result: str


@dataclass
class _Evaluation:
    candidate: RankedQuotationCandidate | None = None
    rejection: str = ""


def _mapping_value(value: Any, *keys: str, default: Any = None) -> Any:
    for key in keys:
        if isinstance(value, Mapping) and key in value:
            return value[key]
        if hasattr(value, key):
            return getattr(value, key)
    return default


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None or value == "":
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Sequence):
        return tuple(value)
    return tuple(value)


def _warning_sequence(value: Any) -> tuple[str, ...]:
    warnings = []
    for item in _as_sequence(value):
        if isinstance(item, Mapping):
            item = _mapping_value(item, "message", "warning", "detail", default="")
        rendered = str(item or "").strip()
        if rendered and rendered not in warnings:
            warnings.append(rendered)
    return tuple(warnings)


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        number = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _datetime(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def canonicalize_po_line(value: MailboxPOLine | Mapping[str, Any], index: int = 0) -> MailboxPOLine:
    if isinstance(value, MailboxPOLine):
        return value
    return MailboxPOLine(
        line_id=_mapping_value(value, "line_id", "id", "row_number", default=index),
        name=str(_mapping_value(value, "name", "item_name", "product_name", default="") or ""),
        description=str(_mapping_value(value, "description", "item_description", default="") or ""),
        quantity=_decimal(_mapping_value(value, "quantity", "qty")),
        unit_price=_decimal(_mapping_value(value, "unit_price", "price", "rate")),
        line_total=_decimal(_mapping_value(value, "line_total", "total", "amount")),
        unit=str(_mapping_value(value, "unit", "uom", default="") or ""),
        source=str(_mapping_value(value, "source", "source_kind", default="attachment") or "attachment"),
    )


def canonicalize_message(value: CanonicalMailboxMessage | Mapping[str, Any]) -> CanonicalMailboxMessage:
    if isinstance(value, CanonicalMailboxMessage):
        return value
    rows = _as_sequence(
        _mapping_value(
            value,
            "parsed_rows",
            "parsed_attachment_rows",
            "attachment_rows",
            "po_lines",
            "lines",
            default=(),
        )
    )
    recipients = _mapping_value(value, "recipients", "to", default=())
    return CanonicalMailboxMessage(
        message_id=str(_mapping_value(value, "message_id", "gmail_message_id", "id", default="") or ""),
        sender=str(_mapping_value(value, "sender", "from_address", "from", default="") or ""),
        recipients=tuple(str(item) for item in _as_sequence(recipients) if item),
        subject=str(_mapping_value(value, "subject", default="") or ""),
        body=str(_mapping_value(value, "body", "body_text", "snippet", default="") or ""),
        received_at=_datetime(_mapping_value(value, "received_at", "sent_at", "internal_date")),
        parsed_rows=tuple(canonicalize_po_line(row, index) for index, row in enumerate(rows, start=1)),
        lpo_references=tuple(
            str(item)
            for item in _as_sequence(
                _mapping_value(value, "lpo_references", "lpo_refs", "po_references", default=())
            )
            if item
        ),
        quotation_references=tuple(
            str(item)
            for item in _as_sequence(
                _mapping_value(value, "quotation_references", "quote_references", "quote_refs", default=())
            )
            if item
        ),
        quotation_references_are_authoritative=bool(
            _mapping_value(
                value,
                "quotation_references_are_authoritative",
                "authoritative_quotation_references",
                default=False,
            )
        ),
        quotation_references_are_review_only=bool(
            _mapping_value(
                value,
                "quotation_references_are_review_only",
                "review_only_quotation_references",
                default=False,
            )
        ),
        company_name=str(_mapping_value(value, "company_name", "customer_name", default="") or ""),
        document_total=_decimal(_mapping_value(value, "document_total", "grand_total", "total")),
        parser_warnings=_warning_sequence(
            _mapping_value(
                value,
                "parser_warnings",
                "parse_warnings",
                "warnings",
                default=(),
            )
        ),
        material_warnings=_warning_sequence(
            _mapping_value(value, "material_warnings", "blocking_warnings", default=())
        ),
    )


def canonicalize_quote_line(
    value: EligibleQuoteLine | Mapping[str, Any], index: int = 0
) -> EligibleQuoteLine:
    if isinstance(value, EligibleQuoteLine):
        return value
    return EligibleQuoteLine(
        line_id=_mapping_value(value, "line_id", "id", default=index),
        name=str(_mapping_value(value, "name", "item_name", "product_name", default="") or ""),
        description=str(_mapping_value(value, "description", "item_description", default="") or ""),
        quantity=_decimal(_mapping_value(value, "quantity", "quoted_quantity", "qty")),
        unit_price=_decimal(_mapping_value(value, "unit_price", "quoted_unit_price", "price", "rate")),
        line_total=_decimal(_mapping_value(value, "line_total", "quoted_total", "total", "amount")),
        unit=str(_mapping_value(value, "unit", "uom", default="") or ""),
    )


def canonicalize_quotation(value: EligibleQuotation | Mapping[str, Any]) -> EligibleQuotation:
    if isinstance(value, EligibleQuotation):
        return value
    lines = _as_sequence(_mapping_value(value, "lines", "quotation_lines", default=()))
    emails = _mapping_value(value, "customer_emails", "emails", "contact_emails", default=())
    return EligibleQuotation(
        quote_id=_mapping_value(value, "quote_id", "id"),
        quotation_number=str(
            _mapping_value(value, "quotation_number", "quote_number", "number", default="") or ""
        ),
        sent_at=_datetime(_mapping_value(value, "sent_at")),
        finalized_at=_datetime(_mapping_value(value, "finalized_at")),
        created_at=_datetime(_mapping_value(value, "created_at")),
        company_name=str(_mapping_value(value, "company_name", "customer_name", default="") or ""),
        customer_emails=tuple(str(item) for item in _as_sequence(emails) if item),
        lines=tuple(canonicalize_quote_line(line, index) for index, line in enumerate(lines, start=1)),
        grand_total=_decimal(_mapping_value(value, "grand_total", "total")),
    )


def _ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(character for character in text if not unicodedata.combining(character))
    return (
        text.replace("’", "'")
        .replace("`", "'")
        .replace("×", " x ")
        .replace("µ", "u")
        .lower()
    )


def _normalize_tokens(value: str, *, drop_noise: bool = False) -> tuple[str, ...]:
    tokens = []
    for raw_token in _TOKEN_RE.findall(_ascii_text(value)):
        token = _TOKEN_ALIASES.get(raw_token, raw_token)
        if token.endswith("'s"):
            token = token[:-2]
        if drop_noise and token in _CORE_NOISE:
            continue
        tokens.append(token)
    return tuple(tokens)


def _normalize_text(value: str, *, drop_noise: bool = False) -> str:
    return " ".join(_normalize_tokens(value, drop_noise=drop_noise))


def _canonical_number(value: str | Decimal) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return str(value)
    rendered = format(number.normalize(), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered


def _measurement_key(number: str, unit: str) -> str:
    value = Decimal(number)
    unit = _TOKEN_ALIASES.get(unit.lower(), unit.lower())
    if unit == "mcg":
        return f"{_canonical_number(value)}mcg"
    if unit == "mg":
        return f"{_canonical_number(value * 1000)}mcg"
    if unit == "g":
        return f"{_canonical_number(value * 1_000_000)}mcg"
    if unit == "kg":
        return f"{_canonical_number(value * 1_000_000_000)}mcg"
    if unit == "l":
        return f"{_canonical_number(value * 1000)}ml"
    if unit == "ml":
        return f"{_canonical_number(value)}ml"
    if unit == "m":
        return f"{_canonical_number(value * 1000)}mm"
    if unit == "cm":
        return f"{_canonical_number(value * 10)}mm"
    if unit == "mm":
        return f"{_canonical_number(value)}mm"
    if unit in {"unit", "units"}:
        unit = "iu"
    return f"{_canonical_number(value)}{unit}"


@lru_cache(maxsize=50_000)
def _line_identity(name: str, description: str = "") -> _TextIdentity:
    name = str(name or "")
    combined = " ".join(part for part in [name, str(description or "")] if part)
    ascii_combined = _ascii_text(combined)
    measurements = frozenset(
        _measurement_key(number, unit)
        for number, unit in _MEASUREMENT_RE.findall(ascii_combined)
    )
    dimensions = set()
    for first, second, third, unit in _DIMENSION_RE.findall(ascii_combined):
        values = [_canonical_number(first), _canonical_number(second)]
        if third:
            values.append(_canonical_number(third))
        dimensions.add("x".join(values) + (unit.lower() if unit else ""))
    sizes = frozenset(_normalize_text(size) for size in _SIZE_RE.findall(ascii_combined))
    packs = frozenset(_PACK_RE.findall(ascii_combined))
    models = frozenset(
        token
        for token in _TOKEN_RE.findall(ascii_combined)
        if re.search(r"[a-z]", token) and re.search(r"\d", token)
    )
    stripped_name = _PACK_RE.sub(" ", _SIZE_RE.sub(" ", _MEASUREMENT_RE.sub(" ", _ascii_text(name))))
    stripped_combined = _PACK_RE.sub(
        " ", _SIZE_RE.sub(" ", _MEASUREMENT_RE.sub(" ", ascii_combined))
    )
    return _TextIdentity(
        normalized_name=_normalize_text(name),
        normalized_combined=_normalize_text(combined),
        core_name=_normalize_text(stripped_name, drop_noise=True),
        core_combined=_normalize_text(stripped_combined, drop_noise=True),
        measurements=measurements,
        dimensions=frozenset(dimensions),
        sizes=sizes,
        packs=packs,
        models=models,
    )


def _sets_conflict(left: frozenset[str], right: frozenset[str]) -> bool:
    return bool(left and right and left.isdisjoint(right))


def _spec_conflict(left: _TextIdentity, right: _TextIdentity) -> bool:
    return any(
        _sets_conflict(left_value, right_value)
        for left_value, right_value in [
            (left.measurements, right.measurements),
            (left.dimensions, right.dimensions),
            (left.sizes, right.sizes),
            (left.packs, right.packs),
            (left.models, right.models),
        ]
    )


def _text_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    if left.replace(" ", "") == right.replace(" ", ""):
        return 0.99
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    common = left_tokens & right_tokens
    if not common:
        return 0.0
    union = left_tokens | right_tokens
    jaccard = len(common) / len(union)
    containment = len(common) / min(len(left_tokens), len(right_tokens))
    if containment == 1:
        if min(len(left_tokens), len(right_tokens)) >= 2:
            containment_score = 0.88
        elif max(len(left_tokens), len(right_tokens)) <= 2:
            containment_score = 0.68
        else:
            containment_score = 0.55
    else:
        containment_score = containment * 0.82
    # This is the matching hot path: a mailbox document may be compared with
    # thousands of quotation lines. RapidFuzz implements the same normalized
    # edit-similarity calculation in native code and keeps a mailbox-wide
    # reconciliation comfortably inside the web-worker timeout.
    sequence = (rapidfuzz_ratio(left, right) / 100.0) * 0.9
    return min(1.0, max(jaccard, containment_score, sequence))


def _identity_similarity(left: _TextIdentity, right: _TextIdentity) -> float:
    comparisons = [
        (left.normalized_name, right.normalized_name),
        (left.core_name, right.core_name),
        (left.normalized_combined, right.normalized_combined),
        (left.core_combined, right.core_combined),
        (left.normalized_name, right.normalized_combined),
        (left.normalized_combined, right.normalized_name),
    ]
    best = 0.0
    for first, second in comparisons:
        best = max(best, _text_similarity(first, second))
        if best >= 1.0:
            return 1.0
    return best


def _compare_quantity(po_value: Decimal | None, quote_value: Decimal | None) -> str:
    if po_value is None or quote_value is None:
        return "unknown"
    tolerance = max(Decimal("0.001"), abs(quote_value) * Decimal("0.005"))
    if abs(po_value - quote_value) <= tolerance:
        return "exact"
    if po_value >= 0 and quote_value > 0 and po_value < quote_value:
        return "reduced"
    return "conflict"


def _compare_money(left: Decimal | None, right: Decimal | None) -> str:
    if left is None or right is None:
        return "unknown"
    # Commercial values are a safety gate, not a fuzzy identity signal. Small
    # currency rounding is tolerated, but a merely "close" amount must never
    # help an automatic link when the line arithmetic or document total differs.
    if abs(left - right) <= Decimal("0.02"):
        return "exact"
    return "conflict"


def _normalize_unit(value: str) -> str:
    tokens = _normalize_tokens(value)
    if not tokens:
        return ""
    unit = tokens[0]
    aliases = {
        "bottle": "bottle",
        "box": "box",
        "each": "piece",
        "no": "piece",
        "nos": "piece",
        "pc": "piece",
        "pcs": "piece",
        "piece": "piece",
        "unit": "piece",
    }
    return aliases.get(unit, unit)


def _compare_unit(po_unit: str, quote_unit: str) -> str:
    po_normalized = _normalize_unit(po_unit)
    quote_normalized = _normalize_unit(quote_unit)
    if not po_normalized or not quote_normalized:
        return "unknown"
    return "exact" if po_normalized == quote_normalized else "conflict"


def _expected_line_total(po_line: MailboxPOLine, quote_line: EligibleQuoteLine) -> Decimal | None:
    if po_line.quantity is not None and quote_line.unit_price is not None:
        return po_line.quantity * quote_line.unit_price
    if (
        po_line.quantity is not None
        and quote_line.quantity not in {None, Decimal("0")}
        and quote_line.line_total is not None
    ):
        return po_line.quantity * quote_line.line_total / quote_line.quantity
    if _compare_quantity(po_line.quantity, quote_line.quantity) == "exact":
        return quote_line.line_total
    return None


def _edge(
    po_index: int,
    quote_index: int,
    po_line: MailboxPOLine,
    quote_line: EligibleQuoteLine,
    po_identity: _TextIdentity,
    quote_identity: _TextIdentity,
    *,
    similarity: float | None = None,
) -> _Edge | None:
    if similarity is None:
        similarity = _identity_similarity(po_identity, quote_identity)
    if similarity < 0.56 or _spec_conflict(po_identity, quote_identity):
        return None
    quantity_result = _compare_quantity(po_line.quantity, quote_line.quantity)
    price_result = _compare_money(po_line.unit_price, quote_line.unit_price)
    total_result = _compare_money(po_line.line_total, _expected_line_total(po_line, quote_line))
    unit_result = _compare_unit(po_line.unit, quote_line.unit)
    priority = similarity * 100
    priority += {"exact": 18, "reduced": 4, "conflict": -18}.get(quantity_result, 0)
    priority += {"exact": 12, "conflict": -15}.get(price_result, 0)
    priority += {"exact": 6, "conflict": -8}.get(total_result, 0)
    priority += {"exact": 3, "conflict": -5}.get(unit_result, 0)
    return _Edge(
        po_index=po_index,
        quote_index=quote_index,
        priority=priority,
        similarity=similarity,
        quantity_result=quantity_result,
        price_result=price_result,
        total_result=total_result,
        unit_result=unit_result,
    )


def _assign_lines(
    po_lines: tuple[MailboxPOLine, ...], quote_lines: tuple[EligibleQuoteLine, ...]
) -> tuple[tuple[_Edge, ...], int]:
    po_identities = tuple(_line_identity(line.name, line.description) for line in po_lines)
    quote_identities = tuple(_line_identity(line.name, line.description) for line in quote_lines)
    edges = []
    conflict_rows = set()
    for po_index, (po_line, po_identity) in enumerate(zip(po_lines, po_identities)):
        for quote_index, (quote_line, quote_identity) in enumerate(zip(quote_lines, quote_identities)):
            similarity = _identity_similarity(po_identity, quote_identity)
            if similarity >= 0.56 and _spec_conflict(po_identity, quote_identity):
                conflict_rows.add(po_index)
                continue
            candidate = _edge(
                po_index,
                quote_index,
                po_line,
                quote_line,
                po_identity,
                quote_identity,
                similarity=similarity,
            )
            if candidate:
                edges.append(candidate)
    edges.sort(
        key=lambda item: (
            item.priority,
            item.similarity,
            -item.po_index,
            -item.quote_index,
        ),
        reverse=True,
    )
    assigned_po = set()
    assigned_quote = set()
    selected = []
    for candidate in edges:
        if candidate.po_index in assigned_po or candidate.quote_index in assigned_quote:
            continue
        assigned_po.add(candidate.po_index)
        assigned_quote.add(candidate.quote_index)
        selected.append(candidate)
    selected.sort(key=lambda item: item.po_index)
    unmatched_spec_conflicts = len(conflict_rows - assigned_po)
    return tuple(selected), unmatched_spec_conflicts


def _reference_key(value: str) -> str:
    value = re.sub(r"\.(?:pdf|xlsx?|xlsb)$", "", str(value or "").strip(), flags=re.IGNORECASE)
    return re.sub(r"[^A-Z0-9]+", "", value.upper())


def _quotation_reference_keys(message: CanonicalMailboxMessage) -> frozenset[str]:
    if message.quotation_references_are_review_only:
        return frozenset()
    values = list(message.quotation_references)
    if not message.quotation_references_are_authoritative:
        haystack = f"{message.subject}\n{message.body}"
        values.extend(match.group(0) for match in _AUTO_QUOTE_REFERENCE_RE.finditer(haystack))
        values.extend(match.group(1) for match in _LABELLED_QUOTE_REFERENCE_RE.finditer(haystack))
    return frozenset(key for key in (_reference_key(value) for value in values) if key)


def _addresses(value: str | Sequence[str]) -> frozenset[str]:
    values = [value] if isinstance(value, str) else [str(item) for item in value]
    return frozenset(
        address.lower()
        for _name, address in getaddresses(values)
        if address and "@" in address
    )


def _domain(address: str) -> str:
    return address.rsplit("@", 1)[-1].lower() if "@" in address else ""


def _private_domain(domain: str) -> bool:
    return bool(
        domain
        and not any(domain == public or domain.endswith(f".{public}") for public in _PUBLIC_EMAIL_DOMAINS)
    )


def _company_strength(company_name: str, message: CanonicalMailboxMessage) -> tuple[float, str]:
    company_tokens = [
        token
        for token in _normalize_tokens(company_name)
        if len(token) >= 3 and token not in _COMPANY_NOISE
    ]
    if not company_tokens:
        return 0.0, ""
    haystack = _normalize_text(
        f"{message.company_name} {message.sender} {message.subject} {message.body[:5000]}"
    )
    normalized_company = " ".join(company_tokens)
    if normalized_company and normalized_company in haystack:
        return 6.0, "customer company name appears in the message"
    haystack_tokens = set(haystack.split())
    overlap = len(set(company_tokens) & haystack_tokens) / len(set(company_tokens))
    if overlap >= 0.6:
        return 4.0, "customer company tokens appear in the message"
    return 0.0, ""


def _customer_component(
    message: CanonicalMailboxMessage, quote: EligibleQuotation
) -> tuple[ScoreComponent, bool]:
    senders = _addresses(message.sender)
    expected = _addresses(quote.customer_emails)
    exact_sender = bool(senders & expected)
    sender_domains = {_domain(address) for address in senders}
    expected_domains = {
        domain for domain in (_domain(address) for address in expected) if _private_domain(domain)
    }
    domain_matches = sender_domains & expected_domains
    company_score, company_reason = _company_strength(quote.company_name, message)
    score = 0.0
    details = []
    if exact_sender:
        score += 18.0
        details.append("exact customer sender")
    elif domain_matches:
        score += 10.0
        details.append(f"customer domain {sorted(domain_matches)[0]}")
    if company_score:
        score += company_score
        details.append(company_reason)
    score = min(score, 20.0)
    return (
        ScoreComponent(
            "customer_identity",
            score,
            "; ".join(details) or "no customer identity signal",
        ),
        exact_sender,
    )


def _quote_boundary(quote: EligibleQuotation) -> datetime | None:
    # A sent timestamp is the actual customer-visible boundary.  Finalisation
    # is the fallback for quotes that were shared outside the email workflow.
    return quote.sent_at or quote.finalized_at or quote.created_at


def _time_component(boundary: datetime, received_at: datetime) -> ScoreComponent:
    days = max(0.0, (received_at - boundary).total_seconds() / 86_400)
    if days <= 3:
        score = 8.0
    elif days <= 14:
        score = 6.0
    elif days <= 45:
        score = 4.0
    elif days <= 120:
        score = 2.0
    elif days <= 365:
        score = 0.0
    else:
        score = -5.0
    return ScoreComponent("time_distance", score, f"PO arrived {days:.1f} days after the quote boundary")


def _count_result(edges: Iterable[_Edge], field_name: str, value: str) -> int:
    return sum(1 for edge in edges if getattr(edge, field_name) == value)


def _comparison_component(
    signal: str,
    exact: int,
    conflict: int,
    comparable: int,
    *,
    exact_weight: float,
    conflict_weight: float,
    extra_positive: int = 0,
    extra_weight: float = 0.0,
) -> ScoreComponent:
    if not comparable:
        return ScoreComponent(signal, 0.0, f"no comparable {signal.replace('_', ' ')} values")
    score = (exact * exact_weight + extra_positive * extra_weight - conflict * conflict_weight) / comparable
    detail = f"{exact} exact, {conflict} conflicting"
    if extra_positive:
        detail += f", {extra_positive} safely reduced"
    return ScoreComponent(signal, score, detail)


def _document_total_component(
    message: CanonicalMailboxMessage,
    quote: EligibleQuotation,
    selected: tuple[_Edge, ...],
) -> tuple[ScoreComponent | None, str]:
    if message.document_total is None or not message.parsed_rows or len(selected) != len(message.parsed_rows):
        return None, "unknown"
    expected_values = []
    for edge in selected:
        expected = _expected_line_total(
            message.parsed_rows[edge.po_index], quote.lines[edge.quote_index]
        )
        if expected is None:
            expected_values = []
            break
        expected_values.append(expected)
    expected_total = sum(expected_values, Decimal("0")) if expected_values else None
    if expected_total is None and len(selected) == len(quote.lines):
        expected_total = quote.grand_total
    result = _compare_money(message.document_total, expected_total)
    score = {"exact": 5.0, "conflict": -10.0}.get(result, 0.0)
    if result == "unknown":
        detail = "document total could not be compared"
    else:
        detail = f"document total is {result} against the matched quote subtotal"
    return ScoreComponent("document_total", score, detail), result


def _evaluate(
    message: CanonicalMailboxMessage,
    quote: EligibleQuotation,
    reference_keys: frozenset[str],
) -> _Evaluation:
    quote_key = _reference_key(quote.quotation_number)
    if not quote_key:
        return _Evaluation(rejection="quotation has no stable reference")
    exact_reference = bool(reference_keys and reference_keys == {quote_key})
    if reference_keys and not exact_reference:
        return _Evaluation(rejection="explicit quotation reference points elsewhere or is mixed")

    boundary = _quote_boundary(quote)
    if message.received_at is None:
        return _Evaluation(rejection="message receipt timestamp is missing")
    if boundary is None:
        return _Evaluation(rejection="quotation send/finalize timestamp is missing")
    boundary = _datetime(boundary)
    if boundary is None or message.received_at <= boundary:
        return _Evaluation(rejection="message is not after the quotation send/finalize timestamp")

    components = []
    if exact_reference:
        components.append(ScoreComponent("quotation_reference", 45.0, "exact sole quotation reference"))
    customer_component, exact_sender = _customer_component(message, quote)
    components.append(customer_component)
    components.append(_time_component(boundary, message.received_at))
    components.append(
        ScoreComponent(
            "order_document",
            2.0,
            "structured PO rows or an LPO/order reference is present",
        )
    )

    selected, spec_conflicts = _assign_lines(message.parsed_rows, quote.lines)
    matched_count = len(selected)
    po_count = len(message.parsed_rows)
    quote_count = len(quote.lines)
    item_coverage = matched_count / po_count if po_count else 0.0
    quote_coverage = matched_count / quote_count if quote_count else 0.0
    average_similarity = (
        sum(edge.similarity for edge in selected) / matched_count if matched_count else 0.0
    )

    if po_count and not matched_count and not exact_reference:
        return _Evaluation(rejection="no parsed PO item overlaps this quotation")
    if not po_count and not exact_reference and not message.lpo_references:
        return _Evaluation(rejection="no structured PO rows or LPO reference to compare")

    item_score = item_coverage * 28.0 + average_similarity * 8.0
    if po_count and not matched_count:
        item_score = -25.0
    components.append(
        ScoreComponent(
            "item_overlap",
            item_score,
            f"matched {matched_count}/{po_count} PO rows; quote coverage {quote_coverage:.0%}",
        )
    )
    if spec_conflicts:
        spec_penalty = -18.0 * (spec_conflicts / max(1, po_count))
        components.append(
            ScoreComponent(
                "spec_conflicts",
                spec_penalty,
                f"{spec_conflicts} unmatched PO row(s) conflict on size/strength/pack specification",
            )
        )

    quantity_exact = _count_result(selected, "quantity_result", "exact")
    quantity_reduced = _count_result(selected, "quantity_result", "reduced")
    quantity_conflicts = _count_result(selected, "quantity_result", "conflict")
    quantity_comparable = quantity_exact + quantity_reduced + quantity_conflicts
    components.append(
        _comparison_component(
            "quantities",
            quantity_exact,
            quantity_conflicts,
            quantity_comparable,
            exact_weight=8.0,
            conflict_weight=12.0,
            extra_positive=quantity_reduced,
            extra_weight=2.0,
        )
    )

    price_exact = _count_result(selected, "price_result", "exact")
    price_conflicts = _count_result(selected, "price_result", "conflict")
    price_comparable = sum(1 for edge in selected if edge.price_result != "unknown")
    components.append(
        _comparison_component(
            "unit_prices",
            price_exact,
            price_conflicts,
            price_comparable,
            exact_weight=6.0,
            conflict_weight=9.0,
        )
    )

    total_exact = _count_result(selected, "total_result", "exact")
    total_conflicts = _count_result(selected, "total_result", "conflict")
    total_comparable = sum(1 for edge in selected if edge.total_result != "unknown")
    if total_comparable:
        components.append(
            _comparison_component(
                "line_totals",
                total_exact,
                total_conflicts,
                total_comparable,
                exact_weight=5.0,
                conflict_weight=8.0,
            )
        )

    unit_conflicts = _count_result(selected, "unit_result", "conflict")
    if unit_conflicts:
        components.append(
            ScoreComponent(
                "units",
                -4.0 * (unit_conflicts / max(1, matched_count)),
                f"{unit_conflicts} matched row(s) have conflicting units",
            )
        )

    document_component, document_total_result = _document_total_component(message, quote, selected)
    if document_component:
        components.append(document_component)

    # Count rows, not individual values: a row with both an exact unit price
    # and exact line total is still one corroborated PO row.  This prevents one
    # well-populated row from making a mostly price-less document decisive.
    commercial_exact_rows = sum(
        1
        for edge in selected
        if edge.price_result == "exact" or edge.total_result == "exact"
    )
    commercial_row_coverage = (
        commercial_exact_rows / matched_count if matched_count else 0.0
    )
    if document_total_result == "conflict":
        commercial_corroboration_result = "document_total_conflict"
    elif document_total_result == "exact":
        commercial_corroboration_result = "document_total_exact"
    elif commercial_row_coverage >= 0.8:
        commercial_corroboration_result = "row_coverage_exact"
    else:
        commercial_corroboration_result = "insufficient"

    identity_score = customer_component.score
    if not exact_reference and identity_score <= 0 and (item_coverage < 0.8 or matched_count < 2):
        return _Evaluation(rejection="no quotation reference or customer identity signal")

    raw_score = sum(component.score for component in components)
    score = round(max(0.0, min(100.0, raw_score)), 3)
    if score < 20.0:
        return _Evaluation(rejection="candidate score is below the review threshold")

    matches = tuple(
        MatchedLine(
            po_line_id=message.parsed_rows[edge.po_index].line_id,
            quote_line_id=quote.lines[edge.quote_index].line_id,
            po_name=message.parsed_rows[edge.po_index].name,
            quote_name=quote.lines[edge.quote_index].name,
            name_similarity=edge.similarity,
            quantity_result=edge.quantity_result,
            price_result=edge.price_result,
            total_result=edge.total_result,
            unit_result=edge.unit_result,
        )
        for edge in selected
    )
    return _Evaluation(
        candidate=RankedQuotationCandidate(
            quote_id=quote.quote_id,
            quotation_number=quote.quotation_number,
            score=score,
            components=tuple(components),
            matched_lines=matches,
            po_line_count=po_count,
            quote_line_count=quote_count,
            item_coverage=item_coverage,
            quote_coverage=quote_coverage,
            average_name_similarity=average_similarity,
            exact_quote_reference=exact_reference,
            exact_sender=exact_sender,
            quantity_exact_count=quantity_exact,
            quantity_reduced_count=quantity_reduced,
            quantity_conflict_count=quantity_conflicts,
            price_exact_count=price_exact,
            price_conflict_count=price_conflicts,
            total_exact_count=total_exact,
            total_conflict_count=total_conflicts,
            spec_conflict_count=spec_conflicts,
            unit_conflict_count=unit_conflicts,
            document_total_result=document_total_result,
            document_total_provided=message.document_total is not None,
            commercial_exact_row_count=commercial_exact_rows,
            commercial_row_coverage=commercial_row_coverage,
            commercial_corroboration_result=commercial_corroboration_result,
            parser_warnings=tuple(message.parser_warnings),
            material_warnings=tuple(message.material_warnings),
        )
    )


def _has_order_signal(message: CanonicalMailboxMessage) -> bool:
    # Structured rows alone are not an order signal: inquiries, quotations and
    # invoices also contain item/quantity/price tables. Require an explicit
    # LPO/PO reference or order/acceptance wording before any quote is ranked.
    if message.lpo_references:
        return True
    return bool(_ORDER_SIGNAL_RE.search(f"{message.subject}\n{message.body}"))


def _automatic_blockers(
    candidate: RankedQuotationCandidate,
    margin: float,
    *,
    threshold: float,
    required_margin: float,
) -> tuple[str, ...]:
    blockers = []
    comparable_quantities = (
        candidate.quantity_exact_count
        + candidate.quantity_reduced_count
        + candidate.quantity_conflict_count
    )
    commercially_corroborated = bool(
        candidate.document_total_result == "exact"
        or candidate.commercial_row_coverage >= 0.8
    )
    sufficiently_specific = bool(
        candidate.exact_quote_reference
        or candidate.quote_coverage >= 0.2
        or len(candidate.matched_lines) >= 2
        or (candidate.exact_sender and commercially_corroborated)
    )
    has_identity = candidate.exact_quote_reference or candidate.exact_sender or any(
        component.signal == "customer_identity" and component.score >= 6.0
        for component in candidate.components
    )

    if candidate.parser_warnings:
        blockers.append(
            f"parser reported {len(candidate.parser_warnings)} warning(s)"
        )
    if candidate.material_warnings:
        blockers.append(
            f"document reported {len(candidate.material_warnings)} material warning(s)"
        )
    if candidate.score < threshold:
        blockers.append(
            f"score {candidate.score:.1f} is below the {threshold:.1f} automatic threshold"
        )
    if margin < required_margin:
        blockers.append(
            f"candidate margin {margin:.1f} is below the required {required_margin:.1f}"
        )
    if candidate.po_line_count <= 0:
        blockers.append("no parsed PO item rows are available")
    if candidate.item_coverage < 0.8:
        blockers.append(
            f"PO item coverage is only {candidate.item_coverage:.0%}; at least 80% is required"
        )
    if candidate.average_name_similarity < 0.72:
        blockers.append("matched item names are not similar enough")
    if candidate.spec_conflict_count:
        blockers.append(f"{candidate.spec_conflict_count} item specification conflict(s)")
    if candidate.quantity_conflict_count:
        blockers.append(f"{candidate.quantity_conflict_count} quantity conflict(s)")
    if candidate.price_conflict_count:
        blockers.append(f"{candidate.price_conflict_count} unit-price conflict(s)")
    if candidate.total_conflict_count:
        blockers.append(f"{candidate.total_conflict_count} line-total conflict(s)")
    if candidate.unit_conflict_count:
        blockers.append(f"{candidate.unit_conflict_count} unit conflict(s)")
    if candidate.document_total_result == "conflict":
        blockers.append("the provided document total conflicts with the matched quote subtotal")
    elif candidate.document_total_provided and candidate.document_total_result == "unknown":
        blockers.append("the provided document total could not be verified")
    if not has_identity:
        blockers.append("no exact quotation or trustworthy customer identity signal")
    if not sufficiently_specific:
        blockers.append("the evidence is not specific enough to one quotation")

    matched_count = len(candidate.matched_lines)
    if comparable_quantities != matched_count or (
        candidate.quantity_exact_count + candidate.quantity_reduced_count != matched_count
    ):
        blockers.append("every matched PO row needs an exact or safely reduced quantity")
    if not commercially_corroborated:
        blockers.append(
            "commercial corroboration is insufficient: require an exact document total or "
            f"exact unit price/line total on at least 80% of matched rows "
            f"({candidate.commercial_exact_row_count}/{matched_count})"
        )
    return tuple(blockers)


def _is_automatic(
    candidate: RankedQuotationCandidate,
    margin: float,
    *,
    threshold: float,
    required_margin: float,
) -> bool:
    return not _automatic_blockers(
        candidate,
        margin,
        threshold=threshold,
        required_margin=required_margin,
    )


def rank_message_to_quotations(
    message: CanonicalMailboxMessage | Mapping[str, Any],
    eligible_quotes: Iterable[EligibleQuotation | Mapping[str, Any]],
    *,
    max_candidates: int = MAX_RETURNED_CANDIDATES,
    automatic_threshold: float = DEFAULT_AUTOMATIC_THRESHOLD,
    automatic_margin: float = DEFAULT_AUTOMATIC_MARGIN,
) -> MailboxMatchResult:
    """Rank one inbound message against eligible quotations without side effects.

    ``max_candidates`` is deliberately capped at three even if a caller asks
    for more.  Rejected candidates are summarized by reason instead of being
    returned individually, preventing the UI from recreating the historical
    candidate explosion.
    """

    canonical_message = canonicalize_message(message)
    quotes = tuple(canonicalize_quotation(quote) for quote in eligible_quotes)
    if not _has_order_signal(canonical_message):
        return MailboxMatchResult(
            status=UNMATCHED,
            evaluated_count=len(quotes),
            rejected_count=len(quotes),
            rejection_summary=(("message has no PO/LPO/order evidence", len(quotes)),) if quotes else (),
            reason="The message has no explicit LPO/PO reference or order-confirmation wording.",
        )
    if canonical_message.received_at is None:
        return MailboxMatchResult(
            status=UNMATCHED,
            evaluated_count=len(quotes),
            rejected_count=len(quotes),
            rejection_summary=(("message receipt timestamp is missing", len(quotes)),) if quotes else (),
            reason="A trustworthy message timestamp is required before comparing quotations.",
        )

    reference_keys = _quotation_reference_keys(canonical_message)
    candidates = []
    rejections: Counter[str] = Counter()
    for quote in quotes:
        evaluation = _evaluate(canonical_message, quote, reference_keys)
        if evaluation.candidate:
            candidates.append(evaluation.candidate)
        else:
            rejections[evaluation.rejection or "candidate rejected"] += 1

    candidates.sort(
        key=lambda candidate: (
            candidate.score,
            candidate.item_coverage,
            candidate.quantity_exact_count,
            candidate.price_exact_count,
            str(candidate.quotation_number),
        ),
        reverse=True,
    )
    if not candidates:
        if reference_keys:
            reason = "No eligible quotation safely matches the explicit quotation reference and message time."
        else:
            reason = "No eligible quotation has enough item and customer overlap for review."
        return MailboxMatchResult(
            status=UNMATCHED,
            evaluated_count=len(quotes),
            rejected_count=sum(rejections.values()),
            rejection_summary=tuple(sorted(rejections.items(), key=lambda item: (-item[1], item[0]))),
            reason=reason,
        )

    top = candidates[0]
    runner_up_score = candidates[1].score if len(candidates) > 1 else 0.0
    margin = round(max(0.0, top.score - runner_up_score), 3)
    returned_limit = max(1, min(MAX_RETURNED_CANDIDATES, int(max_candidates or 1)))
    returned = tuple(candidates[:returned_limit])
    blockers = _automatic_blockers(
        top,
        margin,
        threshold=float(automatic_threshold),
        required_margin=float(automatic_margin),
    )
    automatic = not blockers
    if automatic:
        status = AUTOMATIC
        if top.document_total_result == "exact":
            commercial_reason = "an exact document total"
        else:
            commercial_reason = (
                f"exact prices/totals on {top.commercial_exact_row_count}/"
                f"{len(top.matched_lines)} matched rows"
            )
        reason = (
            f"{top.quotation_number} is decisive: score {top.score:.1f}, "
            f"margin {margin:.1f}, {top.item_coverage:.0%} PO-item coverage, and "
            f"{commercial_reason}."
        )
        winner = top
    else:
        status = AMBIGUOUS
        winner = None
        reason = (
            f"Staff review is required: best score {top.score:.1f}, margin {margin:.1f}, "
            f"and {top.item_coverage:.0%} PO-item coverage. Blocking checks: "
            + "; ".join(blockers)
            + "."
        )
    return MailboxMatchResult(
        status=status,
        candidates=returned,
        automatic_winner=winner,
        ambiguity_margin=margin,
        evaluated_count=len(quotes),
        rejected_count=sum(rejections.values()),
        rejection_summary=tuple(sorted(rejections.items(), key=lambda item: (-item[1], item[0]))),
        reason=reason,
        automatic_blockers=blockers,
    )


# Short integration-friendly alias.
rank_mailbox_po_candidates = rank_message_to_quotations


__all__ = [
    "AMBIGUOUS",
    "AUTOMATIC",
    "UNMATCHED",
    "CanonicalMailboxMessage",
    "EligibleQuotation",
    "EligibleQuoteLine",
    "MailboxMatchResult",
    "MailboxPOLine",
    "MatchedLine",
    "RankedQuotationCandidate",
    "ScoreComponent",
    "canonicalize_message",
    "canonicalize_po_line",
    "canonicalize_quotation",
    "canonicalize_quote_line",
    "rank_mailbox_po_candidates",
    "rank_message_to_quotations",
]
