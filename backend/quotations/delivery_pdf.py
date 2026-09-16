from io import BytesIO
from dataclasses import replace

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import DeliveryNote
from .pdf import (
    LIGHT_BORDER, MUTED, _build_header, _footer, _number, _pdf_styles,
    _text,
)
from .pdf_config import get_quotation_pdf_config


def build_delivery_note_pdf(note):
    """Use the quotation's branding, without commercial prices or invoice totals."""
    config = get_quotation_pdf_config(quotation=note.quotation, include_hidden_trn=True)
    primary = colors.HexColor(config.primary_color or "#0F766E")
    styles = _pdf_styles(primary)
    # Delivery notes are read and checked on paper; keep quotation styling separate.
    for name in ("TableHeader", "TableCell", "TableCellCenter", "TableCellQuantity", "TableCellUnit"):
        styles[name].fontSize = 11
        styles[name].leading = 14
    styles["TableHeader"].textColor = colors.black
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, invariant=1, leftMargin=16 * mm, rightMargin=16 * mm,
        topMargin=14 * mm, bottomMargin=18 * mm, title=note.delivery_number,
    )
    elements = [_build_header(
        replace(config, trn=""), note, note.delivery_date.strftime("%d/%m/%Y"), styles,
        document_title="DELIVERY NOTE", reference_label="DN No", reference_number=note.delivery_number,
    )]
    metadata = [
        ("Customer", note.customer_name), ("Pharmacy TRN", config.trn),
        ("LPO No.", note.lpo_number),
        ("Customer address", note.customer_address), ("Customer TRN", note.customer_trn),
        ("Deliver to", note.delivery_address),
        ("Attention", note.attention), ("Invoice Ref.", note.invoice_number),
        ("Contact No.", note.contact_phone),
        ("Quotation Ref.", note.quotation.quotation_number if note.quotation else note.quotation_reference),
    ]
    metadata = [(label, value) for label, value in metadata if value]
    rows = []
    for index in range(0, len(metadata), 2):
        pair = metadata[index:index + 2]
        if len(pair) == 1:
            pair.append(("", ""))
        rows.append([cell for label, value in pair for cell in (
            Paragraph(_text(label, ""), styles["MetaLabel"]),
            Paragraph(_text(value, "").replace("\n", "<br/>"), styles["MetaValue"]),
        )])
    meta = Table(rows, colWidths=[25 * mm, 64 * mm, 25 * mm, 64 * mm])
    meta.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.3, LIGHT_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
    ]))
    elements.extend([meta, Spacer(1, 8)])
    received = note.status == DeliveryNote.STATUS_DELIVERED
    show_expiry = note.show_expiry_column

    def quantity_cell(value, width_mm):
        text = _number(value)
        style = styles["TableCellQuantity"].clone("DeliveryQuantity")
        natural_width = stringWidth(text, style.fontName, style.fontSize)
        style.fontSize *= min(1, (width_mm * mm - 12) / max(natural_width, 1))
        return Paragraph(_text(text), style)

    styles["TableCellUnit"].splitLongWords = True
    headings = ["No.", "Item description", "Quantity", "UOM"] + (["Expiry"] if show_expiry else []) + (["Received"] if received else [])
    rows = [[Paragraph(label, styles["TableHeader"]) for label in headings]]
    for index, line in enumerate((line for line in note.lines.all() if not line.deliver_later), 1):
        description = f"<b>{_text(line.item_name)}</b>"
        if line.description and line.description.strip() != line.item_name.strip():
            description += f"<br/>{_text(line.description).replace(chr(10), '<br/>')}"
        row = [
            Paragraph(str(index), styles["TableCellCenter"]), Paragraph(description, styles["TableCell"]),
            quantity_cell(line.quantity, 26),
            Paragraph(_text(line.unit or "-"), styles["TableCellUnit"]),
        ]
        if show_expiry:
            row.append(Paragraph(_text(line.expiry or "-"), styles["TableCellCenter"]))
        if received:
            row.append(quantity_cell(line.received_quantity, 24))
        rows.append(row)
    widths = [10, 119 - (28 if show_expiry else 0) - (24 if received else 0), 26, 23]
    if show_expiry:
        widths.append(28)
    if received:
        widths.append(24)
    table = Table(rows, colWidths=[width * mm for width in widths], repeatRows=1, splitByRow=1, splitInRow=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#9CA3AF")),
        ("LEFTPADDING", (0, 0), (0, -1), 3), ("RIGHTPADDING", (0, 0), (0, -1), 3),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(table)
    for label, value in [("Delivery instructions", note.notes), ("Receipt notes", note.receipt_notes), ("Cancellation reason", note.cancellation_reason)]:
        if value:
            elements.extend([Spacer(1, 10), Paragraph(label, styles["NotesTitle"]), Paragraph(_text(value).replace("\n", "<br/>"), styles["Small"])])
    receipt = []
    if received:
        receipt.append(Spacer(1, 8))
        receipt.append(Paragraph(
            f"Recorded receipt: {_text(note.received_by)} | {note.received_date:%d/%m/%Y}"
            + (f" | Reference: {_text(note.receipt_reference)}" if note.receipt_reference else ""), styles["Small"],
        ))
    receipt.extend([
        Spacer(1, 4), Paragraph("Acknowledgement of receipt", styles["SectionTitle"]),
        Paragraph("Please check the quantities and condition of the goods. Record any shortages or damage before signing.", styles["Small"]),
        Spacer(1, 6),
    ])
    signatures = Table([
        [Paragraph(label, styles["MetaLabel"]) for label in ["Received by / customer stamp", "Checked by", "Delivered by"]],
        ["", "", ""],
        [Paragraph("Signature / date", styles["SmallMuted"]), "", ""],
    ], colWidths=[78 * mm, 50 * mm, 50 * mm], minRowHeights=[0, 14 * mm, 0])
    signatures.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.35, LIGHT_BORDER),
        ("LINEAFTER", (0, 0), (1, -1), 0.35, LIGHT_BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, 0), 8), ("BOTTOMPADDING", (0, -1), (-1, -1), 8),
    ]))
    receipt.append(signatures)
    elements.append(KeepTogether(receipt))

    def footer(canvas, document):
        _footer(canvas, document)
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(document.leftMargin, 8 * mm, note.delivery_number)
        if note.status in {DeliveryNote.STATUS_DRAFT, DeliveryNote.STATUS_CANCELLED}:
            label = "DRAFT" if note.status == DeliveryNote.STATUS_DRAFT else "CANCELLED"
            canvas.drawRightString(A4[0] - document.rightMargin, 8 * mm, label)
            canvas.setFillAlpha(0.1)
            canvas.setFont("Helvetica-Bold", 62)
            canvas.translate(A4[0] / 2, A4[1] / 2)
            canvas.rotate(35)
            canvas.drawCentredString(0, 0, "DRAFT" if note.status == DeliveryNote.STATUS_DRAFT else "CANCELLED")
        canvas.restoreState()

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()
