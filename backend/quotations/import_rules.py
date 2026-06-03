import re
import string
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser

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
    "cartoon",
    "cartoons",
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
        "material description",
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
        "req quantity",
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
    "unit_price": {
        "rate",
        "price",
        "u p",
        "up",
        "u price",
        "u/p",
        "unit price",
    },
    "amount": {
        "amount",
        "net price",
        "subtotal",
        "value",
    },
    "vat_amount": {
        "vat",
        "vat amount",
    },
    "line_total": {
        "g total",
        "grand total",
        "gross total",
        "net total",
        "total",
    },
}

HEADER_ROLE_LABELS = {
    "serial_no": "Serial",
    "requested_item_name": "Item",
    "quantity": "Quantity",
    "unit": "Unit",
    "unit_price": "Unit Price",
    "amount": "Amount",
    "vat_amount": "VAT",
    "line_total": "Total",
}

NOISE_PATTERNS = [
    re.compile(r"^\s*$"),
    re.compile(r"^\s*(page|p\.)\s*\d+(\s+of\s+\d+)?\s*$", re.IGNORECASE),
    re.compile(r"^\s*(quotation|quote|inquiry|lpo|local purchase order)\s*$", re.IGNORECASE),
    re.compile(r"^\s*(date|tender no|tender number|quote no|quotation no)\s*:?\s*[-\w/ .]*$", re.IGNORECASE),
    re.compile(r"^\s*(from\s*\(?seller\)?|to\s*\(?the buyer\)?|kind attn|buyer|seller)\b", re.IGNORECASE),
    re.compile(r"^\s*(tel|fax|e-?mail|email|p\s*o\s*box|website|www\.|https?://)\b", re.IGNORECASE),
    re.compile(r"^\s*[\w.+-]+@[\w.-]+\.\w+\s*$", re.IGNORECASE),
    re.compile(r"^\s*(procurement officer|procurement|contact person|yours truly|for al ameen)\b", re.IGNORECASE),
    re.compile(r"^\s*(subtotal|total|vat|amount|grand total)\b", re.IGNORECASE),
    re.compile(r"^\s*(prepared by|approved by|signature|stamp)\b", re.IGNORECASE),
]

