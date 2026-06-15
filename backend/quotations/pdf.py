import re
from functools import lru_cache
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen
from xml.sax.saxutils import escape

from django.conf import settings
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import QuotationLine
from .pdf_config import get_quotation_pdf_config


PRIMARY = colors.HexColor("#0F766E")
TEXT = colors.HexColor("#111827")
MUTED = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#D1D5DB")
LIGHT_BORDER = colors.HexColor("#E5E7EB")
SOFT = colors.HexColor("#F9FAFB")
SUCCESS_SOFT = colors.HexColor("#ECFDF5")

INTERNAL_LINE_DETAIL_PATTERNS = [
    re.compile(r"\bquantity and unit\b", re.IGNORECASE),
    re.compile(r"\b(?:verify|validate|confirm|check)\b.*\b(?:accuracy|details?|item|product|quantity|composition|spelling|consistency)\b", re.IGNORECASE),
    re.compile(r"\b(?:detected|extracted|visible|present|identified)\b.*\b(?:confirm|verify|check|review)\b", re.IGNORECASE),
    re.compile(r"\bimported from (?:historical|inquiry|source)\b", re.IGNORECASE),
]


def _text(value, fallback="-"):
    value = "" if value is None else str(value)
    return escape(value.strip() or fallback)


def _optional_text(value):
    value = "" if value is None else str(value).strip()
    if value.lower() in {"", "-", "—", "n/a", "na", "none", "null"}:
        return ""
    return value


def _money(currency, value):
    return f"{currency} {value or 0:.2f}"


def _number(value):
    return f"{value:g}" if value is not None else "-"


def _customer_line_detail(value):
    value = "" if value is None else str(value).strip()
    if not value:
        return ""
    for pattern in INTERNAL_LINE_DETAIL_PATTERNS:
        if pattern.search(value):
            return ""
    return _text(value, "")


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
    if getattr(quotation, "payment_terms", "") == "as_per_agreement":
        return "As per mutually agreed terms."
    if getattr(quotation, "payment_terms", ""):
        return quotation.get_payment_terms_display()
    return config.payment_terms


@lru_cache(maxsize=128)
def _fetch_remote_image_bytes(source, max_bytes, timeout):
    request = Request(source, headers={"User-Agent": "AlAmeenQuotationPDF/1.0"})
    with urlopen(request, timeout=timeout) as response:
        image_bytes = response.read(max_bytes + 1)
    if len(image_bytes) > max_bytes:
        return None
    return image_bytes


def _resolve_image_source(source):
    if not source:
        return None
    source = str(source)
    parsed = urlparse(source)
    max_bytes = int(getattr(settings, "QUOTATION_BRANDING_IMAGE_MAX_UPLOAD_BYTES", 2 * 1024 * 1024))

    if parsed.scheme in {"http", "https"}:
        allowed_hosts = {
            str(host).strip().lower()
            for host in getattr(settings, "QUOTATION_PDF_ALLOWED_REMOTE_IMAGE_HOSTS", [])
            if str(host).strip()
        }
        host = (parsed.hostname or "").lower()
        remote_allowed = getattr(settings, "QUOTATION_PDF_ALLOW_REMOTE_IMAGES", False) or host in allowed_hosts
        if not remote_allowed:
            return None
        try:
            timeout = float(getattr(settings, "QUOTATION_PDF_REMOTE_IMAGE_TIMEOUT_SECONDS", 2.0))
            image_bytes = _fetch_remote_image_bytes(source, max_bytes, timeout)
            if not image_bytes:
                return None
            return BytesIO(image_bytes)
        except Exception:
            return None

    candidates = []
    if parsed.scheme == "file":
        candidates.append(Path(unquote(parsed.path)))
    else:
        candidates.append(Path(source))
        media_url = getattr(settings, "MEDIA_URL", "")
        if media_url and source.startswith(media_url):
            relative_name = source[len(media_url):].lstrip("/\\")
            candidates.append(Path(settings.MEDIA_ROOT) / relative_name)

    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except (OSError, ValueError):
            continue
    return None


