import hashlib
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from io import BytesIO
from pathlib import Path

import pdfplumber
from django.core.exceptions import ValidationError
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .import_parsers import PDF_MIME, _validate_upload_type, max_pdf_pages, read_upload_bytes
from .import_rules import UNIT_WORDS, normalize_header, normalize_import_line
from .private_storage import store_import_source


HISTORICAL_PARSE_METHOD = "al_ameen_pdf_price_table_v1"

HEADER_ALIASES = {
    "serial_no": {"sn", "s n", "sl no", "sr no", "serial", "serial no", "#"},
    "item_name": {"material description", "item description", "description", "item", "items", "particulars"},
    "unit": {"uom", "unit", "u o m"},
    "quantity": {"qty", "quantity", "qnty", "req quantity", "requested quantity", "required quantity"},
    "unit_price": {"u p", "up", "u/p", "u price", "unit price", "rate", "price"},
    "amount": {"amount", "net price", "subtotal", "value"},
    "vat_amount": {"vat", "vat amount"},
    "line_total": {"g total", "grand total", "total", "net total", "gross total"},
}

UNIT_WORD_SET = {unit.lower().rstrip(".") for unit in UNIT_WORDS}


def _clean_cell(value):
    return normalize_import_line(str(value or "").replace("\n", " "))