SERIAL_PREFIX_RE = re.compile(r"^\s*(?P<serial>\d{1,5})(?:\s*[\).\-/|:]\s*|\s+)(?P<rest>.+)$")
PUNCT_NOISE_TRANS = str.maketrans({char: " " for char in string.punctuation if char not in {"/", "#"}})
PRICE_RE = re.compile(
    rf"\bprice\s*:?\s*(?P<price>\d+(?:[.,]\d+)?)\s*(?:per\s+(?P<unit>{UNIT_PATTERN}))?",
    re.IGNORECASE,
)
TERMINAL_PRICE_ROW_RE = re.compile(
    rf"^(?P<name>.+?)\s+(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<unit>{UNIT_PATTERN})\s+"
    r"(?P<unit_price>\d+(?:[.,]\d+)?)\s+(?P<total>\d+(?:[.,]\d+)?)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class HeaderDetection:
    row_offset: int
    row_number: int
    columns: dict
    score: float
    labels: dict
    data_score: float


class _ClipboardTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._in_cell = False
        self._current_row = None
        self._current_cell = []

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag == "tr":
            self._current_row = []
        elif tag in {"td", "th"} and self._current_row is not None:
            self._in_cell = True
            self._current_cell = []

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag in {"td", "th"} and self._in_cell and self._current_row is not None:
            self._current_row.append(normalize_import_line(" ".join(self._current_cell)))
            self._current_cell = []
            self._in_cell = False
        elif tag == "tr" and self._current_row is not None:
            if _meaningful_cells(self._current_row):
                self.rows.append(self._current_row)
            self._current_row = None


def normalize_import_line(value):
    return re.sub(r"\s+", " ", str(value or "").replace("\u00a0", " ")).strip()


def normalize_header(value):
    value = normalize_import_line(value).lower()
    value = value.replace("\\", "/")
    value = re.sub(r"[:|]+", " ", value)
    value = value.translate(PUNCT_NOISE_TRANS)
    return re.sub(r"\s+", " ", value).strip()


def normalize_unit(value):
    unit = normalize_import_line(value)
    lowered = unit.lower().rstrip(".")
    if lowered in {"cartoon", "cartoons"}:
        return "carton"
    return unit[:50]


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
    return "requested_item_name" in roles and bool(
        {"quantity", "unit", "serial_no", "unit_price", "amount", "vat_amount", "line_total"} & set(roles)
    )


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
    unit_text = normalize_unit(_cell_text(unit_value))
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
            return parse_decimal(match.group("qty")), normalize_unit(match.group("unit"))

    unit_match = re.search(rf"\b(?P<unit>{UNIT_PATTERN})\b", unit_text, re.IGNORECASE)
    if quantity is not None and unit_match:
        return quantity, normalize_unit(unit_match.group("unit"))

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
    unit_price_index = columns.get("unit_price")
    total_index = columns.get("line_total")
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
    unit_price_value = _cell_text(row[unit_price_index]) if unit_price_index is not None and unit_price_index < len(row) else ""
    total_value = _cell_text(row[total_index]) if total_index is not None and total_index < len(row) else ""
    if parse_decimal(unit_price_value) is not None:
        score += 0.75
    if parse_decimal(total_value) is not None:
        score += 0.75
    return score


def detect_header_row(rows, *, start_row_number=1, max_scan_rows=20):
    best = None
    for offset, row in enumerate(rows[:max_scan_rows]):
        if is_title_row(row):
            continue
        roles, labels = _row_roles(row)
        if "requested_item_name" not in roles:
            continue
        if not bool({"quantity", "unit", "serial_no", "unit_price", "amount", "vat_amount", "line_total"} & set(roles)):
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
        if "unit_price" in roles:
            base_score += 2
        if "amount" in roles:
            base_score += 1
        if "vat_amount" in roles:
            base_score += 1
        if "line_total" in roles:
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


def _money_note(label, value):
    preview = decimal_to_preview(value)
    return f"{label}: {preview}" if preview is not None else ""


def _price_notes(*, unit_price=None, amount=None, vat_amount=None, line_total=None, price_unit="", pack_info=""):
    notes = []
    if unit_price is not None:
        price_text = _money_note("Unit price", unit_price)
        if price_text and price_unit:
            price_text = f"{price_text} per {price_unit}"
        if price_text:
            notes.append(price_text)
    for label, value in [("Amount", amount), ("VAT", vat_amount), ("Total", line_total)]:
        note = _money_note(label, value)
        if note:
            notes.append(note)
    if pack_info:
        notes.append(f"Pack info: {pack_info}")
    return "; ".join(notes)


def _cell_by_role(row, columns, role):
    index = columns.get(role)
    if index is None or index >= len(row):
        return ""
    return _cell_text(row[index])


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

    quantity_value = _cell_by_role(row, columns, "quantity")
    unit_value = _cell_by_role(row, columns, "unit")
    quantity, unit = split_quantity_unit(quantity_value, unit_value)
    unit = normalize_unit(unit)
    unit_price = parse_decimal(_cell_by_role(row, columns, "unit_price"))
    amount = parse_decimal(_cell_by_role(row, columns, "amount"))
    vat_amount = parse_decimal(_cell_by_role(row, columns, "vat_amount"))
    line_total = parse_decimal(_cell_by_role(row, columns, "line_total"))

    confidence = float(base_confidence)
    if header and header.data_score:
        confidence += 0.05
    if quantity is not None and unit:
        confidence += 0.05
    if stripped_serial or serial_no:
        confidence += 0.03
    if unit_price is not None:
        confidence += 0.03
    if line_total is not None:
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
        notes=_price_notes(unit_price=unit_price, amount=amount, vat_amount=vat_amount, line_total=line_total),
        parse_status=confidence_status(confidence),
        parse_confidence=confidence,
        unit_price=decimal_to_preview(unit_price),
        amount=decimal_to_preview(amount),
        vat_amount=decimal_to_preview(vat_amount),
        line_total=decimal_to_preview(line_total),
        source_sheet=source_sheet,
        sheet_name=source_sheet,
        source_row=source_row,
        row_number=source_row,
        source_page=source_page,
        page_number=source_page,
        serial_no=serial_no,
    ), None