def _image(source, max_width, max_height):
    image_source = _resolve_image_source(source)
    if not image_source:
        return ""
    try:
        image_width, image_height = ImageReader(image_source).getSize()
        scale = min(max_width / image_width, max_height / image_height, 1)
        if hasattr(image_source, "seek"):
            image_source.seek(0)
        return Image(image_source, width=image_width * scale, height=image_height * scale)
    except Exception:
        return ""


def _bundled_logo_path():
    candidate = Path(settings.BASE_DIR).parent / "frontend" / "public" / "brand" / "al-ameen-pharmacy-logo-dark.png"
    return str(candidate) if candidate.exists() else ""


def _brand_image(source, max_width, max_height):
    image = _image(source, max_width=max_width, max_height=max_height)
    if image:
        return image
    fallback = _bundled_logo_path()
    if fallback and str(fallback) != str(source or ""):
        return _image(fallback, max_width=max_width, max_height=max_height)
    return ""


def _logo(path, max_width=60 * mm, max_height=26 * mm):
    return _brand_image(path, max_width=max_width, max_height=max_height)


def _product_thumbnail(source):
    item_image = _image(source, max_width=24 * mm, max_height=15 * mm)
    if not item_image:
        return ""
    item_image.hAlign = "LEFT"
    frame = Table(
        [[item_image]],
        colWidths=[27 * mm],
        hAlign="LEFT",
    )
    frame.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.25, LIGHT_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ]
        )
    )
    return frame


def _contact_parts(config):
    return [
        part
        for part in [
            _text(config.address, ""),
            f"Phone: {_text(config.phone, '')}" if config.phone else "",
            f"Email: {_text(config.email, '')}" if config.email else "",
            f"TRN: {_text(config.trn, '')}" if config.trn else "",
            f"License: {_text(config.license_number, '')}" if config.license_number else "",
        ]
        if part
    ]


def _contact_block(config, styles, inline=False):
    parts = _contact_parts(config)
    if not parts:
        return ""
    separator = " | " if inline else "<br/>"
    return Paragraph(separator.join(parts), styles["ContactLine"] if inline else styles["SmallMuted"])


def _brand_lines(config, styles, include_contact=True):
    lines = [Paragraph(_text(config.company_name, "Al Ameen Pharmacy"), styles["BrandName"])]
    if config.company_name_ar:
        lines.append(Paragraph(_text(config.company_name_ar, ""), styles["BrandArabic"]))
    if include_contact:
        contact = _contact_block(config, styles)
        if contact:
            lines.append(contact)
    return lines


def _quotation_title_block(quotation, quote_date, styles):
    return [
        Paragraph("<b>QUOTATION</b>", styles["QuoteTitle"]),
        Paragraph(f"Quote No: {_text(quotation.quotation_number)}", styles["SmallMutedRight"]),
        Paragraph(f"Date: {_text(quote_date)}", styles["SmallMutedRight"]),
    ]