def _decimal(value):
    text = _clean_cell(value).replace(",", "")
    if not text or text in {"-", "—", "–"}:
        return None
    match = re.search(r"-?\d+(?:\.\d+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _money(value, default_zero=False):
    parsed = _decimal(value)
    if parsed is None and default_zero:
        return Decimal("0.00")
    if parsed is None:
        return None
    return parsed.quantize(Decimal("0.01"))


def _quantity(value):
    parsed = _decimal(value)
    if parsed is None:
        return None
    return parsed.quantize(Decimal("0.001"))


def _preview_decimal(value):
    if value is None:
        return None
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _header_role(value):
    normalized = normalize_header(value)
    for role, aliases in HEADER_ALIASES.items():
        if normalized in aliases:
            return role
    return None


def _find_header(row):
    mapping = {}
    labels = {}
    for index, value in enumerate(row or []):
        role = _header_role(value)
        if role and role not in mapping:
            mapping[role] = index
            labels[role] = _clean_cell(value)
    required = {"item_name", "quantity", "unit_price", "line_total"}
    if required.issubset(mapping):
        return mapping, labels
    return None, {}


def _cell(row, mapping, role):
    index = mapping.get(role)
    if index is None or index >= len(row):
        return ""
    return _clean_cell(row[index])


def _split_uom(item_name, uom):
    uom = _clean_cell(uom)
    if not uom:
        return item_name, ""
    parts = uom.split()
    if len(parts) > 1 and parts[-1].lower().rstrip(".") in UNIT_WORD_SET:
        return normalize_import_line(f"{item_name} {' '.join(parts[:-1])}")[:255], parts[-1]
    return item_name, uom[:50]


def _vat_rate(amount, vat_amount):
    if not amount or amount <= 0 or vat_amount is None:
        return Decimal("0.00")
    return ((vat_amount / amount) * Decimal("100")).quantize(Decimal("0.01"))


def _row_text(row):
    return " | ".join(_clean_cell(cell) for cell in row if _clean_cell(cell))


def _is_total_row(row, mapping):
    item = _cell(row, mapping, "item_name").lower()
    serial = _cell(row, mapping, "serial_no").lower()
    compacted = re.sub(r"[^a-z]", "", _row_text(row).lower())
    return item == "total" or serial == "total" or " total " in f" {item} " or compacted.startswith("total")


def _extract_total_values(row, mapping):
    subtotal = _money(_cell(row, mapping, "amount"))
    vat_total = _money(_cell(row, mapping, "vat_amount"), default_zero=True)
    total = _money(_cell(row, mapping, "line_total"))
    if subtotal is not None and vat_total is not None and total is not None:
        return subtotal, vat_total, total

    numeric_values = []
    for cell in row or []:
        value = _money(cell)
        if value is not None:
            numeric_values.append(value)
    if len(numeric_values) >= 3:
        return numeric_values[-3], numeric_values[-2], numeric_values[-1]
    return subtotal, vat_total, total


def _parse_table_row(row, mapping, *, page_number, row_number, sort_order):
    raw_line = _row_text(row)
    if not raw_line or _is_total_row(row, mapping):
        return None

    item_name = _cell(row, mapping, "item_name")
    item_name = re.sub(r"^\s*\d+[\).\-/|:]\s*", "", item_name).strip()
    if not item_name:
        return None

    item_name, unit = _split_uom(item_name, _cell(row, mapping, "unit"))
    quantity = _quantity(_cell(row, mapping, "quantity"))
    unit_price = _money(_cell(row, mapping, "unit_price"))
    amount = _money(_cell(row, mapping, "amount"))
    vat_amount = _money(_cell(row, mapping, "vat_amount"), default_zero=True)
    line_total = _money(_cell(row, mapping, "line_total"))

    confidence = Decimal("0.80")
    if quantity is not None and unit:
        confidence += Decimal("0.05")
    if unit_price is not None and line_total is not None:
        confidence += Decimal("0.05")
    if amount is not None and vat_amount is not None:
        confidence += Decimal("0.03")

    return {
        "raw_line": raw_line,
        "item_name": item_name[:255],
        "quantity": _preview_decimal(quantity),
        "unit": unit,
        "unit_price": _preview_decimal(unit_price),
        "amount": _preview_decimal(amount),
        "vat_amount": _preview_decimal(vat_amount),
        "vat_rate": _preview_decimal(_vat_rate(amount, vat_amount)),
        "line_total": _preview_decimal(line_total),
        "serial_no": _cell(row, mapping, "serial_no")[:30],
        "source_page": page_number,
        "source_row": row_number,
        "parse_confidence": float(min(confidence, Decimal("0.98"))),
        "status": "needs_review",
        "sort_order": sort_order,
    }


def _extract_pdf_text(data):
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise ValidationError(f"Could not read PDF: {exc}") from exc
    if reader.is_encrypted:
        raise ValidationError("Encrypted PDF files are not supported. Please upload an unlocked PDF.")
    page_count = len(reader.pages)
    if page_count > max_pdf_pages():
        raise ValidationError(f"PDF has {page_count} pages. Maximum supported pages: {max_pdf_pages()}.")
    return "\n".join(page.extract_text() or "" for page in reader.pages), page_count


def _extract_document_number(text):
    match = re.search(r"\b(QUOTATION\s*[-:]\s*[A-Z0-9/-]+)", text or "", re.IGNORECASE)
    if match:
        return re.sub(r"\s+", "", match.group(1)).replace(":", "-").upper()
    match = re.search(r"\b(?:Tender\s+No\.?|Tender\s+Number)\s*:?\s*([A-Z0-9/-]+)", text or "", re.IGNORECASE)
    if match:
        return normalize_import_line(match.group(1)).upper()
    return ""


def _extract_document_date(text):
    match = re.search(r"\bDATE\s*:?\s*(\d{1,3}[/-]\d{1,2}[/-]\d{4})", text or "", re.IGNORECASE)
    if not match:
        return None
    raw_date = match.group(1)
    date_parts = re.split(r"([/-])", raw_date, maxsplit=1)
    if date_parts and date_parts[0].isdigit() and len(date_parts[0]) > 2:
        raw_date = str(int(date_parts[0])) + "".join(date_parts[1:])
    for date_format in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(raw_date, date_format).date()
        except ValueError:
            continue
    return None


def _suggest_company_from_filename(filename):
    stem = Path(filename or "").stem
    stem = re.sub(r"\b\d{6,8}\b", " ", stem)
    stem = re.sub(r"\b\d{1,2}[-_ ]?\d{1,2}[-_ ]?\d{2,4}\b", " ", stem)
    stem = re.sub(r"[_\-]+", " ", stem)
    stem = normalize_import_line(stem)
    return stem.title()[:255]


def parse_historical_pdf_upload(uploaded_file):
    data = read_upload_bytes(uploaded_file)
    filename = Path(uploaded_file.name or "").name
    extension, sniffed_mime = _validate_upload_type(data, filename)
    if extension != ".pdf":
        raise ValidationError("Historical price backfill currently supports finalized quotation PDF files only.")

    sha256 = hashlib.sha256(data).hexdigest()
    text, page_count = _extract_pdf_text(data)
    document_number = _extract_document_number(text)
    document_date = _extract_document_date(text)
    source_file_ref = ""

    lines = []
    warnings = []
    page_metadata = []
    totals = {"subtotal": None, "vat_total": None, "total": None}
    current_mapping = None
    current_labels = {}
    table_rows_seen = 0

    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                page_tables = page.extract_tables() or []
                page_metadata.append({"page_number": page_index, "tables_seen": len(page_tables)})
                for table in page_tables:
                    mapping = None
                    labels = {}
                    start_index = 0
                    for row_index, row in enumerate(table[:3]):
                        candidate_mapping, candidate_labels = _find_header(row)
                        if candidate_mapping:
                            mapping = candidate_mapping
                            labels = candidate_labels
                            start_index = row_index + 1
                            break
                    if mapping:
                        current_mapping = mapping
                        current_labels = labels
                    elif current_mapping and table and len(table[0]) >= len(current_mapping):
                        mapping = current_mapping
                        labels = current_labels
                    else:
                        continue

                    for local_row_index, row in enumerate(table[start_index:], start=start_index + 1):
                        table_rows_seen += 1
                        if _is_total_row(row, mapping):
                            subtotal, vat_total, total = _extract_total_values(row, mapping)
                            totals["subtotal"] = _preview_decimal(subtotal)
                            totals["vat_total"] = _preview_decimal(vat_total)
                            totals["total"] = _preview_decimal(total)
                            continue
                        parsed = _parse_table_row(
                            row,
                            mapping,
                            page_number=page_index,
                            row_number=local_row_index,
                            sort_order=len(lines),
                        )
                        if parsed:
                            lines.append(parsed)
    except Exception as exc:
        raise ValidationError(f"Could not parse historical quotation PDF tables: {exc}") from exc

    if not lines:
        warnings.append("No historical quotation price rows were detected. Confirm this is a text-based Al Ameen quotation PDF.")
    if not document_number:
        warnings.append("Quotation number was not detected. Enter it manually before committing price history.")
    if not document_date:
        warnings.append("Quotation date was not detected. Enter it manually before committing price history.")

    source_file_ref = store_import_source(data, filename=filename, sha256=sha256)

    return {
        "source_type": "pdf",
        "source_filename": filename,
        "source_mime_type": sniffed_mime or PDF_MIME,
        "source_sha256": sha256,
        "source_file_ref": source_file_ref,
        "source_file_size": len(data),
        "parse_method": HISTORICAL_PARSE_METHOD,
        "document_number": document_number,
        "document_date": document_date.isoformat() if document_date else None,
        "suggested_company_name": _suggest_company_from_filename(filename),
        "currency": "AED",
        "subtotal": totals["subtotal"],
        "vat_total": totals["vat_total"],
        "total": totals["total"],
        "lines": lines,
        "warnings": warnings,
        "meta": {
            "page_count": page_count,
            "page_metadata": page_metadata,
            "table_rows_seen": table_rows_seen,
            "detected_columns": current_labels,
        },
    }
