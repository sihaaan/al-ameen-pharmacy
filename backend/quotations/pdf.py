from datetime import timedelta
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .pdf_config import get_quotation_pdf_config


PRIMARY = colors.HexColor("#0F766E")
TEXT = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#D1D5DB")
SOFT = colors.HexColor("#F9FAFB")
SUCCESS_SOFT = colors.HexColor("#ECFDF5")


def _text(value, fallback="-"):
    value = "" if value is None else str(value)
    return escape(value.strip() or fallback)


def _money(currency, value):
    return f"{currency} {value or 0:.2f}"


def _number(value):
    return f"{value:g}" if value is not None else "-"


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


def _logo(path, max_width=42 * mm, max_height=18 * mm):
    if not path:
        return ""
    logo_path = Path(path)
    if not logo_path.exists():
        return ""
    try:
        image_width, image_height = ImageReader(str(logo_path)).getSize()
        scale = min(max_width / image_width, max_height / image_height)
        return Image(str(logo_path), width=image_width * scale, height=image_height * scale)
    except Exception:
        return ""


def _footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(doc.leftMargin, 12 * mm, A4[0] - doc.rightMargin, 12 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(A4[0] / 2, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_quotation_pdf(quotation):
    config = get_quotation_pdf_config()
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=quotation.quotation_number,
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandName", parent=styles["Title"], fontSize=18, leading=22, textColor=PRIMARY))
    styles.add(ParagraphStyle(name="BrandArabic", parent=styles["Normal"], fontSize=10, leading=13, textColor=MUTED))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, leading=11, textColor=MUTED))
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=8, leading=11, textColor=TEXT))
    styles.add(ParagraphStyle(name="TableHeader", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="TableCell", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=TEXT))
    styles.add(ParagraphStyle(name="TableCellRight", parent=styles["TableCell"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading4"], fontSize=10, leading=12, textColor=PRIMARY))

    elements = []
    quote_date = _local_date(quotation.created_at)

    brand_lines = [
        Paragraph(_text(config.company_name, "Al Ameen Pharmacy"), styles["BrandName"]),
    ]
    if config.company_name_ar:
        brand_lines.append(Paragraph(_text(config.company_name_ar, ""), styles["BrandArabic"]))
    contact_parts = [
        part
        for part in [
            _text(config.address, ""),
            f"Phone: {_text(config.phone, '')}" if config.phone else "",
            f"Email: {_text(config.email, '')}" if config.email else "",
            f"TRN/License: {_text(config.trn, '')}" if config.trn else "",
        ]
        if part
    ]
    if contact_parts:
        brand_lines.append(Paragraph("<br/>".join(contact_parts), styles["SmallMuted"]))

    header = Table(
        [[_logo(config.logo_path), brand_lines, Paragraph("<b>QUOTATION</b>", ParagraphStyle(name="QuoteTitle", parent=styles["Title"], alignment=TA_RIGHT, textColor=TEXT))]],
        colWidths=[44 * mm, 86 * mm, 48 * mm],
    )
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    elements.append(header)

    meta_rows = [
        ["Quotation #", _text(quotation.quotation_number), "Date", _text(quote_date)],
        ["Customer", _text(quotation.company.name), "Status", _text(quotation.get_status_display())],
        ["Contact", _text(quotation.contact.name if quotation.contact else ""), "Currency", _text(quotation.currency)],
        ["Valid Until", _text(_valid_until(quotation, config)), "Prepared By", _text(quotation.created_by.username if quotation.created_by else "")],
    ]
    meta_table = Table(meta_rows, colWidths=[24 * mm, 66 * mm, 24 * mm, 64 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("TEXTCOLOR", (2, 0), (2, -1), MUTED),
                ("FONTSIZE", (0, 0), (-1, -1), 8.5),
                ("LEADING", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(meta_table)
    elements.append(Spacer(1, 8))

    table_data = [
        [
            Paragraph("#", styles["TableHeader"]),
            Paragraph("Item Description", styles["TableHeader"]),
            Paragraph("Qty", styles["TableHeader"]),
            Paragraph("Unit", styles["TableHeader"]),
            Paragraph("Unit Price", styles["TableHeader"]),
            Paragraph("VAT", styles["TableHeader"]),
            Paragraph("Total", styles["TableHeader"]),
        ]
    ]
    for index, line in enumerate(quotation.lines.order_by("sort_order", "id"), start=1):
        item_text = _text(line.item_name_snapshot)
        details = []
        if line.description:
            details.append(_text(line.description, ""))
        if line.notes:
            details.append(f"<font color='#6B7280'>{_text(line.notes, '')}</font>")
        if details:
            item_text = f"{item_text}<br/>{'<br/>'.join(details)}"
        table_data.append(
            [
                Paragraph(str(index), styles["TableCell"]),
                Paragraph(item_text, styles["TableCell"]),
                Paragraph(_number(line.quantity), styles["TableCellRight"]),
                Paragraph(_text(line.unit), styles["TableCell"]),
                Paragraph(_money(quotation.currency, line.unit_price), styles["TableCellRight"]),
                Paragraph(_money(quotation.currency, line.vat_amount), styles["TableCellRight"]),
                Paragraph(_money(quotation.currency, line.line_total), styles["TableCellRight"]),
            ]
        )

    line_table = Table(
        table_data,
        colWidths=[8 * mm, 74 * mm, 16 * mm, 16 * mm, 24 * mm, 18 * mm, 22 * mm],
        repeatRows=1,
    )
    line_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
                ("GRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(line_table)
    elements.append(Spacer(1, 10))

    totals_table = Table(
        [
            ["Subtotal", _money(quotation.currency, quotation.subtotal)],
            ["VAT", _money(quotation.currency, quotation.vat_total)],
            ["Total", _money(quotation.currency, quotation.total)],
        ],
        colWidths=[34 * mm, 36 * mm],
        hAlign="RIGHT",
    )
    totals_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("FONTNAME", (0, 0), (-1, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, -1), (-1, -1), SUCCESS_SOFT),
                ("TEXTCOLOR", (0, -1), (-1, -1), PRIMARY),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(totals_table)

    if quotation.notes:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph("Notes", styles["SectionTitle"]))
        elements.append(Paragraph(_text(quotation.notes), styles["Small"]))

    elements.append(Spacer(1, 10))
    footer_data = [
        [
            [
                Paragraph("Terms and Conditions", styles["SectionTitle"]),
                Paragraph(_text(config.default_terms), styles["Small"]),
                Spacer(1, 4),
                Paragraph(f"<b>Validity:</b> {config.validity_days} days from quotation date unless otherwise stated.", styles["Small"]),
                Paragraph(f"<b>Payment Terms:</b> {_text(config.payment_terms)}", styles["Small"]),
            ],
            [
                Paragraph("Prepared / Approved By", styles["SectionTitle"]),
                Spacer(1, 20),
                Paragraph("Signature / Stamp", ParagraphStyle(name="Signature", parent=styles["SmallMuted"], alignment=TA_CENTER)),
            ],
        ]
    ]
    footer_table = Table(footer_data, colWidths=[112 * mm, 60 * mm])
    footer_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BACKGROUND", (0, 0), (-1, -1), SOFT),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    elements.append(footer_table)

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer.getvalue()
