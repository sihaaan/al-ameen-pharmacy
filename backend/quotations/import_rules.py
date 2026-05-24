import re
import string
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .models import InquiryLine


UNIT_WORDS = [
    "box",
    "boxes",
    "pack",
    "packs",
    "pkt",
    "pkts",
    "pc",
    "pcs",
    "piece",
    "pieces",
    "unit",
    "units",
    "bottle",
    "bottles",
    "strip",
    "strips",
    "carton",
    "cartons",
    "vial",
    "vials",
    "ampoule",
    "ampoules",
    "ampule",
    "ampules",
    "tube",
    "tubes",
    "pair",
    "pairs",
    "dozen",
    "dozens",
    "set",
    "sets",
    "roll",
    "rolls",
    "bag",
    "bags",
    "case",
    "cases",
    "sachet",
    "sachets",
    "no",
    "nos",
    "number",
    "numbers",
]
UNIT_PATTERN = r"(?:{})".format("|".join(re.escape(unit) for unit in sorted(UNIT_WORDS, key=len, reverse=True)))

HEADER_ALIASES = {
    "serial_no": {
        "#",
        "s no",
        "s/no",
        "sl no",
        "sr no",
        "serial",
        "serial no",
    },
    "requested_item_name": {
        "description",
        "item",
        "item description",
        "item name",
        "items",
        "material",
        "medicine",
        "particulars",
        "product",
        "product name",
        "requested item",
    },
    "quantity": {
        "qnty",
        "qty",
        "quantity",
        "requested qty",
        "requested quantity",
        "required qty",
    },
    "unit": {
        "pack",
        "packing",
        "unit",
        "uom",
    },
}

HEADER_ROLE_LABELS = {
    "serial_no": "Serial",
    "requested_item_name": "Item",
    "quantity": "Quantity",
    "unit": "Unit",
}

NOISE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(page|p\.)\s*\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(quotation|quote|inquiry|lpo|local purchase order)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(subtotal|total|vat|amount|grand total)\b", re.IGNORECASE),
    re.compile(r"^\s*(prepared by|approved by|signature|stamp)\b", re.IGNORECASE),
]

SERIAL_PREFIX_RE = re.compile(r"^\s*(?P<serial>\d{1,5})(?:\s*[\).\-/|:]\s*|\s+)(?P<rest>.+)$")
PUNCT_NOISE_TRANS = str.maketrans({char: " " for char in string.punctuation if char not in {"/", "#"}})


@dataclass(frozen=True)
class HeaderDetection:
    row_offset: int
    row_number: int
    columns: dict
    score: float
    labels: dict
    data_score: float


