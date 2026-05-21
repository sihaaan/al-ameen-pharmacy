import hashlib
import zipfile
from io import BytesIO
from pathlib import Path

import pdfplumber
from django.conf import settings
from django.core.exceptions import ValidationError
from openpyxl import load_workbook
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from .import_rules import make_preview_line, normalize_import_line, parse_inquiry_line, parse_text_lines


ALLOWED_EXTENSIONS = {".xlsx", ".pdf"}
EXCEL_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"

ITEM_HEADERS = {
    "item",
    "item name",
    "description",
    "item description",
    "product",
    "product name",
    "medicine",
    "medicine name",
    "name",
}
QTY_HEADERS = {"qty", "quantity", "qnty", "required qty", "requested qty", "requested quantity"}
UNIT_HEADERS = {"unit", "uom", "unit of measure", "pack", "packing"}


def max_upload_bytes():
    return int(getattr(settings, "QUOTATION_IMPORT_MAX_UPLOAD_BYTES", 5 * 1024 * 1024))


def max_excel_rows():
    return int(getattr(settings, "QUOTATION_IMPORT_MAX_EXCEL_ROWS", 500))


def max_pdf_pages():
    return int(getattr(settings, "QUOTATION_IMPORT_MAX_PDF_PAGES", 10))


def _clean_header(value):
    return normalize_import_line(value).lower().strip(":")


def _cell_text(value):
    if value is None:
        return ""
    return normalize_import_line(value)


def _row_text(row):
    return " | ".join(_cell_text(value) for value in row if _cell_text(value))


def _find_header_map(rows):
    for offset, row in enumerate(rows[:15]):
        headers = [_clean_header(value) for value in row]
        item_index = next((index for index, header in enumerate(headers) if header in ITEM_HEADERS), None)
        qty_index = next((index for index, header in enumerate(headers) if header in QTY_HEADERS), None)
        unit_index = next((index for index, header in enumerate(headers) if header in UNIT_HEADERS), None)
        if item_index is not None:
            return {
                "row_offset": offset,
                "item": item_index,
                "quantity": qty_index,
                "unit": unit_index,
            }
    return None


def read_upload_bytes(uploaded_file):
    if not uploaded_file:
        raise ValidationError("No file was uploaded.")
    limit = max_upload_bytes()
    data = bytearray()
    for chunk in uploaded_file.chunks():
        data.extend(chunk)
        if len(data) > limit:
            raise ValidationError(f"Uploaded file is too large. Maximum size is {limit // (1024 * 1024)} MB.")
    if not data:
        raise ValidationError("Uploaded file is empty.")
    return bytes(data)


def parse_text_preview(raw_text):
    lines, skipped = parse_text_lines(raw_text)
    warnings = []
    if not lines:
        warnings.append("No item lines were detected. Review the pasted text or add rows manually.")
    return {
        "source_type": "pasted_text",
        "source_filename": "",
        "source_mime_type": "text/plain",
        "source_sha256": hashlib.sha256(str(raw_text or "").encode("utf-8")).hexdigest(),
        "parse_method": "deterministic_text_v1",
        "original_text": raw_text or "",
        "lines": lines,
        "warnings": warnings,
        "meta": {
            "line_count": len(lines),
            "skipped_noise_lines": skipped,
        },
    }


def parse_file_preview(uploaded_file):
    data = read_upload_bytes(uploaded_file)
    filename = Path(uploaded_file.name or "").name
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValidationError("Unsupported file type. Upload .xlsx or .pdf files only.")

    sha256 = hashlib.sha256(data).hexdigest()
    if extension == ".xlsx":
        return parse_excel_preview(data, filename, uploaded_file.content_type or "", sha256)
    return parse_pdf_preview(data, filename, uploaded_file.content_type or "", sha256)


