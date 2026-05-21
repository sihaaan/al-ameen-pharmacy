import re
from decimal import Decimal, InvalidOperation

from .models import InquiryLine


UNIT_PATTERN = (
    r"boxes?|packs?|pcs|pieces?|units?|bottles?|strips?|cartons?|vials?|"
    r"ampoules?|tubes?|pairs?|dozens?|sets?|rolls?|bags?|cases?|sachets?"
)

NOISE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(page|p\.)\s*\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(quotation|quote|inquiry|lpo|local purchase order)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(item|description|product|medicine)\s+(qty|quantity|unit|uom)", re.IGNORECASE),
    re.compile(r"^\s*(subtotal|total|vat|amount)\b", re.IGNORECASE),
]


def normalize_import_line(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def is_noise_line(value):
    normalized = normalize_import_line(value)
    return any(pattern.search(normalized) for pattern in NOISE_PATTERNS)


def clean_item_name(value):
    value = normalize_import_line(value)
    value = re.sub(r"^\s*(?:\d+[\).\-/]|[-*])\s*", "", value)
    value = value.strip(" -:\t")
    return value[:255]


def parse_decimal(value):
    if value in (None, ""):
        return None
    match = re.search(r"\d+(?:[.,]\d+)?", str(value))
    if not match:
        return None
    try:
        return Decimal(match.group(0).replace(",", "."))
    except InvalidOperation:
        return None


def decimal_to_preview(value):
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def make_preview_line(
    *,
    raw_line,
    raw_name=None,
    quantity=None,
    unit="",
    parse_status=None,
    parse_confidence=0.3,
    notes="",
    **source_meta,
):
    quantity = parse_decimal(quantity)
    raw_name = clean_item_name(raw_name or raw_line)
    if not raw_name:
        raw_name = normalize_import_line(raw_line)[:255]
    return {
        "raw_line": normalize_import_line(raw_line),
        "raw_name": raw_name,
        "quantity": decimal_to_preview(quantity),
        "unit": normalize_import_line(unit)[:50],
        "notes": notes,
        "match_status": "unresolved",
        "matched_quote_item": None,
        "parse_status": parse_status or (InquiryLine.PARSE_PARSED if quantity and raw_name else InquiryLine.PARSE_NEEDS_REVIEW),
        "parse_confidence": round(float(parse_confidence), 2),
        **{key: value for key, value in source_meta.items() if value not in (None, "")},
    }


def parse_inquiry_line(raw_line, **source_meta):
    raw_line = normalize_import_line(raw_line)
    if not raw_line:
        return None
    stripped = re.sub(r"^\s*(?:\d+[\).\-/]|[-*])\s*", "", raw_line)

    patterns = [
        (
            re.compile(
                rf"^(?P<name>.+?)\s*(?:-|–|—|:)\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})?\s*$",
                re.IGNORECASE,
            ),
            0.9,
        ),
        (
            re.compile(
                rf"^(?P<name>.+?)\s+x\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})?\s*$",
                re.IGNORECASE,
            ),
            0.85,
        ),
        (
            re.compile(
                rf"^(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\s+(?P<name>.+?)\s*$",
                re.IGNORECASE,
            ),
            0.9,
        ),
        (
            re.compile(
                rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\s*$",
                re.IGNORECASE,
            ),
            0.85,
        ),
    ]
    for pattern, confidence in patterns:
        match = pattern.match(stripped)
        if match:
            parts = match.groupdict()
            return make_preview_line(
                raw_line=raw_line,
                raw_name=parts.get("name"),
                quantity=parts.get("qty"),
                unit=parts.get("unit") or "",
                parse_status=InquiryLine.PARSE_PARSED,
                parse_confidence=confidence,
                **source_meta,
            )

    return make_preview_line(
        raw_line=raw_line,
        raw_name=stripped,
        parse_status=InquiryLine.PARSE_NEEDS_REVIEW,
        parse_confidence=0.35,
        **source_meta,
    )


def parse_text_lines(raw_text, **source_meta):
    lines = []
    skipped = 0
    for index, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        normalized = normalize_import_line(raw_line)
        if not normalized:
            continue
        if is_noise_line(normalized):
            skipped += 1
            continue
        parsed = parse_inquiry_line(normalized, source_line=index, **source_meta)
        if parsed:
            lines.append(parsed)
    return lines, skipped