def _find_quantity_before_price(before_price, price_unit=""):
    text = normalize_import_line(before_price)
    if not text:
        return None, normalize_unit(price_unit), ""
    unit_pattern = UNIT_PATTERN
    candidates = list(re.finditer(rf"\b(?P<qty>\d+(?:[.,]\d+)?)\s+(?P<context>(?:\w+\s+){{0,3}}?)(?P<unit>{unit_pattern})\b", text, re.IGNORECASE))
    if price_unit:
        normalized_price_unit = normalize_unit(price_unit)
        matching = [
            candidate
            for candidate in candidates
            if normalize_unit(candidate.group("unit")).lower() == normalized_price_unit.lower()
            and "per" not in normalize_import_line(candidate.group("context")).lower().split()
        ]
        if matching:
            chosen = matching[-1]
            pack_info = normalize_import_line(text[: chosen.start()])
            return parse_decimal(chosen.group("qty")), normalized_price_unit, pack_info
        pack_pattern = re.compile(rf"\b\d+(?:[.,]\d+)?\s+{UNIT_PATTERN}\s+per\s+{re.escape(price_unit)}\b", re.IGNORECASE)
        pack_matches = list(pack_pattern.finditer(text))
        if pack_matches:
            return None, normalized_price_unit, normalize_import_line(pack_matches[-1].group(0))
        trailing_qty = list(re.finditer(r"\b(?P<qty>\d+(?:[.,]\d+)?)\b", text))
        if trailing_qty:
            chosen = trailing_qty[-1]
            pack_info = normalize_import_line(text[: chosen.start()])
            return parse_decimal(chosen.group("qty")), normalized_price_unit, pack_info
        return None, normalized_price_unit, text
    if candidates:
        chosen = candidates[-1]
        pack_info = normalize_import_line(text[: chosen.start()])
        return parse_decimal(chosen.group("qty")), normalize_unit(chosen.group("unit")), pack_info
    return None, "", text


def _parse_price_rich_line(stripped, raw_line, *, base_confidence=0.55, serial_no="", stripped_serial=False, **source_meta):
    price_match = PRICE_RE.search(stripped)
    if not price_match:
        terminal_match = TERMINAL_PRICE_ROW_RE.match(stripped)
        if not terminal_match:
            return None
        quantity, unit = split_quantity_unit(terminal_match.group("qty"), terminal_match.group("unit"))
        unit_price = parse_decimal(terminal_match.group("unit_price"))
        line_total = parse_decimal(terminal_match.group("total"))
        confidence = 0.82
        if quantity is not None and unit and unit_price is not None and line_total is not None:
            confidence += 0.08
        return make_preview_line(
            raw_line=raw_line,
            raw_name=terminal_match.group("name"),
            quantity=quantity,
            unit=unit,
            notes=_price_notes(unit_price=unit_price, line_total=line_total),
            parse_status=confidence_status(confidence),
            parse_confidence=min(confidence, 0.95),
            unit_price=decimal_to_preview(unit_price),
            line_total=decimal_to_preview(line_total),
            serial_no=serial_no,
            **source_meta,
        )

    before_price = normalize_import_line(stripped[: price_match.start()].rstrip(" ,;:-"))
    unit_price = parse_decimal(price_match.group("price"))
    price_unit = normalize_unit(price_match.group("unit") or "")
    quantity, unit, pack_info = _find_quantity_before_price(before_price, price_unit)

    name = before_price
    if pack_info and quantity is not None:
        name = pack_info
    name = re.sub(rf"[,;]?\s*\d+(?:[.,]\d+)?\s+{UNIT_PATTERN}\s+per\s+{UNIT_PATTERN}\s*$", "", name, flags=re.IGNORECASE)
    name = re.sub(rf"[,;]?\s*\d+(?:[.,]\d+)?\s+{UNIT_PATTERN}\s*$", "", name, flags=re.IGNORECASE)
    name = normalize_import_line(name.rstrip(" ,;:-"))

    confidence = max(base_confidence, 0.62)
    if unit_price is not None:
        confidence += 0.08
    if quantity is not None and unit:
        confidence += 0.12
    else:
        confidence -= 0.05
    if stripped_serial:
        confidence += 0.03

    return make_preview_line(
        raw_line=raw_line,
        raw_name=name or before_price or stripped,
        quantity=quantity,
        unit=unit,
        notes=_price_notes(unit_price=unit_price, price_unit=price_unit or unit, pack_info=pack_info if pack_info != name else ""),
        parse_status=confidence_status(confidence),
        parse_confidence=min(confidence, 0.90),
        unit_price=decimal_to_preview(unit_price),
        price_unit=price_unit or unit,
        serial_no=serial_no,
        **source_meta,
    )


