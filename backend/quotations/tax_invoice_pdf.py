"""Print-friendly tax invoice, sharing the pharmacy's quotation branding."""
from dataclasses import replace
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf import LIGHT_BORDER, _build_header, _footer, _number, _pdf_styles, _text
from .pdf_config import get_quotation_pdf_config


def build_tax_invoice_pdf(invoice, *, config=None):
    config = config or get_quotation_pdf_config(include_hidden_trn=True)
    styles = _pdf_styles(colors.HexColor(config.primary_color or "#0F766E"))
    for name in ("TableHeader", "TableCell", "TableCellRight", "TableCellCenter", "MetaLabel", "MetaValue"):
        styles[name].fontSize = 9.5
        styles[name].leading = 12
    styles["TableHeader"].textColor = colors.black
    buffer = BytesIO()
    number = invoice.invoice_number or f"DRAFT-{invoice.pk}"
    doc = SimpleDocTemplate(buffer, pagesize=A4, invariant=1, leftMargin=16 * mm, rightMargin=16 * mm,
                            topMargin=14 * mm, bottomMargin=18 * mm, title=number)

    def cell(value, style="TableCell"):
        return Paragraph(_text(value, "").replace("\n", "<br/>"), styles[style])

    elements = [_build_header(replace(config, trn=""), invoice, invoice.invoice_date.strftime("%d/%m/%Y"), styles,
                             document_title="TAX INVOICE" if invoice.status == "issued" else "DRAFT TAX INVOICE",
                             reference_label="Invoice No", reference_number=number)]
    # Explicit supplier details remain visible even when the logo contains only a brand name.
    elements.extend([cell(f"{config.company_name}\n{config.address}\nPharmacy TRN: {config.trn or 'Not configured'}"), Spacer(1, 10)])
    metadata = [("Customer", invoice.customer_name), ("Address", invoice.customer_address),
                ("Customer TRN", invoice.customer_trn or "Not provided"), ("Supply date", invoice.supply_date.strftime("%d/%m/%Y")),
                ("Attention", invoice.attention), ("Quotation ref.", invoice.quotation_reference),
                ("LPO number", invoice.lpo_number), ("Currency", "AED")]
    meta = Table([[cell(label, "MetaLabel"), cell(value, "MetaValue")] for label, value in metadata if value],
                 colWidths=[32 * mm, 146 * mm])
    table_style = [("GRID", (0, 0), (-1, -1), .3, LIGHT_BORDER), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                   ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                   ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]
    meta.setStyle(TableStyle(table_style))
    elements.extend([meta, Spacer(1, 12), cell("All amounts in AED. Unit prices exclude VAT.", "SmallMuted"), Spacer(1, 5)])
    headers = ["#", "Item description", "Qty / Unit", "Unit price", "VAT %", "VAT", "Total"]
    data = [[cell(value, "TableHeader") for value in headers]]
    for index, line in enumerate(invoice.lines.all(), 1):
        description = line.item_name
        if line.description and line.description != line.item_name:
            description += "\n" + line.description
        if line.discount:
            description += f"\nDiscount: AED {line.discount:,.2f}"
        data.append([cell(index, "TableCellCenter"), cell(description), cell(f"{_number(line.quantity)}\n{line.unit}", "TableCellRight"),
                     cell(_number(line.unit_price) if line.unit_price is not None else "Missing", "TableCellRight"),
                     cell(_number(line.vat_rate) if line.vat_rate is not None else "?", "TableCellRight"),
                     cell(f"{line.vat_amount:,.2f}", "TableCellRight"), cell(f"{line.line_total:,.2f}", "TableCellRight")])
    table = Table(data, colWidths=[8 * mm, 64 * mm, 21 * mm, 24 * mm, 14 * mm, 21 * mm, 26 * mm], repeatRows=1, splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle(table_style + [("LINEBELOW", (0, 0), (-1, 0), .8, colors.black)]))
    elements.append(table)
    totals = [("Before discount", invoice.subtotal + invoice.discount_total)]
    if invoice.discount_total:
        totals.append(("Discount", invoice.discount_total))
    totals.extend([("Taxable subtotal", invoice.subtotal), ("VAT", invoice.vat_total), ("Total payable", invoice.total)])
    total_table = Table([[cell(label), cell(f"AED {amount:,.2f}", "TableCellRight")] for label, amount in totals],
                        colWidths=[43 * mm, 40 * mm], hAlign="RIGHT")
    total_table.setStyle(TableStyle(table_style + [("LINEABOVE", (0, -1), (-1, -1), .8, colors.black)]))
    elements.append(KeepTogether([Spacer(1, 12), total_table]))
    if invoice.notes:
        elements.extend([Spacer(1, 12), cell("Notes", "NotesTitle"), cell(invoice.notes)])

    def footer(canvas, document):
        _footer(canvas, document)
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.drawString(document.leftMargin, 8 * mm, number)
        if invoice.status != "issued":
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(40)
            canvas.setFillColor(colors.Color(.6, .6, .6, alpha=.18))
            canvas.setFont("Helvetica-Bold", 65)
            canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