def normalize_import_line(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalize_header(value):
    value = normalize_import_line(value).lower()
    value = value.replace("\\", "/")
    value = re.sub(r"[:|]+", " ", value)
    value = value.translate(PUNCT_NOISE_TRANS)
    return re.sub(r"\s+", " ", value).strip()


def _cell_text(value):
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return normalize_import_line(value)


def _meaningful_cells(row):
    return [_cell_text(value) for value in row if _cell_text(value)]


def row_to_text(row):
    return " | ".join(_meaningful_cells(row))


def is_noise_line(value):
    normalized = normalize_import_line(value)
    return any(pattern.search(normalized) for pattern in NOISE_PATTERNS)


def _looks_like_request_title(value):
    normalized = normalize_import_line(value)
    lowered = normalized.lower()
    if len(normalized) < 6:
        return False
    if "request" in lowered and ("item" in lowered or "material" in lowered or "medicine" in lowered):
        return True
    letters = [char for char in normalized if char.isalpha()]
    if len(letters) >= 8 and normalized.upper() == normalized and not re.search(r"\d", normalized):
        return True
    return False


def is_title_row(row):
    cells = _meaningful_cells(row)
    if len(cells) != 1:
        return False
    cell = cells[0]
    if classify_header_cell(cell):
        return False
    return _looks_like_request_title(cell)


def classify_header_cell(value):
    normalized = normalize_header(value)
    if not normalized:
        return None
    for role, aliases in HEADER_ALIASES.items():
        if normalized in aliases:
            return role
    return None


def _row_roles(row):
    roles = {}
    labels = {}
    for index, value in enumerate(row):
        role = classify_header_cell(value)
        if role and role not in roles:
            roles[role] = index
            labels[role] = _cell_text(value)
    return roles, labels


def is_header_like_row(row):
    roles, _ = _row_roles(row)
    return "requested_item_name" in roles and bool({"quantity", "unit", "serial_no"} & set(roles))


def parse_decimal(value):
    if value in (None, ""):
        return None
    if isinstance(value, Decimal):
        return value
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
    value = parse_decimal(value)
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def split_quantity_unit(quantity_value, unit_value=""):
    quantity_text = _cell_text(quantity_value)
    unit_text = _cell_text(unit_value)[:50]
    quantity = parse_decimal(quantity_text)

    if quantity is not None and unit_text:
        return quantity, unit_text

    patterns = [
        re.compile(rf"^\s*(?:qty|quantity)?\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\b", re.IGNORECASE),
        re.compile(rf"\b(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\b", re.IGNORECASE),
    ]
    for pattern in patterns:
        match = pattern.search(quantity_text)
        if match:
            return parse_decimal(match.group("qty")), normalize_import_line(match.group("unit"))[:50]

    unit_match = re.search(rf"\b(?P<unit>{UNIT_PATTERN})\b", unit_text, re.IGNORECASE)
    if quantity is not None and unit_match:
        return quantity, normalize_import_line(unit_match.group("unit"))[:50]

    return quantity, unit_text


def strip_serial_prefix(value):
    text = normalize_import_line(value)
    match = SERIAL_PREFIX_RE.match(text)
    if not match:
        return text, "", False
    rest = normalize_import_line(match.group("rest"))
    if not rest:
        return text, "", False
    return rest.strip(" -:|"), match.group("serial"), True


def clean_item_name(value):
    value, _, _ = strip_serial_prefix(value)
    value = value.replace("|", " ")
    value = re.sub(r"^\s*[-*•]\s*", "", value)
    value = re.sub(r"\s+", " ", value)
    return value.strip(" -:\t|")[:255]


def _data_row_score(row, columns):
    if is_title_row(row) or is_header_like_row(row):
        return 0
    item_index = columns.get("requested_item_name")
    quantity_index = columns.get("quantity")
    unit_index = columns.get("unit")
    if item_index is None or item_index >= len(row):
        return 0
    item = clean_item_name(_cell_text(row[item_index]))
    if not item or len(item) < 2:
        return 0
    score = 1
    quantity_value = _cell_text(row[quantity_index]) if quantity_index is not None and quantity_index < len(row) else ""
    unit_value = _cell_text(row[unit_index]) if unit_index is not None and unit_index < len(row) else ""
    quantity, unit = split_quantity_unit(quantity_value, unit_value)
    if quantity is not None:
        score += 1
    if unit:
        score += 0.5
    return score


def detect_header_row(rows, *, start_row_number=1, max_scan_rows=20):
    best = None
    for offset, row in enumerate(rows[:max_scan_rows]):
        if is_title_row(row):
            continue
        roles, labels = _row_roles(row)
        if "requested_item_name" not in roles:
            continue
        if not bool({"quantity", "unit", "serial_no"} & set(roles)):
            continue

        base_score = 0
        if "serial_no" in roles:
            base_score += 1
        if "requested_item_name" in roles:
            base_score += 4
        if "quantity" in roles:
            base_score += 3
        if "unit" in roles:
            base_score += 2

        lookahead = rows[offset + 1 : offset + 6]
        data_scores = [_data_row_score(candidate, roles) for candidate in lookahead]
        data_score = sum(1 for score in data_scores if score >= 1)
        strong_data_score = sum(data_scores)
        if data_score == 0:
            base_score -= 2
        else:
            base_score += min(strong_data_score, 5)

        detection = HeaderDetection(
            row_offset=offset,
            row_number=start_row_number + offset,
            columns=roles,
            score=round(float(base_score), 2),
            labels=labels,
            data_score=round(float(strong_data_score), 2),
        )
        if best is None or detection.score > best.score:
            best = detection

    return best


def confidence_status(confidence):
    if confidence >= 0.80:
        return InquiryLine.PARSE_PARSED
    if confidence >= 0.50:
        return InquiryLine.PARSE_NEEDS_REVIEW
    return InquiryLine.PARSE_UNPARSED


def make_preview_line(
    *,
    raw_line,
    raw_name=None,
    quantity=None,
    unit="",
    parse_status=None,
    parse_confidence=0.3,
    notes="",
    raw_source_line=None,
    **source_meta,
):
    quantity = parse_decimal(quantity)
    raw_line = normalize_import_line(raw_line)
    raw_name = clean_item_name(raw_name or raw_line)
    if not raw_name:
        raw_name = raw_line[:255]
    confidence = max(0, min(1, round(float(parse_confidence), 2)))
    payload = {
        "raw_line": raw_line,
        "raw_source_line": normalize_import_line(raw_source_line or raw_line),
        "raw_name": raw_name,
        "requested_item_name": raw_name,
        "quantity": decimal_to_preview(quantity),
        "unit": normalize_import_line(unit)[:50],
        "notes": notes,
        "match_status": "unresolved",
        "matched_quote_item": None,
        "matched_product": None,
        "matched_product_name": "",
        "match_reason": "",
        "parse_status": parse_status or confidence_status(confidence),
        "parse_confidence": confidence,
    }
    for key, value in source_meta.items():
        if value not in (None, ""):
            payload[key] = value
    return payload


def parse_structured_row(row, header, *, source_sheet="", source_row=None, source_page=None, base_confidence=0.85):
    raw_line = row_to_text(row)
    if not raw_line or is_title_row(row) or is_header_like_row(row):
        return None, "skipped"

    columns = header.columns if header else {}
    item_index = columns.get("requested_item_name")
    if item_index is None or item_index >= len(row):
        return None, "skipped"

    item_value = _cell_text(row[item_index])
    if not item_value:
        return None, "skipped"

    item_name, serial_from_item, stripped_serial = strip_serial_prefix(item_value)
    serial_index = columns.get("serial_no")
    serial_no = _cell_text(row[serial_index]) if serial_index is not None and serial_index < len(row) else serial_from_item

    quantity_index = columns.get("quantity")
    unit_index = columns.get("unit")
    quantity_value = _cell_text(row[quantity_index]) if quantity_index is not None and quantity_index < len(row) else ""
    unit_value = _cell_text(row[unit_index]) if unit_index is not None and unit_index < len(row) else ""
    quantity, unit = split_quantity_unit(quantity_value, unit_value)

    confidence = float(base_confidence)
    if header and header.data_score:
        confidence += 0.05
    if quantity is not None and unit:
        confidence += 0.05
    if stripped_serial or serial_no:
        confidence += 0.03
    if quantity is None:
        confidence -= 0.10
    if not unit:
        confidence -= 0.04
    if item_name != item_value:
        confidence -= 0.02

    confidence = max(0.0, min(0.98, confidence))
    return make_preview_line(
        raw_line=raw_line,
        raw_source_line=raw_line,
        raw_name=item_name,
        quantity=quantity,
        unit=unit,
        parse_status=confidence_status(confidence),
        parse_confidence=confidence,
        source_sheet=source_sheet,
        sheet_name=source_sheet,
        source_row=source_row,
        row_number=source_row,
        source_page=source_page,
        page_number=source_page,
        serial_no=serial_no,
    ), None


def parse_inquiry_line(raw_line, *, base_confidence=0.55, **source_meta):
    raw_line = normalize_import_line(raw_line)
    if not raw_line:
        return None
    if is_noise_line(raw_line):
        return None

    stripped, serial_no, stripped_serial = strip_serial_prefix(raw_line)
    if _looks_like_request_title(stripped):
        return None

    patterns = [
        (
            re.compile(
                rf"^(?P<name>.+?)\s*(?:-|–|—|:)\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})?\s*$",
                re.IGNORECASE,
            ),
            0.82,
        ),
        (
            re.compile(
                rf"^(?P<name>.+?)\s+x\s*(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})?\s*$",
                re.IGNORECASE,
            ),
            0.80,
        ),
        (
            re.compile(
                rf"^(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\s+(?P<name>.+?)\s*$",
                re.IGNORECASE,
            ),
            0.82,
        ),
        (
            re.compile(
                rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s*(?P<unit>{UNIT_PATTERN})\s*$",
                re.IGNORECASE,
            ),
            0.78,
        ),
    ]
    for pattern, pattern_confidence in patterns:
        match = pattern.match(stripped)
        if match:
            parts = match.groupdict()
            quantity, unit = split_quantity_unit(parts.get("qty"), parts.get("unit") or "")
            confidence = max(base_confidence, pattern_confidence)
            if quantity is not None and unit:
                confidence += 0.05
            if stripped_serial:
                confidence += 0.03
            return make_preview_line(
                raw_line=raw_line,
                raw_name=parts.get("name"),
                quantity=quantity,
                unit=unit,
                parse_status=confidence_status(confidence),
                parse_confidence=min(confidence, 0.95),
                serial_no=serial_no,
                **source_meta,
            )

    return make_preview_line(
        raw_line=raw_line,
        raw_name=stripped,
        parse_status=InquiryLine.PARSE_NEEDS_REVIEW,
        parse_confidence=base_confidence - (0.05 if stripped_serial else 0),
        serial_no=serial_no,
        **source_meta,
    )


def parse_text_lines(raw_text, **source_meta):
    lines = []
    skipped = 0
    for index, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        normalized = normalize_import_line(raw_line)
        if not normalized:
            continue
        if is_noise_line(normalized) or _looks_like_request_title(normalized):
            skipped += 1
            continue
        cells = [part.strip() for part in re.split(r"\s*\|\s*", normalized)]
        if len(cells) > 1 and is_header_like_row(cells):
            skipped += 1
            continue
        parsed = parse_inquiry_line(normalized, source_line=index, row_number=index, **source_meta)
        if parsed:
            lines.append(parsed)
        else:
            skipped += 1
    return lines, skipped


def summarize_lines(lines, skipped_count=0):
    parsed_count = sum(1 for line in lines if line.get("parse_status") == InquiryLine.PARSE_PARSED)
    needs_review_count = sum(1 for line in lines if line.get("parse_status") == InquiryLine.PARSE_NEEDS_REVIEW)
    unparsed_count = sum(1 for line in lines if line.get("parse_status") == InquiryLine.PARSE_UNPARSED)
    return {
        "total_lines": len(lines),
        "parsed_count": parsed_count,
        "needs_review_count": needs_review_count,
        "unparsed_count": unparsed_count,
        "skipped_count": skipped_count,
    }
