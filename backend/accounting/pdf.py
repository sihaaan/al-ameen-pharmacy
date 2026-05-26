from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

try:
    from quotations.pdf_config import get_quotation_pdf_config
except Exception:  # pragma: no cover
    get_quotation_pdf_config = None


def money(value):
    return f"AED {value:,.2f}"


def short_date(value):
    return value.isoformat() if value else "-"


def company_config():
    if get_quotation_pdf_config:
        try:
            return get_quotation_pdf_config()
        except Exception:
            pass
    return None


def build_statement_pdf(import_customer):
    config = company_config()
    company_name = getattr(config, "company_name", "Al Ameen Pharmacy")
    address = getattr(config, "address", "Dubai, United Arab Emirates")
    phone = getattr(config, "phone", "")
    email = getattr(config, "email", "")
    primary_color = getattr(config, "primary_color", "#00796B")

    buffer = BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=14 * mm,
        leftMargin=14 * mm,
        topMargin=14 * mm,
        bottomMargin=14 * mm,
        title=f"Statement - {import_customer.customer_name}",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="RightSmall", parent=styles["Normal"], alignment=TA_RIGHT, fontSize=8))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, textColor=colors.HexColor("#4b5563")))
    styles.add(ParagraphStyle(name="Cell", parent=styles["Normal"], fontSize=8, leading=10))
    styles.add(ParagraphStyle(name="CellRight", parent=styles["Cell"], alignment=TA_RIGHT))

    story = []
    header = Table(
        [
            [
                Paragraph(f"<b>{company_name}</b><br/>{address}<br/>Phone: {phone}<br/>Email: {email}", styles["Normal"]),
                Paragraph("<b>STATEMENT OF ACCOUNT</b><br/>Overdue Payment Statement", styles["RightSmall"]),
            ]
        ],
        colWidths=[118 * mm, 54 * mm],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story.append(header)
    story.append(Spacer(1, 8))

    meta = Table(
        [
            ["Customer", import_customer.customer_name, "Report Date", short_date(import_customer.accounting_import.report_date)],
            ["Customer Code", import_customer.customer_code or "-", "Category", import_customer.get_category_display()],
            ["Email", import_customer.email or "Email missing", "Max Days", str(import_customer.max_days)],
        ],
        colWidths=[28 * mm, 82 * mm, 28 * mm, 34 * mm],
    )
    meta.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f3f4f6")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(meta)
    story.append(Spacer(1, 10))

    rows = [["Bill No.", "Date", "Amount", "0-30", "30-60", "60-90", "Over 90", "Total", "Days"]]
    for invoice in import_customer.invoice_rows.all():
        rows.append(
            [
                invoice.bill_number or "-",
                short_date(invoice.invoice_date),
                money(invoice.amount),
                money(invoice.bucket_0_30),
                money(invoice.bucket_30_60),
                money(invoice.bucket_60_90),
                money(invoice.bucket_over_90),
                money(invoice.total),
                str(invoice.days),
            ]
        )

    invoice_table = Table(
        rows,
        repeatRows=1,
        colWidths=[26 * mm, 18 * mm, 20 * mm, 18 * mm, 18 * mm, 18 * mm, 20 * mm, 20 * mm, 10 * mm],
    )
    invoice_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(primary_color)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
                ("PADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(invoice_table)
    story.append(Spacer(1, 10))

    totals = Table(
        [
            ["Total Outstanding", money(import_customer.total_outstanding)],
            ["Overdue > 30 Days", money(import_customer.overdue_amount)],
            ["0-30", money(import_customer.bucket_0_30)],
            ["30-60", money(import_customer.bucket_30_60)],
            ["60-90", money(import_customer.bucket_60_90)],
            ["Over 90", money(import_customer.bucket_over_90)],
        ],
        colWidths=[45 * mm, 38 * mm],
        hAlign="RIGHT",
    )
    totals.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#ecfdf5")),
                ("TEXTCOLOR", (0, 1), (-1, 1), colors.HexColor(primary_color)),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("PADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.append(totals)
    story.append(Spacer(1, 12))
    story.append(
        Paragraph(
            "Please verify the statement and clear the outstanding amount at the earliest. "
            "If payment has recently been made, please accept our thanks and ignore this reminder.",
            styles["SmallMuted"],
        )
    )

    document.build(story)
    return buffer.getvalue()
