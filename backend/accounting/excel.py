from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .services import statement_filename, statement_ledger


PRIMARY = "0F766E"
LIGHT = "E6FFFA"
SOFT = "F9FAFB"
BORDER = "D1D5DB"
TEXT = "111827"
MUTED = "6B7280"


def money(value):
    return float(value or 0)


def period_text(ledger):
    start = ledger.get("period_start")
    end = ledger.get("period_end")
    if start and end:
        if start == end:
            return start.isoformat()
        return f"{start.isoformat()} to {end.isoformat()}"
    if start:
        return start.isoformat()
    if end:
        return end.isoformat()
    return "No invoice rows"


def statement_excel_filename(import_customer):
    return statement_filename(import_customer).replace(".pdf", ".xlsx")


def apply_common_styles(sheet):
    thin = Side(style="thin", color=BORDER)
    sheet.sheet_view.showGridLines = False
    for row in sheet.iter_rows():
        for cell in row:
            cell.font = Font(name="Calibri", size=10, color=TEXT)
            cell.alignment = Alignment(vertical="center")
            cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)


def build_statement_workbook(import_customer, *, date_from=None, date_to=None):
    ledger = statement_ledger(import_customer, date_from=date_from, date_to=date_to)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Statement"

    sheet.merge_cells("A1:G1")
    sheet["A1"] = "AL AMEEN PHARMACY LLC"
    sheet["A1"].font = Font(name="Calibri", size=16, bold=True, color=PRIMARY)
    sheet["A1"].alignment = Alignment(horizontal="center")

    sheet.merge_cells("A2:G2")
    sheet["A2"] = "Statement of Account"
    sheet["A2"].font = Font(name="Calibri", size=13, bold=True, color=TEXT)
    sheet["A2"].alignment = Alignment(horizontal="center")

    info_rows = [
        ("Customer", import_customer.customer_name, "Statement Date", import_customer.accounting_import.report_date),
        ("Account No.", import_customer.customer_code or "-", "Currency", "AED"),
        ("Statement Period", period_text(ledger), "Final Balance", ledger["final_balance"]),
    ]
    start_row = 4
    for index, row in enumerate(info_rows, start=start_row):
        sheet.cell(index, 1, row[0])
        sheet.cell(index, 2, row[1])
        sheet.cell(index, 4, row[2])
        sheet.cell(index, 5, row[3])

    header_row = 8
    headers = ["Invoice Date", "Doc Type", "Invoice No.", "LPO / Reference No.", "Debit", "Credit", "Balance"]
    for col, header in enumerate(headers, start=1):
        cell = sheet.cell(header_row, col, header)
        cell.fill = PatternFill("solid", fgColor=PRIMARY)
        cell.font = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
        cell.alignment = Alignment(horizontal="center", vertical="center")

    current_row = header_row + 1
    for line in ledger["lines"]:
        invoice = line["row"]
        values = [
            invoice.invoice_date,
            line["doc_type"],
            invoice.invoice_number or invoice.bill_number,
            invoice.lpo_reference or "-",
            money(line["debit"]),
            money(line["credit"]),
            money(line["balance"]),
        ]
        for col, value in enumerate(values, start=1):
            cell = sheet.cell(current_row, col, value)
            if col in {5, 6, 7}:
                cell.number_format = '#,##0.00'
                cell.alignment = Alignment(horizontal="right", vertical="center")
            elif col == 1:
                cell.number_format = "yyyy-mm-dd"
            else:
                cell.alignment = Alignment(vertical="center")
        current_row += 1

    if not ledger["lines"]:
        sheet.merge_cells(start_row=current_row, start_column=1, end_row=current_row, end_column=7)
        sheet.cell(current_row, 1, "No invoice rows found for this statement period.")
        current_row += 1

    totals_start = current_row + 2
    totals = [
        ("Total Debit", ledger["total_debit"]),
        ("Total Credit", ledger["total_credit"]),
        ("Net Value / Total Outstanding", ledger["net_value"]),
        ("Final Balance", ledger["final_balance"]),
    ]
    for index, (label, value) in enumerate(totals, start=totals_start):
        sheet.cell(index, 5, label)
        sheet.cell(index, 6, money(value))
        sheet.cell(index, 6).number_format = '#,##0.00'
        sheet.cell(index, 5).font = Font(name="Calibri", size=10, bold=True, color=TEXT)
        sheet.cell(index, 6).font = Font(name="Calibri", size=10, bold=True, color=TEXT)
        sheet.cell(index, 6).alignment = Alignment(horizontal="right")

    note_row = totals_start + len(totals) + 2
    sheet.merge_cells(start_row=note_row, start_column=1, end_row=note_row + 1, end_column=7)
    sheet.cell(
        note_row,
        1,
        "Please verify this statement and clear the outstanding amount at the earliest. "
        "If payment has recently been made, please accept our thanks and ignore this reminder.",
    )
    sheet.cell(note_row, 1).alignment = Alignment(wrap_text=True, vertical="top")

    apply_common_styles(sheet)
    for col in range(1, 8):
        sheet.column_dimensions[get_column_letter(col)].width = [16, 14, 16, 26, 15, 15, 17][col - 1]
    sheet.freeze_panes = "A9"
    sheet.auto_filter.ref = f"A{header_row}:G{max(header_row, current_row - 1)}"

    for row_number in range(start_row, start_row + len(info_rows)):
        sheet.cell(row_number, 1).fill = PatternFill("solid", fgColor=SOFT)
        sheet.cell(row_number, 4).fill = PatternFill("solid", fgColor=SOFT)
        sheet.cell(row_number, 1).font = Font(name="Calibri", size=10, bold=True, color=TEXT)
        sheet.cell(row_number, 4).font = Font(name="Calibri", size=10, bold=True, color=TEXT)
    for row_number in range(totals_start, totals_start + len(totals)):
        sheet.cell(row_number, 5).fill = PatternFill("solid", fgColor=LIGHT)
        sheet.cell(row_number, 6).fill = PatternFill("solid", fgColor=LIGHT)

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()
