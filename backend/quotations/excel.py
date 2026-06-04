from datetime import timedelta
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .pdf_config import get_quotation_pdf_config


PRIMARY = "0F766E"
ACCENT = "ECFDF5"
BORDER = "D1D5DB"
SOFT = "F9FAFB"
TEXT = "111827"
MUTED = "6B7280"


def _local_date(value):
    if not value:
        return timezone.localdate()
    if hasattr(value, "date"):
        return timezone.localtime(value).date()
    return value


def _valid_until(quotation, config):
    if quotation.valid_until:
        return quotation.valid_until
    return _local_date(quotation.created_at) + timedelta(days=config.validity_days)


def _payment_terms(quotation, config):
    if getattr(quotation, "payment_terms", ""):
        return quotation.get_payment_terms_display()
    return config.payment_terms


def _money_format(currency):
    return f'"{currency}" #,##0.00'


def _safe_number(value):
    return float(value or 0)


def build_quotation_excel(quotation):
    config = get_quotation_pdf_config()
    quote_date = _local_date(quotation.created_at)
    valid_until = _valid_until(quotation, config)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quotation"
    sheet.sheet_view.showGridLines = False

    thin = Side(style="thin", color=BORDER)
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    header_fill = PatternFill("solid", fgColor=PRIMARY)
    accent_fill = PatternFill("solid", fgColor=ACCENT)
    soft_fill = PatternFill("solid", fgColor=SOFT)

    sheet.merge_cells("A1:G1")
    sheet["A1"] = config.company_name or "Al Ameen Pharmacy"
    sheet["A1"].font = Font(bold=True, size=18, color=PRIMARY)
    sheet["A1"].alignment = Alignment(horizontal="center")

    contact_parts = [
        part
        for part in [
            config.address,
            f"Phone: {config.phone}" if config.phone else "",
            f"Email: {config.email}" if config.email else "",
            f"TRN: {config.trn}" if config.trn else "",
            f"License: {config.license_number}" if config.license_number else "",
        ]
        if part
    ]
    sheet.merge_cells("A2:G2")
    sheet["A2"] = " | ".join(contact_parts)
    sheet["A2"].font = Font(size=9, color=MUTED)
    sheet["A2"].alignment = Alignment(horizontal="center")

    sheet.merge_cells("A4:G4")
    sheet["A4"] = "QUOTATION"
    sheet["A4"].font = Font(bold=True, size=16, color=TEXT)
    sheet["A4"].alignment = Alignment(horizontal="center")

    info_rows = [
        ("Customer", quotation.company.name, "Quotation #", quotation.quotation_number),
        ("Contact", quotation.contact.name if quotation.contact else "-", "Date", quote_date),
        ("Currency", quotation.currency, "Valid Until", valid_until),
        ("Status", quotation.get_status_display(), "Prepared By", quotation.created_by.username if quotation.created_by else "-"),
    ]
    for row_offset, values in enumerate(info_rows, start=6):
        for col_offset, value in enumerate(values, start=1):
            cell = sheet.cell(row=row_offset, column=col_offset, value=value)
            cell.border = border
            cell.alignment = Alignment(vertical="top")
            if col_offset in {1, 3}:
                cell.fill = soft_fill
                cell.font = Font(bold=True, color=MUTED)
            if hasattr(value, "year"):
                cell.number_format = "yyyy-mm-dd"

    table_start = 12
    headers = ["#", "Item Description", "Qty", "Unit", "Unit Price", "VAT", "Total"]
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=table_start, column=column, value=header)
        cell.fill = header_fill
        cell.font = Font(bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = border

    money_format = _money_format(quotation.currency)
    for index, line in enumerate(quotation.lines.order_by("sort_order", "id"), start=1):
        row = table_start + index
        values = [
            index,
            line.item_name_snapshot,
            _safe_number(line.quantity),
            line.unit or "-",
            _safe_number(line.unit_price),
            _safe_number(line.vat_amount),
            _safe_number(line.line_total),
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.border = border
            cell.alignment = Alignment(vertical="top", wrap_text=(column == 2))
            if index % 2 == 0:
                cell.fill = soft_fill
            if column == 1:
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif column in {3, 5, 6, 7}:
                cell.alignment = Alignment(horizontal="right", vertical="top")
            if column == 3:
                cell.number_format = "#,##0.000"
            elif column in {5, 6, 7}:
                cell.number_format = money_format

    last_line_row = table_start + max(quotation.lines.count(), 1)
    totals_start = last_line_row + 2
    totals = [
        ("Subtotal", _safe_number(quotation.subtotal)),
        ("VAT", _safe_number(quotation.vat_total)),
        ("Grand Total", _safe_number(quotation.total)),
    ]
    for row_offset, (label, value) in enumerate(totals, start=totals_start):
        label_cell = sheet.cell(row=row_offset, column=6, value=label)
        value_cell = sheet.cell(row=row_offset, column=7, value=value)
        for cell in (label_cell, value_cell):
            cell.border = border
            cell.font = Font(bold=True, color=PRIMARY if label == "Grand Total" else TEXT)
            cell.alignment = Alignment(horizontal="right")
        value_cell.number_format = money_format
        if label == "Grand Total":
            label_cell.fill = accent_fill
            value_cell.fill = accent_fill

    footer_start = totals_start + len(totals) + 2
    sheet.merge_cells(start_row=footer_start, start_column=1, end_row=footer_start, end_column=7)
    sheet.cell(row=footer_start, column=1, value="Terms and Conditions").font = Font(bold=True, color=PRIMARY)
    sheet.merge_cells(start_row=footer_start + 1, start_column=1, end_row=footer_start + 1, end_column=7)
    sheet.cell(row=footer_start + 1, column=1, value=config.default_terms or "")
    sheet.merge_cells(start_row=footer_start + 2, start_column=1, end_row=footer_start + 2, end_column=7)
    sheet.cell(row=footer_start + 2, column=1, value=f"Payment Terms: {_payment_terms(quotation, config) or '-'}")

    widths = {
        "A": 7,
        "B": 44,
        "C": 12,
        "D": 14,
        "E": 16,
        "F": 14,
        "G": 16,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.row_dimensions[1].height = 26
    sheet.freeze_panes = f"A{table_start + 1}"
    sheet.auto_filter.ref = f"A{table_start}:G{last_line_row}"
    sheet.page_setup.orientation = "portrait"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.print_title_rows = f"{table_start}:{table_start}"
    sheet.page_margins.left = 0.3
    sheet.page_margins.right = 0.3
    sheet.page_margins.top = 0.5
    sheet.page_margins.bottom = 0.5

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
