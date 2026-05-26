from io import BytesIO
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from quotations.pdf import _image as quotation_image
    from quotations.pdf_config import get_quotation_pdf_config
except Exception:  # pragma: no cover
    get_quotation_pdf_config = None
    quotation_image = None


TEXT = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#D1D5DB")
LIGHT_BORDER = colors.HexColor("#E5E7EB")
SOFT = colors.HexColor("#F9FAFB")
SUCCESS_SOFT = colors.HexColor("#ECFDF5")


def _text(value, fallback="-"):
    value = "" if value is None else str(value)
    return escape(value.strip() or fallback)


def money(value):
    return f"AED {value or 0:,.2f}"


def short_date(value):
    return value.isoformat() if value else "-"


def company_config():
    if get_quotation_pdf_config:
        try:
            return get_quotation_pdf_config()
        except Exception:
            pass
    return None


def config_value(config, name, fallback=""):
    return getattr(config, name, fallback) if config else fallback


def primary_color(config):
    return colors.HexColor(config_value(config, "primary_color", "#0F766E") or "#0F766E")


def contact_parts(config):
    parts = [
        config_value(config, "address", "Dubai, United Arab Emirates"),
        f"Phone: {config_value(config, 'phone')}" if config_value(config, "phone") else "",
        f"Email: {config_value(config, 'email')}" if config_value(config, "email") else "",
    ]
    return [part for part in parts if part]