def parse_excel_preview(data, filename, content_type, sha256):
    if not data.startswith(b"PK") or not zipfile.is_zipfile(BytesIO(data)):
        raise ValidationError("Invalid Excel file. The upload does not look like a valid .xlsx workbook.")

    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValidationError(f"Could not read Excel workbook: {exc}") from exc

    lines = []
    warnings = []
    inspected_sheets = []
    row_limit = max_excel_rows()

    try:
        for sheet in workbook.worksheets[:3]:
            if getattr(sheet, "sheet_state", "visible") != "visible":
                continue
            inspected_sheets.append(sheet.title)
            rows = []
            for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
                rows.append((row_index, row))
                if len(rows) >= row_limit:
                    warnings.append(f"Stopped reading sheet '{sheet.title}' after {row_limit} rows.")
                    break
            value_rows = [row for _, row in rows]
            header_map = _find_header_map(value_rows)
            if header_map:
                start = header_map["row_offset"] + 1
                for source_row, row in rows[start:]:
                    raw_line = _row_text(row)
                    if not raw_line:
                        continue
                    item = _cell_text(row[header_map["item"]]) if header_map["item"] < len(row) else ""
                    if not item:
                        continue
                    quantity = (
                        _cell_text(row[header_map["quantity"]])
                        if header_map["quantity"] is not None and header_map["quantity"] < len(row)
                        else None
                    )
                    unit = (
                        _cell_text(row[header_map["unit"]])
                        if header_map["unit"] is not None and header_map["unit"] < len(row)
                        else ""
                    )
                    confidence = 0.9 if quantity else 0.7
                    status = "parsed" if quantity else "needs_review"
                    lines.append(
                        make_preview_line(
                            raw_line=raw_line,
                            raw_name=item,
                            quantity=quantity,
                            unit=unit,
                            parse_status=status,
                            parse_confidence=confidence,
                            source_sheet=sheet.title,
                            source_row=source_row,
                        )
                    )
            else:
                warnings.append(f"No clear header row detected in sheet '{sheet.title}'. Parsed visible text rows instead.")
                for source_row, row in rows:
                    raw_line = _row_text(row)
                    if not raw_line:
                        continue
                    parsed = parse_inquiry_line(raw_line, source_sheet=sheet.title, source_row=source_row)
                    if parsed:
                        lines.append(parsed)
    finally:
        workbook.close()

    if not lines:
        warnings.append("No item lines were detected in the Excel workbook.")

    return {
        "source_type": "excel",
        "source_filename": filename,
        "source_mime_type": content_type or EXCEL_MIME,
        "source_sha256": sha256,
        "parse_method": "openpyxl_v1",
        "original_text": "",
        "lines": lines,
        "warnings": warnings,
        "meta": {
            "line_count": len(lines),
            "inspected_sheets": inspected_sheets,
        },
    }


def _preflight_pdf(data):
    if not data.startswith(b"%PDF-"):
        raise ValidationError("Invalid PDF file. The upload does not look like a PDF.")
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise ValidationError(f"Could not read PDF: {exc}") from exc
    if reader.is_encrypted:
        raise ValidationError("Encrypted PDF files are not supported. Please upload an unlocked PDF.")
    page_count = len(reader.pages)
    if page_count > max_pdf_pages():
        raise ValidationError(f"PDF has {page_count} pages. Maximum supported pages: {max_pdf_pages()}.")
    return page_count


def parse_pdf_preview(data, filename, content_type, sha256):
    page_count = _preflight_pdf(data)
    lines = []
    warnings = []
    text_chunks = []
    table_rows = 0

    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            for page_index, page in enumerate(pdf.pages, start=1):
                for table in page.extract_tables() or []:
                    table_rows += len(table)
                    header_map = _find_header_map(table[:10])
                    start = header_map["row_offset"] + 1 if header_map else 0
                    for row_number, row in enumerate(table[start:], start=start + 1):
                        raw_line = _row_text(row)
                        if not raw_line:
                            continue
                        if header_map and header_map["item"] < len(row):
                            item = _cell_text(row[header_map["item"]])
                            if not item:
                                continue
                            quantity = (
                                _cell_text(row[header_map["quantity"]])
                                if header_map["quantity"] is not None and header_map["quantity"] < len(row)
                                else None
                            )
                            unit = (
                                _cell_text(row[header_map["unit"]])
                                if header_map["unit"] is not None and header_map["unit"] < len(row)
                                else ""
                            )
                            lines.append(
                                make_preview_line(
                                    raw_line=raw_line,
                                    raw_name=item,
                                    quantity=quantity,
                                    unit=unit,
                                    parse_status="parsed" if quantity else "needs_review",
                                    parse_confidence=0.85 if quantity else 0.65,
                                    source_page=page_index,
                                    source_row=row_number,
                                )
                            )
                        else:
                            parsed = parse_inquiry_line(raw_line, source_page=page_index, source_row=row_number)
                            if parsed:
                                lines.append(parsed)

                extracted = page.extract_text() or ""
                if extracted.strip():
                    text_chunks.append(extracted)
    except Exception as exc:
        raise ValidationError(f"Could not parse PDF content: {exc}") from exc

    if not lines and text_chunks:
        parsed_lines, skipped = parse_text_lines("\n".join(text_chunks))
        for parsed in parsed_lines:
            parsed.setdefault("source_page", "")
        lines = parsed_lines
        if skipped:
            warnings.append(f"Skipped {skipped} likely heading/footer line(s) while parsing PDF text.")

    if not lines and not text_chunks:
        warnings.append("No selectable text detected. OCR is not enabled in this environment.")
    elif not lines:
        warnings.append("Selectable text was found, but no item lines were confidently detected. Add rows manually if needed.")

    return {
        "source_type": "pdf",
        "source_filename": filename,
        "source_mime_type": content_type or PDF_MIME,
        "source_sha256": sha256,
        "parse_method": "pypdf_pdfplumber_v1",
        "original_text": "\n".join(text_chunks).strip(),
        "lines": lines,
        "warnings": warnings,
        "meta": {
            "line_count": len(lines),
            "page_count": page_count,
            "table_rows_seen": table_rows,
        },
    }
