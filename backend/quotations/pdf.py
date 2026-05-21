from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


def build_quotation_pdf(quotation):
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=quotation.quotation_number,
    )
    styles = getSampleStyleSheet()
    elements = []

    elements.append(Paragraph("AL AMEEN PHARMACY", styles["Title"]))
    elements.append(Paragraph(f"Quotation {quotation.quotation_number}", styles["Heading2"]))
    elements.append(Spacer(1, 8))

    company_lines = [
        f"<b>Company:</b> {quotation.company.name}",
        f"<b>Contact:</b> {quotation.contact.name if quotation.contact else '-'}",
        f"<b>Status:</b> {quotation.get_status_display()}",
        f"<b>Currency:</b> {quotation.currency}",
    ]
    if quotation.valid_until:
        company_lines.append(f"<b>Valid Until:</b> {quotation.valid_until}")
    elements.append(Paragraph("<br/>".join(company_lines), styles["Normal"]))
    elements.append(Spacer(1, 10))

    table_data = [["#", "Item", "Qty", "Unit", "Unit Price", "VAT", "Total"]]
    for index, line in enumerate(quotation.lines.order_by("sort_order", "id"), start=1):
        table_data.append(
            [
                str(index),
                line.item_name_snapshot,
                f"{line.quantity:g}",
                line.unit or "-",
                f"{quotation.currency} {line.unit_price or 0:.2f}",
                f"{line.vat_amount:.2f}",
                f"{quotation.currency} {line.line_total:.2f}",
            ]
        )

    table = Table(table_data, colWidths=[10 * mm, 72 * mm, 18 * mm, 18 * mm, 28 * mm, 22 * mm, 28 * mm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0F766E")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F9FAFB")]),
            ]
        )
    )
    elements.append(table)
    elements.append(Spacer(1, 10))

    totals = [
        ["Subtotal", f"{quotation.currency} {quotation.subtotal:.2f}"],
        ["VAT", f"{quotation.currency} {quotation.vat_total:.2f}"],
        ["Total", f"{quotation.currency} {quotation.total:.2f}"],
    ]
    totals_table = Table(totals, colWidths=[35 * mm, 35 * mm], hAlign="RIGHT")
    totals_table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#D1D5DB")),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#ECFDF5")),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
            ]
        )
    )
    elements.append(totals_table)

    if quotation.notes:
        elements.append(Spacer(1, 12))
        elements.append(Paragraph("<b>Notes</b>", styles["Heading4"]))
        elements.append(Paragraph(quotation.notes, styles["Normal"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer.getvalue()