def _build_header(config, quotation, quote_date, styles):
    logo_layout = config.logo_layout or "full_logo_only"
    logo_flowable = (
        ""
        if logo_layout == "no_logo"
        else _brand_image(
            config.logo_path,
            max_width=78 * mm if logo_layout == "full_logo_only" else 60 * mm,
            max_height=28 * mm if logo_layout == "full_logo_only" else 26 * mm,
        )
    )
    title_block = _quotation_title_block(quotation, quote_date, styles)

    if logo_layout == "full_logo_only" and logo_flowable:
        contact = _contact_block(config, styles, inline=True)
        top_row = Table([["", logo_flowable, title_block]], colWidths=[45 * mm, 88 * mm, 45 * mm])
        top_row.setStyle(
            TableStyle(
                [
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("ALIGN", (1, 0), (1, 0), "CENTER"),
                    ("ALIGN", (2, 0), (2, 0), "RIGHT"),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        header_rows = [[top_row]]
        if contact:
            contact_row = Table([["", contact, ""]], colWidths=[30 * mm, 118 * mm, 30 * mm])
            contact_row.setStyle(
                TableStyle(
                    [
                        ("ALIGN", (1, 0), (1, 0), "CENTER"),
                        ("TOPPADDING", (0, 0), (-1, -1), 0),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                    ]
                )
            )
            header_rows.append([contact_row])
        header = Table(header_rows, colWidths=[178 * mm])
        header.setStyle(
            TableStyle(
                [
                    ("LEFTPADDING", (0, 0), (-1, -1), 0),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                    ("TOPPADDING", (0, 0), (-1, -1), 0),
                    ("BOTTOMPADDING", (0, -1), (-1, -1), 4),
                ]
            )
        )
        return header
    elif logo_layout == "no_logo" or not logo_flowable:
        header = Table([[_brand_lines(config, styles, include_contact=True), title_block]], colWidths=[118 * mm, 60 * mm])
    else:
        logo_width = 34 * mm if logo_layout == "icon_left_company_text" else 42 * mm
        logo_height = 20 * mm if logo_layout == "icon_left_company_text" else 22 * mm
        small_logo = _brand_image(config.logo_path, max_width=logo_width, max_height=logo_height)
        header = Table(
            [[small_logo, _brand_lines(config, styles, include_contact=True), title_block]],
            colWidths=[logo_width + 4 * mm, 74 * mm, 60 * mm],
        )

    header.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (-1, 0), (-1, 0), "RIGHT"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ]
        )
    )
    return header


def _draw_draft_watermark(canvas, doc):
    quotation = getattr(doc, "quotation", None)
    if not quotation or quotation.status not in {quotation.STATUS_DRAFT, quotation.STATUS_PENDING_REVIEW, quotation.STATUS_APPROVED}:
        return
    canvas.saveState()
    try:
        canvas.setFillAlpha(0.08)
    except AttributeError:
        pass
    canvas.setFillColor(MUTED)
    canvas.setFont("Helvetica-Bold", 64)
    canvas.translate(A4[0] / 2, A4[1] / 2)
    canvas.rotate(35)
    canvas.drawCentredString(0, 0, "DRAFT")
    canvas.restoreState()


