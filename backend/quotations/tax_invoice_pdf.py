"""Tax invoices with the website branding, full billing details and payment instructions."""
from decimal import Decimal
from io import BytesIO
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import BaseDocTemplate, Frame, HRFlowable, KeepTogether, PageTemplate, Paragraph, Spacer, Table, TableStyle

from .pdf import _footer, _image, _number, _pdf_styles, _text
from .pdf_config import get_quotation_pdf_config

TEAL = colors.HexColor("#0F766E")
INK = colors.HexColor("#153B38")
GOLD = colors.HexColor("#CDA25A")
BORDER = colors.HexColor("#CBD5D1")
LOGO_PATH = Path(__file__).with_name("assets") / "al-ameen-website-logo.png"
BANK_DETAILS = {
    "bank_name": "RAK Bank",
    "account_name": "AL AMEEN PHARMACY LLC",
    "account_number": "0025346405061",
    "iban": "AE440400000025346405061",
    "cheque_payee": "AL AMEEN PHARMACY LLC",
}


def build_tax_invoice_pdf(invoice, *, config=None):
    config = config or get_quotation_pdf_config(include_hidden_trn=True)
    styles = _pdf_styles(TEAL)
    for name in ("TableHeader", "TableCell", "TableCellRight", "TableCellCenter", "MetaLabel", "MetaValue"):
        styles[name].fontSize = 9
        styles[name].leading = 12
        styles[name].textColor = INK
    styles["TableHeader"].fontSize = 8
    styles["TableHeader"].leading = 10
    styles.add(ParagraphStyle(name="InvoiceTitle", fontName="Helvetica-Bold", fontSize=20, leading=24,
                              textColor=TEAL, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="InvoiceSection", fontName="Helvetica-Bold", fontSize=8, leading=11,
                              textColor=TEAL, spaceAfter=5))
    styles.add(ParagraphStyle(name="InvoiceCustomer", fontName="Helvetica-Bold", fontSize=10.5, leading=14, textColor=INK))
    styles.add(ParagraphStyle(name="InvoiceStrong", parent=styles["TableCell"], fontName="Helvetica-Bold"))
    styles.add(ParagraphStyle(name="InvoiceTotal", parent=styles["InvoiceStrong"], fontSize=11, leading=14, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="InvoiceSmall", parent=styles["SmallMuted"], fontSize=8.2, leading=11))
    styles.add(ParagraphStyle(name="InvoiceSupplier", parent=styles["InvoiceSmall"], alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="InvoiceBrand", parent=styles["InvoiceCustomer"], alignment=TA_CENTER))
    buffer = BytesIO()
    number = invoice.invoice_number or "Not entered"
    draft = invoice.status != "issued"
    width = 182 * mm
    doc = BaseDocTemplate(buffer, pagesize=A4, invariant=1, leftMargin=14 * mm, rightMargin=14 * mm,
                          topMargin=12 * mm, bottomMargin=18 * mm, title=f"Tax Invoice {number}")

    def cell(value, style="TableCell"):
        return Paragraph(_text(value, "").replace("\n", "<br/>"), styles[style])

    def numeric_cell(value, column_mm, style="TableCellRight"):
        # Keep an amount on one line, including unusually large invoices. A
        # wrapped last digit would make the printed amount easy to misread.
        base = styles[style]
        available = column_mm * mm - 8
        measured = stringWidth(str(value), base.fontName, base.fontSize)
        fitted = ParagraphStyle(name="InvoiceNumber", parent=base,
                                fontSize=base.fontSize * min(1, available / max(1, measured)))
        return Paragraph(_text(value, ""), fitted)

    def grid(rows, widths, *, padding=6, borders=False, **kwargs):
        table = Table(rows, colWidths=widths, hAlign="LEFT", **kwargs)
        commands = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                    ("LEFTPADDING", (0, 0), (-1, -1), padding), ("RIGHTPADDING", (0, 0), (-1, -1), padding),
                    ("TOPPADDING", (0, 0), (-1, -1), padding), ("BOTTOMPADDING", (0, 0), (-1, -1), padding)]
        if borders:
            commands += [("BOX", (0, 0), (-1, -1), .4, BORDER), ("INNERGRID", (0, 0), (-1, -1), .25, BORDER)]
        table.setStyle(TableStyle(commands))
        return table

    # Ship the actual website artwork with the backend, so a backend-only
    # deployment does not need a frontend folder or a remote logo download.
    logo = _image(str(LOGO_PATH), max_width=74 * mm, max_height=27 * mm)
    if logo:
        logo.hAlign = "CENTER"
    brand = logo or cell(config.company_name, "InvoiceBrand")
    supplier = [config.address,
                f"Tel: {config.phone}" if config.phone else "", config.email,
                f"TRN: {config.trn or 'Not configured'}"]
    title = [cell("TAX INVOICE", "InvoiceTitle")]
    if draft:
        title += [Spacer(1, 4), cell("DRAFT - NOT ISSUED", "SmallMutedRight")]
    header_row = grid([["", brand, title]], [54 * mm, 74 * mm, 54 * mm], padding=0)
    header_row.setStyle(TableStyle([("ALIGN", (1, 0), (1, 0), "CENTER")]))
    supplier_line = " | ".join(" ".join(str(part).split()) for part in supplier if part)
    header = [header_row, Spacer(1, 8), cell(supplier_line, "InvoiceSupplier")]
    elements = [KeepTogether(header), Spacer(1, 10),
                HRFlowable(width=width, thickness=1.2, color=GOLD), Spacer(1, 12)]

    customer = [cell("BILL TO", "InvoiceSection"), cell(invoice.customer_name, "InvoiceCustomer"), Spacer(1, 5),
                cell(invoice.customer_address), Spacer(1, 7), cell(f"Customer TRN: {invoice.customer_trn or 'Not entered'}", "InvoiceStrong")]
    if invoice.attention:
        customer.extend([Spacer(1, 5), cell(f"Attention: {invoice.attention}")])
    metadata = [("Invoice no.", number), ("Invoice date", invoice.invoice_date.strftime("%d/%m/%Y")),
                ("Supply date", invoice.supply_date.strftime("%d/%m/%Y")),
                ("Quotation ref.", invoice.quotation_reference), ("LPO no.", invoice.lpo_number)]
    reference = grid([[cell(label, "MetaLabel"), cell(value, "InvoiceStrong" if label == "Invoice no." else "MetaValue")]
                      for label, value in metadata if value], [25 * mm, 43 * mm], padding=3)
    parties = grid([[customer, reference]], [108 * mm, 74 * mm], padding=8, borders=True)
    elements.extend([parties, Spacer(1, 14), cell("All amounts in AED. Unit prices exclude VAT.", "InvoiceSmall"), Spacer(1, 5)])

    headers = ["No.", "Item description", "Qty", "Unit", "Unit price", "Discount", "VAT %", "VAT", "Amount"]
    data = [[cell(value, "TableHeader") for value in headers]]
    lines = list(invoice.lines.all())
    for index, line in enumerate(lines, 1):
        description = [cell(line.item_name, "InvoiceStrong")]
        if line.description and line.description != line.item_name:
            description.extend([Spacer(1, 3), cell(line.description, "InvoiceSmall")])
        price = f"{line.unit_price:,.3f}".rstrip("0").rstrip(".") if line.unit_price is not None else "Missing"
        if line.unit_price is not None and line.unit_price == line.unit_price.quantize(Decimal(".01")):
            price = f"{line.unit_price:,.2f}"
        data.append([numeric_cell(index, 9, "TableCellCenter"), description, numeric_cell(_number(line.quantity), 12),
                     cell(line.unit, "TableCellCenter"), numeric_cell(price, 20), numeric_cell(f"{line.discount:,.2f}", 18),
                     numeric_cell(_number(line.vat_rate) if line.vat_rate is not None else "?", 12),
                     numeric_cell(f"{line.vat_amount:,.2f}", 18), numeric_cell(f"{line.line_total:,.2f}", 21)])
    items = grid(data, [9 * mm, 59 * mm, 12 * mm, 13 * mm, 20 * mm, 18 * mm, 12 * mm, 18 * mm, 21 * mm],
                 padding=4, borders=True, repeatRows=1, splitByRow=1, splitInRow=1)
    items.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), .8, TEAL), ("LINEBELOW", (0, 0), (-1, 0), .8, TEAL),
                               ("TOPPADDING", (0, 1), (-1, -1), 7), ("BOTTOMPADDING", (0, 1), (-1, -1), 7)]))
    elements.extend([items, Spacer(1, 12)])

    zero_rated = sum((line.line_subtotal for line in lines if line.vat_rate == 0), Decimal(0))
    standard_rated = sum((line.line_subtotal for line in lines if line.vat_rate == 5), Decimal(0))
    tax_summary = grid([[cell("Taxable at 0%", "InvoiceSmall"), cell(f"AED {zero_rated:,.2f}")],
                        [cell("Taxable at 5%", "InvoiceSmall"), cell(f"AED {standard_rated:,.2f}")]],
                       [34 * mm, 45 * mm], padding=2)
    totals = [("Total sales", invoice.subtotal + invoice.discount_total), ("Total discount", invoice.discount_total),
              ("Net before VAT", invoice.subtotal), ("VAT total", invoice.vat_total), ("Total payable", invoice.total)]
    total_table = grid([[cell(label, "InvoiceStrong" if index == 4 else "TableCell"),
                        cell(f"AED {value:,.2f}", "InvoiceTotal" if index == 4 else "TableCellRight")]
                       for index, (label, value) in enumerate(totals)], [35 * mm, 40 * mm], padding=5)
    total_table.setStyle(TableStyle([("LINEABOVE", (0, -1), (-1, -1), .9, TEAL), ("LINEBELOW", (0, -1), (-1, -1), .9, TEAL)]))

    bank = invoice.supplier_snapshot.get("bank_details") or BANK_DETAILS
    bank_rows = [("Bank name", bank["bank_name"]), ("Account name", bank["account_name"]),
                 ("Account no.", bank["account_number"]), ("IBAN", bank["iban"])]
    bank_table = grid([[cell(label, "InvoiceSmall"), cell(value, "InvoiceStrong")] for label, value in bank_rows],
                      [25 * mm, 73 * mm], padding=2)
    payment = [tax_summary, Spacer(1, 12), cell("BANK DETAILS", "InvoiceSection"), bank_table, Spacer(1, 7),
               cell(f'Cheques payable to "{bank["cheque_payee"]}".', "InvoiceSmall")]
    settlement = grid([[payment, total_table]], [107 * mm, 75 * mm], padding=0)
    elements.append(KeepTogether([settlement]))
    if invoice.notes:
        elements.extend([Spacer(1, 12), cell("NOTES", "InvoiceSection"), cell(invoice.notes)])
    signatures = grid([[cell("Customer seal / signature", "InvoiceSmall"), cell("Checked by", "InvoiceSmall"),
                        cell("For AL AMEEN PHARMACY LLC\nAuthorised signatory", "InvoiceSmall")]],
                      [69 * mm, 44 * mm, 69 * mm], padding=4)
    signatures.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), .4, BORDER)]))
    elements.append(KeepTogether([Spacer(1, 36), signatures]))

    def footer(canvas, document):
        _footer(canvas, document)
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(INK)
        reference_text = f"Invoice {number}"
        if stringWidth(reference_text, "Helvetica", 8) > document.width * .44:
            while stringWidth(reference_text + "...", "Helvetica", 8) > document.width * .44:
                reference_text = reference_text[:-1]
            reference_text += "..."
        canvas.drawString(document.leftMargin, 8 * mm, reference_text)
        if draft:
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(40)
            canvas.setFillColor(colors.Color(.6, .6, .6, alpha=.12))
            canvas.setFont("Helvetica-Bold", 65)
            canvas.drawCentredString(0, 0, "DRAFT")
        canvas.restoreState()

    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height,
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates(PageTemplate(id="Invoice", frames=[frame], onPage=footer))
    doc.build(elements)
    return buffer.getvalue()