def parse_inquiry_paragraph(raw_lines, *, base_confidence=0.55, **source_meta):
    normalized_lines = [normalize_import_line(line) for line in raw_lines if normalize_import_line(line)]
    if not normalized_lines:
        return None
    paragraph = normalize_import_line(" ".join(normalized_lines))
    if not PRICE_RE.search(paragraph):
        return parse_inquiry_line(paragraph, base_confidence=base_confidence, **source_meta)

    first_line = normalized_lines[0]
    if len(normalized_lines) > 1 and not PRICE_RE.search(first_line):
        details = normalize_import_line(" ".join(normalized_lines[1:]))
        parsed = _parse_price_rich_line(details, paragraph, base_confidence=base_confidence, **source_meta)
        if parsed:
            parsed["raw_name"] = clean_item_name(first_line)
            parsed["requested_item_name"] = parsed["raw_name"]
        return parsed
    return _parse_price_rich_line(paragraph, paragraph, base_confidence=base_confidence, **source_meta)


def parse_inquiry_line(raw_line, *, base_confidence=0.55, **source_meta):
    raw_line = normalize_import_line(raw_line)
    if not raw_line:
        return None
    if is_noise_line(raw_line):
        return None

    stripped, serial_no, stripped_serial = strip_serial_prefix(raw_line)
    if _looks_like_request_title(stripped):
        return None

    price_parsed = _parse_price_rich_line(
        stripped,
        raw_line,
        base_confidence=base_confidence,
        serial_no=serial_no,
        stripped_serial=stripped_serial,
        **source_meta,
    )
    if price_parsed:
        return price_parsed

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


def _parse_structured_rows(rows, **source_meta):
    header = detect_header_row(rows, max_scan_rows=10)
    if not header:
        return [], 0
    lines = []
    skipped = header.row_offset + 1
    for offset, row in enumerate(rows[header.row_offset + 1 :], start=header.row_offset + 2):
        parsed, reason = parse_structured_row(
            row,
            header,
            source_row=offset,
            base_confidence=0.88,
            **source_meta,
        )
        if parsed:
            lines.append(parsed)
        elif reason == "skipped":
            skipped += 1
    return lines, skipped


def parse_html_table_lines(raw_html, **source_meta):
    html = str(raw_html or "")
    if "<table" not in html.lower() and "<tr" not in html.lower():
        return [], 0
    parser = _ClipboardTableParser()
    parser.feed(html)
    return _parse_structured_rows(parser.rows, **source_meta)


def _split_delimited_text_rows(raw_text):
    rows = []
    for raw_line in str(raw_text or "").splitlines():
        line = normalize_import_line(raw_line)
        if not line:
            continue
        if "\t" in raw_line:
            cells = [normalize_import_line(cell) for cell in raw_line.split("\t")]
        elif "|" in line:
            cells = [normalize_import_line(cell) for cell in re.split(r"\s*\|\s*", line)]
        else:
            continue
        if len(_meaningful_cells(cells)) > 1:
            rows.append(cells)
    return rows