def _footer(canvas, doc):
    _draw_draft_watermark(canvas, doc)
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.3)
    canvas.line(doc.leftMargin, 12 * mm, A4[0] - doc.rightMargin, 12 * mm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(A4[0] / 2, 8 * mm, f"Page {doc.page}")
    canvas.restoreState()


def build_quotation_pdf(quotation):
    config = get_quotation_pdf_config(quotation=quotation)
    primary = colors.HexColor(config.primary_color or "#0F766E")
    accent = colors.HexColor(config.accent_color or "#ECFDF5")
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
    doc.quotation = quotation
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="BrandName", parent=styles["Title"], fontSize=18, leading=22, textColor=primary))
    styles.add(ParagraphStyle(name="BrandArabic", parent=styles["Normal"], fontSize=10, leading=13, textColor=MUTED))
    styles.add(ParagraphStyle(name="SmallMuted", parent=styles["Normal"], fontSize=8, leading=11, textColor=MUTED))
    styles.add(ParagraphStyle(name="ContactLine", parent=styles["SmallMuted"], fontSize=7.4, leading=9, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="SmallMutedRight", parent=styles["SmallMuted"], fontSize=7.5, leading=10, alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="Small", parent=styles["Normal"], fontSize=8, leading=11, textColor=TEXT))
    styles.add(ParagraphStyle(name="MetaLabel", parent=styles["Small"], fontName="Helvetica-Bold", fontSize=8.25, leading=10, textColor=MUTED))
    styles.add(ParagraphStyle(name="MetaValue", parent=styles["Small"], fontSize=8.25, leading=10, textColor=TEXT, splitLongWords=True, spaceShrinkage=0.04))
    styles.add(ParagraphStyle(name="TableHeader", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=7.6, leading=9.2, textColor=colors.white, alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="TableCell", parent=styles["Normal"], fontName="Helvetica", fontSize=7.8, leading=9.4, textColor=TEXT))
    styles.add(ParagraphStyle(name="TableCellCenter", parent=styles["TableCell"], alignment=TA_CENTER))
    styles.add(ParagraphStyle(name="TableCellRight", parent=styles["TableCell"], alignment=TA_RIGHT))
    styles.add(ParagraphStyle(name="TableCellMoney", parent=styles["TableCellRight"], fontSize=7.8, leading=9.4, splitLongWords=False, spaceShrinkage=0.03))
    styles.add(ParagraphStyle(name="SectionTitle", parent=styles["Heading4"], fontSize=10, leading=12, textColor=primary))
    styles.add(ParagraphStyle(name="QuoteTitle", parent=styles["Title"], alignment=TA_RIGHT, fontSize=18, leading=22, textColor=TEXT))
    styles.add(ParagraphStyle(name="ApprovalLine", parent=styles["SmallMuted"], alignment=TA_CENTER, fontSize=8, leading=9, textColor=MUTED))

    elements = []
    quote_date = _local_date(quotation.created_at)

    elements.append(_build_header(config, quotation, quote_date, styles))

    def meta_label(value):
        return Paragraph(_text(value), styles["MetaLabel"])

    def meta_value(value):
        return Paragraph(_text(value), styles["MetaValue"])

    contact = quotation.contact
    contact_phone = getattr(contact, "phone", "") if contact else ""
    contact_email = getattr(contact, "email", "") if contact else ""
    meta_items = [
        ("Customer", quotation.company.name),
        ("Quotation #", quotation.quotation_number),
        ("Customer Address", _optional_text(getattr(quotation.company, "billing_address", ""))),
        ("Customer TRN", _optional_text(getattr(quotation.company, "trn", ""))),
        ("Attention", _optional_text(contact.name if contact else "")),
        ("Contact No.", _optional_text(contact_phone)),
        ("Contact Email", _optional_text(contact_email)),
        ("Date", quote_date),
        ("Valid Until", _valid_until(quotation, config)),
        ("Prepared By", quotation.created_by.username if quotation.created_by else ""),
        ("Status", quotation.get_status_display()),
        ("Currency", quotation.currency),
    ]
    meta_items = [(label, value) for label, value in meta_items if str(value or "").strip()]
    meta_rows = []
    for index in range(0, len(meta_items), 2):
        left_label, left_value = meta_items[index]
        if index + 1 < len(meta_items):
            right_label, right_value = meta_items[index + 1]
        else:
            right_label, right_value = "", ""
        meta_rows.append([meta_label(left_label), meta_value(left_value), meta_label(right_label), meta_value(right_value)])
    meta_table = Table(meta_rows, colWidths=[24 * mm, 76 * mm, 24 * mm, 54 * mm])
    meta_table.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.35, LIGHT_BORDER),
                ("INNERGRID", (0, 0), (-1, -1), 0.2, LIGHT_BORDER),
                ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                ("BACKGROUND", (0, 0), (0, -1), SOFT),
                ("BACKGROUND", (2, 0), (2, -1), SOFT),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 4.5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4.5),
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
    lines = quotation.lines.exclude(match_status=QuotationLine.MATCH_IGNORED).select_related("product", "product_image").order_by("sort_order", "id")
    for index, line in enumerate(lines, start=1):
        item_text = _text(line.item_name_snapshot)
        details = []
        description = _customer_line_detail(line.description)
        if description and description != item_text:
            details.append(description)
        if details:
            item_text = f"{item_text}<br/>{'<br/>'.join(details)}"
        item_cell = [Paragraph(item_text, styles["TableCell"])]
        if line.include_product_image:
            selected_image = line.product_image or (line.product.primary_image if line.product_id else None)
            image_url = getattr(getattr(selected_image, "image", None), "url", "")
            item_image = _product_thumbnail(image_url)
            if item_image:
                item_cell.extend([Spacer(1, 3), item_image])
        table_data.append(
            [
                Paragraph(str(index), styles["TableCellCenter"]),
                item_cell,
                Paragraph(_number(line.quantity), styles["TableCellRight"]),
                Paragraph(_text(line.unit), styles["TableCell"]),
                Paragraph(_money(quotation.currency, line.unit_price), styles["TableCellMoney"]),
                Paragraph(_money(quotation.currency, line.vat_amount), styles["TableCellMoney"]),
                Paragraph(_money(quotation.currency, line.line_total), styles["TableCellMoney"]),
            ]
        )

    line_table = Table(
        table_data,
        colWidths=[10 * mm, 73 * mm, 14 * mm, 15 * mm, 26 * mm, 25 * mm, 25 * mm],
        repeatRows=1,
    )
    line_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), primary),
                ("GRID", (0, 0), (-1, -1), 0.25, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, SOFT]),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (4, 0), (-1, -1), 2),
                ("RIGHTPADDING", (4, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (0, -1), 3),
                ("RIGHTPADDING", (0, 0), (0, -1), 3),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
            ]
        )
    )
    elements.append(line_table)
    line_count = max(lines.count(), 1)

    totals_table = Table(
        [
            ["Subtotal", _money(quotation.currency, quotation.subtotal)],
            ["VAT", _money(quotation.currency, quotation.vat_total)],
            ["Grand Total", _money(quotation.currency, quotation.total)],
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
                ("BACKGROUND", (0, -1), (-1, -1), accent),
                ("TEXTCOLOR", (0, -1), (-1, -1), primary),
                ("ALIGN", (1, 0), (1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    elements.append(KeepTogether([Spacer(1, 10), totals_table]))

    if quotation.notes:
        elements.append(Spacer(1, 8))
        elements.append(Paragraph("Notes", styles["SectionTitle"]))
        elements.append(Paragraph(_text(quotation.notes), styles["Small"]))

    elements.append(Spacer(1, 20 if line_count <= 3 else 10))
    footer_data = [
        [
            [
                Paragraph("Terms and Conditions", styles["SectionTitle"]),
                Paragraph(_text(config.default_terms), styles["Small"]),
                Spacer(1, 4),
                Paragraph(f"<b>Validity:</b> {config.validity_days} days from quotation date unless otherwise stated.", styles["Small"]),
                Paragraph(f"<b>Payment Terms:</b> {_text(_payment_terms(quotation, config))}", styles["Small"]),
            ],
            _signature_flowables(config, styles, quotation),
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
    if config.footer_note:
        elements.append(Spacer(1, 6))
        elements.append(Paragraph(_text(config.footer_note), styles["SmallMuted"]))

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    buffer.seek(0)
    return buffer.getvalue()


def _signature_flowables(config, styles, quotation=None):
    flowables = [Paragraph("Prepared / Approved By", styles["SectionTitle"])]

    approval_cells = []
    if config.show_signature_area:
        signature_image = _image(config.signature_image_path, max_width=28 * mm, max_height=13 * mm)
        signature_label = _approval_label(config.signature_label, "Authorized Signature", {"signature"})
        approval_cells.append(_approval_cell(signature_image, signature_label, styles))
    if config.show_stamp_area:
        stamp_image = _image(config.stamp_image_path, max_width=24 * mm, max_height=24 * mm)
        stamp_label = _approval_label(config.stamp_label, "Company Stamp", {"stamp"})
        approval_cells.append(_approval_cell(stamp_image, stamp_label, styles))
    if approval_cells:
        flowables.append(Spacer(1, 8))
        column_width = (52 * mm) / len(approval_cells)
        approval_table = Table([approval_cells], colWidths=[column_width] * len(approval_cells))
        approval_table.setStyle(
            TableStyle(
                [
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 2),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        flowables.append(approval_table)
    return flowables


def _approval_cell(image, label, styles):
    if image:
        return [
            image,
            Spacer(1, 3),
            Paragraph(label, ParagraphStyle(name="ApprovalLabel", parent=styles["SmallMuted"], alignment=TA_CENTER)),
        ]
    return [
        Spacer(1, 8),
        Paragraph("______________", styles["ApprovalLine"]),
        Spacer(1, 2),
        Paragraph(label, ParagraphStyle(name="ApprovalPlaceholder", parent=styles["SmallMuted"], alignment=TA_CENTER)),
    ]


def _approval_label(value, fallback, generic_values):
    raw_value = (value or "").strip()
    if not raw_value or raw_value.lower() in generic_values:
        raw_value = fallback
    return _text(raw_value)