def make_styles(config):
    primary = primary_color(config)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandName", parent=styles["Title"], fontSize=17, leading=21, textColor=primary))
    styles.add(ParagraphStyle(name="DocTitle", parent=styles["Title"], fontSize=18, leading=22, alignment=TA_RIGHT, textColor=TEXT))
    styles.add(ParagraphStyle(name="DocSubtitle", parent=styles["Normal"], fontSize=8, leading=11, alignment=TA_RIGHT, textColor=MUTED))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, leading=11, textColor=MUTED))
    styles.add(ParagraphStyle(name="SmallMutedRight", parent=styles["SmallMuted"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="ContactLine", parent=styles["SmallMuted"], fontSize=7.5, leading=9, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=7.6, leading=9.5, textColor=TEXT))
    styles.add(ParagraphStyle(name="CellRight", parent=styles["Cell"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="TableHeader", parent=styles["Normal"], fontSize=7.5, leading=9, textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Normal"], fontSize=9, leading=11, textColor=primary))
    return styles


def logo_flowable(config, max_width=70 * mm, max_height=24 * mm):
    logo_path = config_value(config, "logo_path")
    if quotation_image and logo_path and config_value(config, "logo_layout", "full_logo_only") != "no_logo":
        return quotation_image(logo_path, max_width=max_width, max_height=max_height)
    return ""


def build_header(config, styles, *, subtitle="Overdue Payment Statement", classic=False):
    logo = logo_flowable(config, max_width=52 * mm if classic else 76 * mm, max_height=20 * mm if classic else 26 * mm)
    company_name = config_value(config, "company_name", "Al Ameen Pharmacy")
    left = logo or Paragraph(f"<b>{_text(company_name)}</b>", styles["BrandName"])
    right = [
        Paragraph("<b>STATEMENT OF ACCOUNT</b>", styles["DocTitle"]),
        Paragraph(_text(subtitle), styles["DocSubtitle"]),
    ]
    header = Table([[left, right]], colWidths=[110 * mm, 62 * mm])
    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    contact = contact_parts(config)
    if not contact:
        return header
    contact_line = Table([[Paragraph(" | ".join(_text(part, "") for part in contact), styles["ContactLine"])]], colWidths=[172 * mm])
    contact_line.setStyle(TableStyle([("BOTTOMPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 0)]))
    return Table([[header], [contact_line]], colWidths=[172 * mm])


def footer(canvas, doc):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(doc.leftMargin, 12 * mm, A4[0] - doc.rightMargin, 12 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(A4[0] / 2, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def customer_info_table(import_customer, styles, professional=True):
    data = [
        ["Customer", import_customer.customer_name, "Statement Date", short_date(import_customer.accounting_import.report_date)],
        ["Account No.", import_customer.customer_code or "-", "Currency", "AED"],
        ["Total Outstanding", money(import_customer.total_outstanding), "Overdue > 30 Days", money(import_customer.overdue_amount)],
    ]
    table = Table(data, colWidths=[30 * mm, 68 * mm, 35 * mm, 39 * mm])
    table.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, LIGHT_BORDER if professional else BORDER),
                ("BACKGROUND", (0, 0), (0, -1), SOFT),
                ("BACKGROUND", (2, 0), (2, -1), SOFT),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def invoice_rows(import_customer, styles):
    rows = [[
        Paragraph("Invoice&nbsp;No.", styles["TableHeader"]),
        Paragraph("LPO / Reference No.", styles["TableHeader"]),
        Paragraph("Date", styles["TableHeader"]),
        Paragraph("Amount", styles["TableHeader"]),
        Paragraph("0-30", styles["TableHeader"]),
        Paragraph("30-60", styles["TableHeader"]),
        Paragraph("60-90", styles["TableHeader"]),
        Paragraph("Over 90", styles["TableHeader"]),
        Paragraph("Total", styles["TableHeader"]),
        Paragraph("Days", styles["TableHeader"]),
    ]]
    for invoice in import_customer.invoice_rows.all():
        rows.append(
            [
                Paragraph(_text(invoice.invoice_number or invoice.bill_number), styles["Cell"]),
                Paragraph(_text(invoice.lpo_reference, "-"), styles["Cell"]),
                short_date(invoice.invoice_date),
                Paragraph(money(invoice.amount), styles["CellRight"]),
                Paragraph(money(invoice.bucket_0_30), styles["CellRight"]),
                Paragraph(money(invoice.bucket_30_60), styles["CellRight"]),
                Paragraph(money(invoice.bucket_60_90), styles["CellRight"]),
                Paragraph(money(invoice.bucket_over_90), styles["CellRight"]),
                Paragraph(money(invoice.total), styles["CellRight"]),
                str(invoice.days),
            ]
        )
    return rows


def invoice_table(import_customer, styles, config, *, classic=False):
    widths = [21 * mm, 30 * mm, 16 * mm, 17 * mm, 16 * mm, 16 * mm, 16 * mm, 18 * mm, 18 * mm, 10 * mm]
    table = Table(invoice_rows(import_customer, styles), repeatRows=1, colWidths=widths)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary_color(config)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7 if classic else 7.4),
                ("GRID", (0, 0), (-1, -1), 0.25, BORDER if classic else LIGHT_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (3, 1), (-1, -1), "RIGHT"),
                ("PADDING", (0, 0), (-1, -1), 3.5 if classic else 4),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
            ]
        )
    )
    return table


def totals_table(import_customer, config):
    totals = Table(
        [
            ["Total Outstanding", money(import_customer.total_outstanding)],
            ["Overdue > 30 Days", money(import_customer.overdue_amount)],
            ["0-30", money(import_customer.bucket_0_30)],
            ["30-60", money(import_customer.bucket_30_60)],
            ["60-90", money(import_customer.bucket_60_90)],
            ["Over 90", money(import_customer.bucket_over_90)],
        ],
        colWidths=[48 * mm, 40 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 1), (-1, 1), SUCCESS_SOFT),
                ("TEXTCOLOR", (0, 1), (-1, 1), primary_color(config)),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return totals


def note_block(styles, professional=True):
    text = (
        "Please verify this statement and clear the outstanding amount at the earliest. "
        "If payment has recently been made, please accept our thanks and ignore this reminder."
    )
    if professional:
        text = (
            "<b>Payment Reminder</b><br/>"
            "Please find attached the statement of account for your company. Kindly verify the statement "
            "and clear the outstanding amount at the earliest to keep in line with the agreed payment terms. "
            "If payment has recently been made, please accept our thanks and ignore this reminder."
        )
    return Paragraph(text, styles["SmallMuted"])


def build_classic_statement_pdf(import_customer):
    config = company_config()
    styles = make_styles(config)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=12 * mm,
        leftMargin=12 * mm,
        topMargin=12 * mm,
        bottomMargin=16 * mm,
        title=f"Statement - {import_customer.customer_name}",
    )
    story = [
        build_header(config, styles, classic=True),
        customer_info_table(import_customer, styles, professional=False),
        Spacer(1, 8),
        invoice_table(import_customer, styles, config, classic=True),
        Spacer(1, 8),
        totals_table(import_customer, config),
        Spacer(1, 10),
        note_block(styles, professional=False),
    ]
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def build_professional_statement_pdf(import_customer):
    config = company_config()
    styles = make_styles(config)
    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=16 * mm,
        leftMargin=16 * mm,
        topMargin=14 * mm,
        bottomMargin=18 * mm,
        title=f"Statement - {import_customer.customer_name}",
    )
    story = [
        build_header(config, styles),
        customer_info_table(import_customer, styles, professional=True),
        Spacer(1, 10),
        invoice_table(import_customer, styles, config),
        Spacer(1, 10),
        totals_table(import_customer, config),
        Spacer(1, 12),
        note_block(styles, professional=True),
    ]
    document.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def build_statement_pdf(import_customer, style="professional"):
    if style == "classic":
        return build_classic_statement_pdf(import_customer)
    return build_professional_statement_pdf(import_customer)
