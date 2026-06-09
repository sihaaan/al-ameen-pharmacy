from datetime import timedelta
from io import BytesIO

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

from .pdf_config import get_quotation_pdf_config


PRIMARY = "0F766E"
BORDER = "D1D5DB"
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


def _safe_number(value):
    return float(value or 0)


def build_quotation_excel(quotation):
    config = get_quotation_pdf_config()
    quote_date = _local_date(quotation.created_at)
    valid_until = _valid_until(quotation, config)

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Quotation"
    sheet.sheet_view.showGridLines = True

    thin = Side(style="thin", color=BORDER)
    header_border = Border(bottom=thin)
    total_border = Border(top=thin)
    header_fill = PatternFill("solid", fgColor="E5E7EB")

    sheet["A1"] = "Quotation Export"
    sheet["A1"].font = Font(bold=True, size=14, color=TEXT)
    sheet["A2"] = config.company_name or "Al Ameen Pharmacy"
    sheet["A2"].font = Font(bold=True, color=PRIMARY)

    contact = quotation.contact
    contact_number = " / ".join(
        part for part in [
            getattr(contact, "phone", "") if contact else "",
            getattr(contact, "email", "") if contact else "",
        ]
        if part
    )
    info_rows = [
        ("Customer", quotation.company.name),
        ("Attention", contact.name if contact else "-"),
        ("Position", contact.role if contact else "-"),
        ("Department", contact.department if contact else "-"),
        ("Contact No.", contact_number or "-"),
        ("Quotation #", quotation.quotation_number),
        ("Date", quote_date),
        ("Valid Until", valid_until),
        ("Currency", quotation.currency),
        ("Status", quotation.get_status_display()),
        ("Payment Terms", _payment_terms(quotation, config) or "-"),
        ("Prepared By", quotation.created_by.username if quotation.created_by else "-"),
    ]
    for row_offset, (label, value) in enumerate(info_rows, start=4):
        label_cell = sheet.cell(row=row_offset, column=1, value=label)
        value_cell = sheet.cell(row=row_offset, column=2, value=value)
        label_cell.font = Font(bold=True, color=MUTED)
        label_cell.alignment = Alignment(vertical="top")
        value_cell.alignment = Alignment(vertical="top")
        if hasattr(value, "year"):
            value_cell.number_format = "dd/mm/yyyy"

    table_start = 18
    headers = ["S. No.", "Item Description", "Qty", "Unit", "Unit Price", "VAT %", "VAT Amount", "Line Total"]
    for column, header in enumerate(headers, start=1):
        cell = sheet.cell(row=table_start, column=column, value=header)
        cell.fill = header_fill
        cell.font = Font(bold=True, color=TEXT)
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = header_border

    money_format = "#,##0.00"
    for index, line in enumerate(quotation.lines.order_by("sort_order", "id"), start=1):
        row = table_start + index
        values = [
            index,
            line.item_name_snapshot,
            _safe_number(line.quantity),
            line.unit or "-",
            _safe_number(line.unit_price),
            _safe_number(line.vat_rate),
            _safe_number(line.vat_amount),
            _safe_number(line.line_total),
        ]
        for column, value in enumerate(values, start=1):
            cell = sheet.cell(row=row, column=column, value=value)
            cell.alignment = Alignment(vertical="top", wrap_text=(column == 2))
            if column == 1:
                cell.alignment = Alignment(horizontal="center", vertical="top")
            elif column in {3, 5, 6, 7, 8}:
                cell.alignment = Alignment(horizontal="right", vertical="top")
            if column == 3:
                cell.number_format = "#,##0.000"
            elif column == 6:
                cell.number_format = "0.00"
            elif column in {5, 7, 8}:
                cell.number_format = money_format

    last_line_row = table_start + max(quotation.lines.count(), 1)
    totals_start = last_line_row + 2
    totals = [
        ("Subtotal", _safe_number(quotation.subtotal)),
        ("VAT", _safe_number(quotation.vat_total)),
        ("Grand Total", _safe_number(quotation.total)),
    ]
    for row_offset, (label, value) in enumerate(totals, start=totals_start):
        label_cell = sheet.cell(row=row_offset, column=7, value=label)
        value_cell = sheet.cell(row=row_offset, column=8, value=value)
        for cell in (label_cell, value_cell):
            cell.font = Font(bold=True, color=PRIMARY if label == "Grand Total" else TEXT)
            cell.alignment = Alignment(horizontal="right")
            if label == "Subtotal":
                cell.border = total_border
        value_cell.number_format = money_format

    footer_start = totals_start + len(totals) + 2
    sheet.cell(row=footer_start, column=1, value="Terms").font = Font(bold=True, color=MUTED)
    sheet.cell(row=footer_start, column=2, value=config.default_terms or "")
    sheet.cell(row=footer_start + 1, column=1, value="Payment Terms").font = Font(bold=True, color=MUTED)
    sheet.cell(row=footer_start + 1, column=2, value=_payment_terms(quotation, config) or "-")

    widths = {
        "A": 11,
        "B": 48,
        "C": 12,
        "D": 16,
        "E": 14,
        "F": 10,
        "G": 14,
        "H": 14,
    }
    for column, width in widths.items():
        sheet.column_dimensions[column].width = width
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_margins.left = 0.3
    sheet.page_margins.right = 0.3
    sheet.page_margins.top = 0.5
    sheet.page_margins.bottom = 0.5

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