def _looks_like_serial_cell(value):
    return bool(re.fullmatch(r"\d{1,5}", normalize_import_line(value)))


def _reconstruct_cell_per_line_table(raw_text):
    cells = [normalize_import_line(line) for line in str(raw_text or "").splitlines()]
    cells = [cell for cell in cells if cell]
    if len(cells) < 8:
        return []

    roles = []
    header_cells = []
    index = 0
    while index < len(cells):
        role = classify_header_cell(cells[index])
        if not role:
            break
        roles.append(role)
        header_cells.append(cells[index])
        index += 1
        if "requested_item_name" in roles and "quantity" in roles and "unit" in roles:
            next_role = classify_header_cell(cells[index]) if index < len(cells) else None
            if next_role:
                continue
            break

    if "requested_item_name" not in roles or "quantity" not in roles or "unit" not in roles:
        return []

    rows = [header_cells]
    role_count = len(header_cells)
    has_price = "unit_price" in roles or "amount" in roles or "line_total" in roles
    while index < len(cells):
        if "serial_no" in roles:
            if index + 3 >= len(cells) or not _looks_like_serial_cell(cells[index]):
                index += 1
                continue
            row = [
                cells[index],
                cells[index + 1] if index + 1 < len(cells) else "",
                cells[index + 2] if index + 2 < len(cells) else "",
                cells[index + 3] if index + 3 < len(cells) else "",
            ]
            index += 4
            if has_price:
                if index < len(cells) and not _looks_like_serial_cell(cells[index]):
                    row.append(cells[index])
                    index += 1
                else:
                    row.append("")
            while len(row) < role_count:
                row.append("")
            rows.append(row[:role_count])
        else:
            if index + 2 >= len(cells):
                break
            row = cells[index : index + role_count]
            rows.append(row)
            index += role_count
    return rows if len(rows) > 1 else []


def parse_text_table_lines(raw_text, **source_meta):
    delimited_rows = _split_delimited_text_rows(raw_text)
    lines, skipped = _parse_structured_rows(delimited_rows, **source_meta)
    if lines:
        return lines, skipped

    reconstructed_rows = _reconstruct_cell_per_line_table(raw_text)
    return _parse_structured_rows(reconstructed_rows, **source_meta)


def parse_text_lines(raw_text, **source_meta):
    table_lines, table_skipped = parse_text_table_lines(raw_text, **source_meta)
    if table_lines:
        return table_lines, table_skipped

    lines = []
    skipped = 0
    paragraph = []
    paragraph_start = 1

    def flush_paragraph():
        nonlocal paragraph, paragraph_start, skipped
        if not paragraph:
            return
        if len(paragraph) > 1 and any(PRICE_RE.search(line) for line in paragraph):
            parsed = parse_inquiry_paragraph(paragraph, source_line=paragraph_start, row_number=paragraph_start, **source_meta)
            if parsed:
                lines.append(parsed)
            else:
                skipped += len(paragraph)
            paragraph = []
            return
        for local_offset, paragraph_line in enumerate(paragraph):
            parsed = parse_inquiry_line(paragraph_line, source_line=paragraph_start + local_offset, row_number=paragraph_start + local_offset, **source_meta)
            if parsed:
                lines.append(parsed)
            else:
                skipped += 1
        paragraph = []

    for index, raw_line in enumerate(str(raw_text or "").splitlines(), start=1):
        normalized = normalize_import_line(raw_line)
        if not normalized:
            flush_paragraph()
            continue
        if is_noise_line(normalized) or _looks_like_request_title(normalized):
            flush_paragraph()
            skipped += 1
            continue
        cells = [part.strip() for part in re.split(r"\s*\|\s*", normalized)]
        if len(cells) > 1 and is_header_like_row(cells):
            flush_paragraph()
            skipped += 1
            continue
        if not paragraph:
            paragraph_start = index
        paragraph.append(normalized)
    flush_paragraph()
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
