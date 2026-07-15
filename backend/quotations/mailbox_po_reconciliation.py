"""Reconcile canonical mailbox PO documents to quotations, without outcomes.

The inventory is message-centric and complete.  This module turns each parsed
attachment (and the newest email body) into an independent document variant,
ranks it against eligible quotations, and stores only review evidence.  It
never creates an LPO, proforma, order, or quotation-line outcome.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Prefetch, Q
from django.utils import timezone

from .ai_parsing import AI_SOURCE_VISION
from .contract_intelligence import gmail_connection_lineage_q
from .import_parsers import parse_text_preview
from .mailbox_po_audit import extract_po_references
from .mailbox_po_matching import (
    AMBIGUOUS,
    AUTOMATIC,
    CanonicalMailboxMessage,
    EligibleQuotation,
    EligibleQuoteLine,
    MailboxPOLine,
    rank_message_to_quotations,
)
from .models import (
    CompanyContact,
    MailboxPOAuditRun,
    MailboxPOMatchRun,
    MailboxPOMessage,
    Quotation,
    QuotationLine,
    QuotationPOEvidence,
)


ALGORITHM_VERSION = "mailbox_match_v5"
MAX_ACTIVE_EVIDENCE_PER_QUOTE = 3
MAX_MATCH_ERRORS = 500
DEFAULT_MATCH_PAGE_SIZE = 5
MAX_MATCH_PAGE_SIZE = 25
MATCH_LEASE_SECONDS = 90
BODY_ORDER_SIGNAL_RE = re.compile(
    r"\b(?:lpo|local\s+purchase\s+order|purchase\s+order|order\s+confirmation)\b"
    r"|\b(?:please\s+proceed|go\s+ahead)\b"
    r"|\b(?:accepted|approved)\s+(?:quote|quotation)\b",
    re.IGNORECASE,
)
BODY_METADATA_ROW_RE = re.compile(
    r"^\s*(?:quotation|quote|local\s+purchase\s+order|purchase\s+order|lpo|"
    r"po(?:\s*(?:no\.?|number|#|:|-))|quantity|qty|grand\s+total|total|"
    r"please\s+proceed|go\s+ahead|please\s+(?:find|see)|see\s+attached)\b",
    re.IGNORECASE,
)
TOTAL_LINE_RE = re.compile(
    r"(?im)^[ \t]*(?P<label>grand[ \t]+total|net[ \t]+total|net[ \t]+amount|"
    r"total[ \t]+amount[ \t]+due|total[ \t]+amount|"
    r"invoice[ \t]+total|total)[ \t]*(?:\([^\n)]*\))?[ \t]*[:\-]?[ \t]*"
    r"(?:AED|DHS?|USD|EUR|GBP)?[ \t]*"
    r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d{1,3})?|\d+(?:\.\d{1,3})?)[ \t]*$"
)
ARIBA_MONEY_CELL_RE = re.compile(
    r"^\s*(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s*"
    r"(?:AED|DHS?|USD|EUR|GBP)\s*$",
    re.IGNORECASE,
)
ARIBA_UNIT_CELL_RE = re.compile(r"^\((?P<unit>[A-Z0-9_./-]{1,12})\)$", re.IGNORECASE)
EMRILL_DESCRIPTION_RE = re.compile(r"^description\s*:\s*(?P<name>.+)$", re.IGNORECASE)
EMRILL_AMOUNT_DATE_RE = re.compile(
    r"^\s*(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)\s+"
    r"\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
    re.IGNORECASE,
)
EMRILL_PERCENT_RE = re.compile(r"^\d+(?:\.\d+)?%$")


@dataclass(frozen=True)
class DocumentVariant:
    message: CanonicalMailboxMessage
    source_kind: str
    attachment_id: str = ""
    filename: str = ""
    source_sha256: str = ""
    extracted_text: str = ""
    lpo_references: tuple[str, ...] = ()
    quotation_references: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProposedEvidence:
    quote_id: int
    candidate: object
    variant: DocumentVariant
    result_status: str
    result_reason: str
    decisive: bool = False
    automatic_blockers: tuple[str, ...] = ()


def _decimal(value):
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, TypeError, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _row_name(row):
    return str(
        row.get("name")
        or row.get("item_name")
        or row.get("raw_name")
        or row.get("requested_item_name")
        or row.get("product_name")
        or ""
    ).strip()


def _canonical_rows(rows, *, source):
    canonical = []
    for index, row in enumerate(rows or [], start=1):
        if not isinstance(row, dict):
            continue
        name = _row_name(row)
        if not name:
            continue
        canonical.append(
            MailboxPOLine(
                line_id=row.get("line_id") or row.get("row_number") or index,
                name=name,
                description=str(row.get("description") or row.get("notes") or ""),
                quantity=_decimal(row.get("quantity") if row.get("quantity") is not None else row.get("qty")),
                unit_price=_decimal(
                    row.get("unit_price") if row.get("unit_price") is not None else row.get("price")
                ),
                line_total=_decimal(
                    row.get("line_total") if row.get("line_total") is not None else row.get("amount")
                ),
                unit=str(row.get("unit") or row.get("uom") or ""),
                source=source,
            )
        )
    return tuple(canonical)


def _body_item_rows(rows):
    """Discard reference/headline rows a plain-text parser can mistake for items."""

    return [
        row
        for row in (rows or [])
        if isinstance(row, dict) and not BODY_METADATA_ROW_RE.search(_row_name(row))
    ]


def _nonblank_cells(text):
    return [
        re.sub(r"\s+", " ", raw_line.replace("\u00a0", " ")).strip()
        for raw_line in str(text or "").splitlines()
        if re.sub(r"\s+", " ", raw_line.replace("\u00a0", " ")).strip()
    ]


def _portal_unit(value):
    unit = str(value or "").strip(" ()")
    return {"EA": "each", "_01": "each"}.get(unit.upper(), unit)


PORTAL_NUMBER_CELL_RE = re.compile(
    r"^(?:AED|DHS?)?\s*(?P<value>-?(?:\d{1,3}(?:,\d{3})*|\d+)(?:\.\d+)?|-?\.\d+)\s*$",
    re.IGNORECASE,
)
PORTAL_UNIT_CELL_RE = re.compile(r"^[A-Z][A-Z0-9._/-]{0,15}$", re.IGNORECASE)


def _portal_number(value):
    match = PORTAL_NUMBER_CELL_RE.fullmatch(str(value or "").strip())
    return _decimal(match.group("value")) if match else None


def _layout_row_warnings(layout, rows, expected_rows):
    if not rows:
        return (
            f"{layout} purchase order was recognized, but no commercial item rows were parsed.",
        )
    if expected_rows > len(rows):
        return (
            f"{layout} purchase-order extraction was incomplete: parsed {len(rows)} of "
            f"{expected_rows} detected line-item rows.",
        )
    return ()


def _hotel_expected_line_number(cells, index, end):
    """Identify a hotel line marker without counting integer money/qty cells."""

    match = re.fullmatch(r"(?P<line>\d{1,3})(?:\s+(?P<suffix>.+))?", cells[index])
    if not match:
        return None
    suffix = str(match.group("suffix") or "").strip(" ()")
    if suffix:
        # A quantity cell such as ``2 EA`` is not item line 2.
        if suffix.upper() in {
            "BTL",
            "BOX",
            "CTN",
            "EA",
            "EACH",
            "NO",
            "NOS",
            "PACK",
            "PACKET",
            "PC",
            "PCS",
            "PKT",
            "SET",
        }:
            return None
        return int(match.group("line"))
    # Bare serials are followed by a SKU/description. Integer prices are
    # followed by more numeric cells or by the next genuine serial marker.
    for candidate in range(index + 1, min(end, index + 4)):
        if re.fullmatch(r"\d{1,3}(?:\s+.+)?", cells[candidate]):
            return None
        if re.search(r"[A-Za-z]", cells[candidate]):
            return int(match.group("line"))
    return None


def _hotel_procurement_order_rows(text, *, source):
    """Parse the vertical-cell hotel PO used by Bvlgari and Marriott."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    required = {"# item", "product desc.", "qty unit", "unit price", "extension"}
    recognized = bool(
        any(cell == "purchase order" for cell in lowered[:12])
        and required.issubset(lowered)
        and any(cell.startswith("department:") for cell in lowered)
    )
    if not recognized:
        return False, (), ()

    header = lowered.index("# item")
    start = next(
        (
            index + 1
            for index in range(header, min(len(cells), header + 20))
            if lowered[index].startswith("department:")
        ),
        header + 1,
    )
    end = next(
        (
            index
            for index in range(start, len(cells))
            if lowered[index].startswith("* - non catalog item")
            or lowered[index] in {"sub total:", "subtotal:"}
        ),
        len(cells),
    )
    expected_rows = len(
        {
            line_number
            for candidate in range(start, end)
            if (line_number := _hotel_expected_line_number(cells, candidate, end))
            is not None
        }
    )
    rows = []
    index = start
    while index < end:
        line_match = re.fullmatch(r"(?P<line>\d{1,3})(?:\s+(?P<sku>.+))?", cells[index])
        if not line_match:
            index += 1
            continue
        quantity_index = next(
            (
                candidate
                for candidate in range(index + 1, min(end, index + 10))
                if re.fullmatch(
                    r"\d+(?:\.\d+)?\s+[A-Z][A-Z0-9._/-]{0,15}",
                    cells[candidate],
                    re.IGNORECASE,
                )
            ),
            None,
        )
        if quantity_index is None:
            index += 1
            continue
        quantity_match = re.fullmatch(
            r"(?P<quantity>\d+(?:\.\d+)?)\s+(?P<unit>[A-Z][A-Z0-9._/-]{0,15})",
            cells[quantity_index],
            re.IGNORECASE,
        )
        money = [
            _portal_number(cells[candidate])
            for candidate in range(
                quantity_index + 1,
                min(end, quantity_index + 6),
            )
        ]
        if len(money) < 2 or money[0] is None or money[1] is None:
            index += 1
            continue

        name_cells = []
        if line_match.group("sku"):
            name_cells.append(line_match.group("sku"))
        name_cells.extend(cells[index + 1 : quantity_index])
        star_indexes = [
            offset for offset, value in enumerate(name_cells) if "*" in value
        ]
        if star_indexes:
            name_cells = name_cells[star_indexes[-1] + 1 :]
        name = " ".join(
            value.strip(" *")
            for value in name_cells
            if value.strip(" *") and re.search(r"[A-Za-z]", value)
        )
        if not name:
            index += 1
            continue
        rows.append(
            MailboxPOLine(
                line_id=line_match.group("line"),
                name=name,
                quantity=_decimal(quantity_match.group("quantity")),
                unit_price=money[0],
                line_total=money[1],
                unit=_portal_unit(quantity_match.group("unit")),
                source=source,
            )
        )
        # Only quantity, unit price and extension are required/consumed. Scan
        # through optional tax/amount cells so a compact two-column layout does
        # not skip the next line item.
        index = quantity_index + 3

    warnings = _layout_row_warnings("Hotel", rows, expected_rows)
    return True, tuple(rows), warnings


def _raq_order_rows(text, *, source):
    """Parse RAQ/Sanisoft POs whose visual columns extract in reading order."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    required = {"unit price", "description", "#", "unit", "quantity", "total price"}
    recognized = bool(
        "purchase order" in lowered[:12]
        and required.issubset(lowered)
        and any("sanisoft" in cell for cell in lowered)
    )
    if not recognized:
        return False, (), ()
    start = lowered.index("total price") + 1
    end = next(
        (
            index
            for index in range(start, len(cells))
            if "powered by sanisoft" in lowered[index]
        ),
        len(cells),
    )

    def row_shape(index):
        return bool(
            index + 5 < end
            and re.fullmatch(r"\d{1,3}", cells[index])
            and _portal_number(cells[index + 1]) is not None
            and re.search(r"[A-Za-z]", cells[index + 2])
            and PORTAL_UNIT_CELL_RE.fullmatch(cells[index + 3])
            and _portal_number(cells[index + 4]) is not None
            and _portal_number(cells[index + 5]) is not None
        )

    expected_rows = sum(
        1
        for candidate in range(start, max(start, end - 2))
        if (
            re.fullmatch(r"\d{1,3}", cells[candidate])
            and _portal_number(cells[candidate + 1]) is not None
            and re.search(r"[A-Za-z]", cells[candidate + 2])
        )
    )
    rows = []
    index = start
    while index < end:
        if not row_shape(index):
            index += 1
            continue
        next_index = next(
            (candidate for candidate in range(index + 6, end) if row_shape(candidate)),
            end,
        )
        primary = cells[index + 2]
        details = [
            value
            for value in cells[index + 6 : next_index]
            if re.search(r"[A-Za-z]", value)
        ]
        if primary.casefold().startswith("first aid items") and details:
            name = " ".join(details)
        else:
            name = " ".join(dict.fromkeys([primary, *details]))
        rows.append(
            MailboxPOLine(
                line_id=cells[index],
                name=name,
                quantity=_portal_number(cells[index + 4]),
                unit_price=_portal_number(cells[index + 5]),
                line_total=_portal_number(cells[index + 1]),
                unit=_portal_unit(cells[index + 3]),
                source=source,
            )
        )
        index = next_index

    warnings = _layout_row_warnings("RAQ", rows, expected_rows)
    return True, tuple(rows), warnings


def _khansaheb_order_rows(text, *, source):
    """Parse Khansaheb's split-cell commodity table."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        any("khansaheb civil engineering" in cell for cell in lowered[:40])
        and "purchase order" in lowered[:12]
        and {"commodity code", "description", "quantity", "uom", "unit price", "disc%"}.issubset(lowered)
    )
    if not recognized:
        return False, (), ()
    header = lowered.index("commodity code")
    start = next(
        (index + 1 for index in range(header, len(cells)) if lowered[index] == "total"),
        header + 1,
    )
    end = next(
        (
            index
            for index in range(start, len(cells))
            if lowered[index].startswith("delivery contact")
            or lowered[index].startswith("the above rates")
        ),
        len(cells),
    )
    expected_rows = sum(
        1
        for candidate in range(start, max(start, end - 2))
        if (
            re.fullmatch(r"\d{1,3}", cells[candidate])
            and re.fullmatch(r"[A-Z0-9][A-Z0-9./_-]{4,}", cells[candidate + 1], re.IGNORECASE)
            and re.search(r"[A-Za-z]", cells[candidate + 2])
        )
    )
    rows = []
    index = start
    while index < end:
        if not re.fullmatch(r"\d{1,3}", cells[index]) or index + 6 >= end:
            index += 1
            continue
        quantity_index = next(
            (
                candidate
                for candidate in range(index + 2, min(end, index + 8))
                if (
                    _portal_number(cells[candidate]) is not None
                    and candidate + 4 < end
                    and PORTAL_UNIT_CELL_RE.fullmatch(cells[candidate + 1])
                    and all(
                        _portal_number(cells[offset]) is not None
                        for offset in range(candidate + 2, candidate + 5)
                    )
                )
            ),
            None,
        )
        if quantity_index is None:
            index += 1
            continue
        name = " ".join(
            value
            for value in cells[index + 2 : quantity_index]
            if re.search(r"[A-Za-z]", value)
        )
        if not name:
            index += 1
            continue
        rows.append(
            MailboxPOLine(
                line_id=cells[index],
                name=name,
                description=f"Commodity code: {cells[index + 1]}",
                quantity=_portal_number(cells[quantity_index]),
                unit_price=_portal_number(cells[quantity_index + 2]),
                line_total=_portal_number(cells[quantity_index + 4]),
                unit=_portal_unit(cells[quantity_index + 1]),
                source=source,
            )
        )
        index = quantity_index + 5

    warnings = _layout_row_warnings("Khansaheb", rows, expected_rows)
    return True, tuple(rows), warnings


def _dubai_holding_order_rows(text, *, source):
    """Parse Madinat Jumeirah/Dubai Holding's schedule-of-details table."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        any(cell.startswith("purchase order:") for cell in lowered[:12])
        and "schedule of details" in lowered
        and {"item description", "uom", "qty"}.issubset(lowered)
    )
    if not recognized:
        return False, (), ()
    start = lowered.index("schedule of details") + 1
    end = next(
        (index for index in range(start, len(cells)) if lowered[index] == "attachments:"),
        len(cells),
    )
    expected_rows = sum(
        1
        for candidate in range(start, max(start, end - 1))
        if (
            re.fullmatch(r"\d{1,3}", cells[candidate])
            and re.search(r"[A-Za-z]", cells[candidate + 1])
        )
    )
    rows = []
    index = start
    while index < end:
        if not re.fullmatch(r"\d{1,3}", cells[index]):
            index += 1
            continue
        unit_index = next(
            (
                candidate
                for candidate in range(index + 2, min(end, index + 20))
                if (
                    PORTAL_UNIT_CELL_RE.fullmatch(cells[candidate])
                    and candidate + 5 < end
                    and all(
                        _portal_number(cells[offset]) is not None
                        for offset in range(candidate + 1, candidate + 6)
                    )
                )
            ),
            None,
        )
        if unit_index is None:
            index += 1
            continue
        name_cells = []
        for value in cells[index + 1 : unit_index]:
            lowered_value = value.casefold()
            if lowered_value.startswith(("product code:", "bpa:", "note from requester:")):
                break
            if re.search(r"[A-Za-z]", value):
                name_cells.append(value)
        name = " ".join(name_cells)
        if not name:
            index += 1
            continue
        rows.append(
            MailboxPOLine(
                line_id=cells[index],
                name=name,
                quantity=_portal_number(cells[unit_index + 1]),
                unit_price=_portal_number(cells[unit_index + 2]),
                # ``Amount`` is the pre-tax commercial extension; ``Line
                # Total`` includes VAT and is compared separately as the
                # document total for full-quote matches.
                line_total=_portal_number(cells[unit_index + 3]),
                unit=_portal_unit(cells[unit_index]),
                source=source,
            )
        )
        index = unit_index + 6

    warnings = _layout_row_warnings("Dubai Holding", rows, expected_rows)
    return True, tuple(rows), warnings


def _ecc_order_rows(text, *, source):
    """Parse ECC's reordered single-page PO table cells."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        any("engineering contracting co." in cell for cell in lowered[:40])
        and "purchase order" in lowered[:30]
        and {"resource name/description", "quantity"}.issubset(lowered)
        and any(cell.startswith("unit price") for cell in lowered)
    )
    if not recognized:
        return False, (), ()
    expected_rows = sum(
        1
        for candidate in range(len(cells))
        if (
            re.fullmatch(r"\d{10,14}", cells[candidate])
            and candidate + 1 < len(cells)
            and re.search(r"[A-Za-z]", cells[candidate + 1])
            and any(
                PORTAL_UNIT_CELL_RE.fullmatch(value)
                for value in cells[candidate + 2 : candidate + 6]
            )
        )
    )
    rows = []
    index = 0
    while index + 7 < len(cells):
        shaped = bool(
            _portal_number(cells[index]) is not None
            and _portal_number(cells[index + 1]) is not None
            and re.fullmatch(r"\d{1,3}", cells[index + 2])
            and _portal_number(cells[index + 3]) is not None
            and _portal_number(cells[index + 4]) is not None
            and re.fullmatch(r"\d{8,}", cells[index + 5])
            and re.search(r"[A-Za-z]", cells[index + 6])
            and PORTAL_UNIT_CELL_RE.fullmatch(cells[index + 7])
        )
        if not shaped:
            index += 1
            continue
        rows.append(
            MailboxPOLine(
                line_id=cells[index + 2],
                name=cells[index + 6],
                description=f"Resource code: {cells[index + 5]}",
                quantity=_portal_number(cells[index + 4]),
                unit_price=_portal_number(cells[index]),
                line_total=_portal_number(cells[index + 1]),
                unit=_portal_unit(cells[index + 7]),
                source=source,
            )
        )
        index += 8

    warnings = _layout_row_warnings("ECC", rows, expected_rows)
    return True, tuple(rows), warnings


def _al_sahel_order_rows(text, *, source):
    """Parse Al Sahel's local-purchase-order table."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        "local purchase order" in lowered[:100]
        and any("al sahel" in cell for cell in lowered[:100])
        and {"item code", "qty.", "unit"}.issubset(lowered)
    )
    if not recognized:
        return False, (), ()
    start = lowered.index("local purchase order") + 1
    end = next(
        (
            index
            for index in range(start, len(cells))
            if "****end****" in lowered[index]
        ),
        len(cells),
    )
    expected_rows = sum(
        1
        for candidate in range(start, max(start, end - 1))
        if (
            re.fullmatch(r"0*\d{1,3}", cells[candidate])
            and re.fullmatch(r"\d{5,}", cells[candidate + 1])
        )
    )
    rows = []
    index = start
    while index < end:
        if (
            not re.fullmatch(r"0*\d{1,3}", cells[index])
            or index + 6 >= end
            or not re.fullmatch(r"\d{5,}", cells[index + 1])
        ):
            index += 1
            continue
        unit_index = next(
            (
                candidate
                for candidate in range(index + 3, min(end, index + 10))
                if (
                    PORTAL_UNIT_CELL_RE.fullmatch(cells[candidate])
                    and candidate + 3 < end
                    and all(
                        _portal_number(cells[offset]) is not None
                        for offset in range(candidate + 1, candidate + 4)
                    )
                )
            ),
            None,
        )
        if unit_index is None:
            index += 1
            continue
        name = " ".join(cells[index + 2 : unit_index])
        rows.append(
            MailboxPOLine(
                line_id=cells[index],
                name=name,
                description=f"Item code: {cells[index + 1]}",
                quantity=_portal_number(cells[unit_index + 1]),
                unit_price=_portal_number(cells[unit_index + 3]),
                line_total=_portal_number(cells[unit_index + 2]),
                unit=_portal_unit(cells[unit_index]),
                source=source,
            )
        )
        index = unit_index + 4

    warnings = _layout_row_warnings("Al Sahel", rows, expected_rows)
    return True, tuple(rows), warnings


def _ariba_order_rows(text, *, source):
    """Parse SAP Business Network's one-cell-per-line PO notification."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        any("sap business network" in cell or "ariba network" in cell for cell in lowered)
        and any("purchase order" in cell for cell in lowered)
    )
    if not recognized:
        return False, (), ()
    if "line #" not in lowered:
        return True, (), (
            "SAP Business Network purchase order was recognized, but no displayed line-item sections were found.",
        )

    starts = [index for index, cell in enumerate(lowered) if cell == "line #"]
    rows = []
    for section_index, start in enumerate(starts):
        end = starts[section_index + 1] if section_index + 1 < len(starts) else len(cells)
        section = cells[start:end]
        section_lower = lowered[start:end]
        required_headers = {
            "part # / description",
            "qty (unit)",
            "unit price",
            "subtotal",
            "tax",
        }
        if not required_headers.issubset(section_lower):
            continue
        try:
            header_end = section_lower.index("tax") + 1
            control_index = section_lower.index("control keys", header_end)
        except ValueError:
            continue
        payload = section[header_end:control_index]
        payload_lower = section_lower[header_end:control_index]
        type_index = next(
            (
                index
                for index, value in enumerate(payload_lower)
                if value in {"material", "service"}
            ),
            None,
        )
        if type_index is None:
            continue
        values = payload[type_index + 1 :]
        quantity_index = next(
            (
                index
                for index, value in enumerate(values)
                if re.fullmatch(r"\d+(?:\.\d+)?", value)
            ),
            None,
        )
        if quantity_index is None:
            continue
        quantity = _decimal(values[quantity_index])
        unit = ""
        if quantity_index + 1 < len(values):
            unit_match = ARIBA_UNIT_CELL_RE.fullmatch(values[quantity_index + 1])
            if unit_match:
                unit = _portal_unit(unit_match.group("unit"))
        money = [
            (index, _decimal(match.group("value")))
            for index, value in enumerate(values)
            if (match := ARIBA_MONEY_CELL_RE.fullmatch(value))
        ]
        if quantity is None or len(money) < 2:
            continue
        last_money_index = money[-1][0]
        description_cells = [
            value
            for value in values[last_money_index + 1 :]
            if re.search(r"[A-Za-z]", value)
        ]
        if not description_cells:
            continue
        name = " ".join(description_cells)
        part_number = payload[type_index - 1] if type_index else ""
        rows.append(
            MailboxPOLine(
                line_id=payload[0] if payload else section_index + 1,
                name=name,
                description=f"Part # {part_number}" if part_number else "",
                quantity=quantity,
                unit_price=money[0][1],
                line_total=money[1][1],
                unit=unit,
                source=source,
            )
        )

    warnings = ()
    if len(rows) != len(starts):
        warnings = (
            f"SAP Business Network extraction was incomplete: parsed {len(rows)} of "
            f"{len(starts)} displayed line-item sections.",
        )
    return True, tuple(rows), warnings


def _emrill_order_rows(text, *, source):
    """Parse Emrill's repeated PDF line blocks without treating labels as items."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    emrill_brand = any("emrill" in cell for cell in lowered[:80])
    recognized = bool(
        emrill_brand
        and (
            any("purchase order" in cell for cell in lowered[:80])
            or {"line number", "unit price", "amount delivery"}.issubset(lowered)
        )
    )
    if not recognized:
        return False, (), ()
    if not {"line number", "unit price", "amount delivery"}.issubset(lowered):
        return True, (), (
            "Emrill purchase order was recognized, but its commercial line headers were incomplete.",
        )

    description_indexes = [
        index for index, value in enumerate(cells) if EMRILL_DESCRIPTION_RE.match(value)
    ]
    rows = []
    used_units = set()
    for description_index in description_indexes:
        description_match = EMRILL_DESCRIPTION_RE.match(cells[description_index])
        description_parts = [description_match.group("name").strip()]
        for candidate in cells[description_index + 1 : description_index + 6]:
            normalized = re.sub(r"[^a-z]+", " ", candidate.casefold()).strip()
            if normalized.startswith(
                ("quantity", "amount", "warehouse", "gross total", "total discount")
            ):
                break
            if re.search(r"[A-Za-z]", candidate):
                description_parts.append(candidate)
        reference_only_description = bool(
            re.fullmatch(
                r"(?:qtn|quotation|quote)\s*(?:no|number|ref(?:erence)?)?\s*[:#._/-]*\s*[A-Z0-9._/-]+",
                description_parts[0],
                re.IGNORECASE,
            )
        )
        if reference_only_description and len(description_parts) > 1:
            name = " ".join(description_parts[1:])
            description = description_parts[0]
        else:
            name = " ".join(description_parts)
            description = ""
        unit_index = None
        amount = None
        for candidate in range(description_index - 1, max(-1, description_index - 45), -1):
            if candidate in used_units or candidate + 6 >= len(cells):
                continue
            unit_value = cells[candidate]
            if not re.fullmatch(r"[A-Z_][A-Z0-9_./-]{0,11}", unit_value, re.IGNORECASE):
                continue
            quantity = _decimal(cells[candidate + 1])
            unit_price = _decimal(cells[candidate + 2])
            discount = _decimal(cells[candidate + 3])
            percent = EMRILL_PERCENT_RE.fullmatch(cells[candidate + 4])
            vat_amount = _decimal(cells[candidate + 5])
            amount_match = EMRILL_AMOUNT_DATE_RE.match(cells[candidate + 6])
            if None in {quantity, unit_price, discount, vat_amount} or not percent or not amount_match:
                continue
            unit_index = candidate
            amount = _decimal(amount_match.group("amount"))
            break
        if unit_index is None or amount is None:
            continue
        used_units.add(unit_index)
        rows.append(
            MailboxPOLine(
                line_id=len(rows) + 1,
                name=name,
                description=description,
                quantity=_decimal(cells[unit_index + 1]),
                unit_price=_decimal(cells[unit_index + 2]),
                line_total=amount,
                unit=_portal_unit(cells[unit_index]),
                source=source,
            )
        )

    warnings = ()
    if not description_indexes:
        warnings = (
            "Emrill purchase order was recognized, but no displayed item descriptions were found.",
        )
    elif len(rows) != len(description_indexes):
        warnings = (
            f"Emrill purchase-order extraction was incomplete: parsed {len(rows)} of "
            f"{len(description_indexes)} displayed descriptions.",
        )
    return True, tuple(rows), warnings


def _imdaad_order_rows(text, *, source):
    """Parse IMDAAD's split-cell PO table and exclude repeated terms pages."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        "purchase order" in lowered[:20]
        and any("imdaad contact name" in cell for cell in lowered[:80])
    )
    if not recognized:
        return False, (), ()
    if not {"description", "uom", "qty"}.issubset(lowered):
        return True, (), (
            "IMDAAD purchase order was recognized, but its commercial line headers were incomplete.",
        )
    header_end = next(
        (
            index + 2
            for index in range(len(lowered) - 1)
            if lowered[index] == "total" and lowered[index + 1] == "amount"
        ),
        None,
    )
    if header_end is None:
        return True, (), (
            "IMDAAD purchase order was recognized, but the line-item table boundary was not found.",
        )

    rows = []
    warnings = []
    index = header_end
    while index < len(cells):
        current_label = re.sub(r"[^a-z]+", " ", lowered[index]).strip()
        if current_label in {"total amount", "total amount aed"}:
            break
        if index + 8 >= len(cells):
            warnings.append(
                f"IMDAAD purchase-order extraction was incomplete after {len(rows)} parsed line(s)."
            )
            break
        serial = cells[index]
        name = cells[index + 1]
        unit = cells[index + 2]
        quantity = _decimal(cells[index + 3])
        unit_price = _decimal(cells[index + 4])
        net_amount = _decimal(cells[index + 6])
        vat_amount = _decimal(cells[index + 7])
        gross_amount = _decimal(cells[index + 8])
        if not (
            re.fullmatch(r"\d{1,4}", serial)
            and re.search(r"[A-Za-z]", name)
            and quantity is not None
            and unit_price is not None
            and net_amount is not None
            and vat_amount is not None
            and gross_amount is not None
        ):
            warnings.append(
                f"IMDAAD purchase-order extraction stopped at an incomplete line after "
                f"{len(rows)} parsed line(s)."
            )
            break
        rows.append(
            MailboxPOLine(
                line_id=serial,
                name=name,
                quantity=quantity,
                unit_price=unit_price,
                line_total=net_amount,
                unit=_portal_unit(unit),
                source=source,
            )
        )
        index += 9
    if not rows and not warnings:
        warnings.append(
            "IMDAAD purchase order was recognized, but no commercial item lines were found."
        )
    return True, tuple(rows), tuple(warnings)


def _portal_order_rows(text, *, source):
    for parser in (
        _ariba_order_rows,
        _hotel_procurement_order_rows,
        _raq_order_rows,
        _khansaheb_order_rows,
        _dubai_holding_order_rows,
        _ecc_order_rows,
        _al_sahel_order_rows,
        _emrill_order_rows,
        _imdaad_order_rows,
    ):
        recognized, rows, warnings = parser(text, source=source)
        if recognized:
            return True, rows, warnings
    return False, (), ()


def _ecc_document_total(text):
    """Return ECC's grand total only when its reordered summary reconciles."""

    cells = _nonblank_cells(text)
    lowered = [cell.casefold() for cell in cells]
    recognized = bool(
        any("engineering contracting co" in cell for cell in lowered[:80])
        and "purchase order" in lowered[:80]
        and {"gross total", "discount", "net total", "vat"}.issubset(lowered)
    )
    if not recognized:
        return False, None
    for index, cell in enumerate(lowered):
        if cell != "gross total" or index < 2 or index + 6 >= len(cells):
            continue
        if lowered[index + 1 : index + 3] != ["discount", "net total"]:
            continue
        if lowered[index + 5] != "vat":
            continue
        grand_before = _portal_number(cells[index - 2])
        net_total = _portal_number(cells[index - 1])
        discount = _portal_number(cells[index + 3])
        vat = _portal_number(cells[index + 4])
        grand_after = _portal_number(cells[index + 6])
        if None in {grand_before, net_total, discount, vat, grand_after}:
            continue
        if (
            grand_before == grand_after
            and net_total - discount + vat == grand_after
        ):
            return True, grand_after
    # The document is ECC-shaped, but the commercial summary did not validate.
    # Do not let the generic split-label heuristic guess a VAT/discount value.
    return True, None


def _raq_document_total(text):
    """Read Sanisoft's value-before-label net amount conservatively."""

    cells = _nonblank_cells(text)
    lowered = [re.sub(r"[^a-z]+", " ", cell.casefold()).strip() for cell in cells]
    recognized = bool(
        "purchase order" in lowered[:12]
        and any("powered by sanisoft" in cell for cell in lowered)
    )
    if not recognized:
        return False, None
    for index, label in enumerate(lowered):
        if label != "net amount aed" or index == 0:
            continue
        value = _portal_number(cells[index - 1])
        has_words_label = any(
            candidate.startswith("total in words")
            for candidate in lowered[index + 1 : index + 4]
        )
        if value is not None and has_words_label:
            return True, value
    return True, None


def _document_total(document, text):
    ecc_recognized, ecc_total = _ecc_document_total(text)
    if ecc_recognized:
        return ecc_total
    raq_recognized, raq_total = _raq_document_total(text)
    if raq_recognized:
        return raq_total
    totals = document.get("totals") or {}
    if isinstance(totals, dict):
        for key in ("grand_total", "net_total", "total", "total_amount"):
            value = _decimal(totals.get(key))
            if value is not None:
                return value
    matches = list(TOTAL_LINE_RE.finditer(str(text or "")))
    if matches:
        matches.sort(
            key=lambda match: (
                "grand" in match.group("label").lower(),
                "net" in match.group("label").lower(),
                match.start(),
            ),
            reverse=True,
        )
        return _decimal(matches[0].group("value"))

    # HTML order notifications often put every table cell on its own line.
    # Preserve that structure while accepting a labelled total followed by its
    # amount on the next nonblank line.
    cells = _nonblank_cells(text)
    split_totals = []
    for index, cell in enumerate(cells[:-1]):
        label = re.sub(r"[^a-z]+", " ", cell.casefold()).strip()
        if label not in {
            "est grand total",
            "grand total",
            "grand total aed",
            "net total",
            "net total aed",
            "net amount",
            "net amount aed",
            "total aed",
            "total amount",
            "total amount aed",
            "total amount due",
            "total amount due aed",
            "invoice total",
        }:
            continue
        values = []
        for candidate in cells[index + 1 : index + 6]:
            match = re.fullmatch(
                r"\(?\s*(?:AED|DHS?|USD|EUR|GBP)?\s*"
                r"(?P<value>\d{1,3}(?:,\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
                r"\s*(?:AED|DHS?|USD|EUR|GBP)?\s*\)?",
                candidate,
                re.IGNORECASE,
            )
            if not match:
                break
            values.append(_decimal(match.group("value")))
        # Multiple adjacent numerics are usually a visually reordered summary
        # (net, discount, VAT, grand total). Picking either endpoint can turn a
        # tax value into the PO total, so ambiguous layouts fail closed.
        if len(values) == 1:
            split_totals.append(
                (
                    "grand" in label,
                    "net" in label,
                    index,
                    values[-1],
                )
            )
    if not split_totals:
        return None
    split_totals.sort(reverse=True)
    return split_totals[0][3]


def _references(*chunks):
    found = extract_po_references("\n".join(str(chunk or "") for chunk in chunks))
    quote_refs = tuple(reference["value"] for reference in found if reference.get("kind") == "quotation")
    po_refs = tuple(reference["value"] for reference in found if reference.get("kind") == "po")
    return po_refs, quote_refs


def _dedupe_references(values):
    return tuple(dict.fromkeys(str(value) for value in values if str(value or "").strip()))


def _attachment_references(subject, body, filename, attachment_text):
    """Return references authoritative to one attachment.

    A message body can legitimately mention several quotations because it
    introduces several attached LPOs.  References printed on the attachment
    itself (including its filename) therefore take precedence.  Surrounding
    subject/body references are used only when the attachment has none; a
    mixed contextual set then remains mixed and the matcher fails closed.
    """

    local_po_refs, local_quote_refs = _references(filename, attachment_text)
    context_po_refs, context_quote_refs = _references(subject, body)
    po_refs = local_po_refs or context_po_refs
    quote_refs = local_quote_refs or context_quote_refs
    return _dedupe_references(po_refs), _dedupe_references(quote_refs)


def _normalized_row_value(value):
    return re.sub(r"[^a-z0-9]+", "", str(value or "").casefold())


def _rows_equivalent(left, right):
    """Check that every body-side field is represented by an attachment row."""

    if _normalized_row_value(left.name) != _normalized_row_value(right.name):
        return False
    left_unit = _normalized_row_value(left.unit)
    right_unit = _normalized_row_value(right.unit)
    if left_unit and right_unit and left_unit != right_unit:
        return False
    for field in ("quantity", "unit_price", "line_total"):
        left_value = getattr(left, field)
        right_value = getattr(right, field)
        if left_value is not None and (
            right_value is None or left_value != right_value
        ):
            return False
    return True


def _body_mirrors_attachment(
    body_rows,
    body_total,
    body_po_refs,
    body_quote_refs,
    body_has_order_signal,
    attachment_rows,
    attachment_total,
    attachment_po_refs,
    attachment_quote_refs,
    attachment_has_order_signal,
):
    """Conservatively identify a body table already represented by a file."""

    if not body_rows or not attachment_rows:
        return False
    remaining = list(attachment_rows)
    for body_row in body_rows:
        index = next(
            (
                candidate_index
                for candidate_index, attachment_row in enumerate(remaining)
                if _rows_equivalent(body_row, attachment_row)
            ),
            None,
        )
        if index is None:
            return False
        remaining.pop(index)
    if remaining:
        return False
    if body_total is not None and attachment_total is not None and body_total != attachment_total:
        return False
    if body_total is not None and attachment_total is None:
        return False
    body_po_ref_keys = {
        re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        for value in body_po_refs
        if value
    }
    attachment_po_ref_keys = {
        re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        for value in attachment_po_refs
        if value
    }
    if body_po_ref_keys and not body_po_ref_keys.issubset(attachment_po_ref_keys):
        return False
    if body_has_order_signal and not attachment_has_order_signal:
        return False
    body_ref_keys = {
        re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        for value in body_quote_refs
        if value
    }
    attachment_ref_keys = {
        re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())
        for value in attachment_quote_refs
        if value
    }
    if body_ref_keys and not body_ref_keys.issubset(attachment_ref_keys):
        return False
    return True


def _warning_tuple(values):
    if isinstance(values, str):
        values = [values]
    elif not isinstance(values, (list, tuple, set, frozenset)):
        values = [] if values is None else [values]
    return tuple(
        dict.fromkeys(
            str(value).strip()
            for value in (values or [])
            if str(value or "").strip()
        )
    )


def _material_warnings(warnings, meta=None):
    keywords = (
        "aggregate",
        "ambiguous",
        "arithmetic",
        "confidence",
        "failed",
        "fallback",
        "incomplete",
        "no clear header",
        "no item lines",
        "not enabled",
        "ocr",
        "stopped reading",
        "total",
        "unsupported",
    )
    material = [
        warning
        for warning in _warning_tuple(warnings)
        if any(keyword in warning.lower() for keyword in keywords)
    ]
    if isinstance(meta, dict) and meta.get("aggregate_po_summary_detected"):
        material.append("Aggregate PO summary rows were detected and require staff verification.")
    return tuple(dict.fromkeys(material))


def _variant_message(
    inventory,
    *,
    rows,
    text,
    po_refs,
    quote_refs,
    total,
    parser_warnings=(),
    material_warnings=(),
    quotation_references_review_only=False,
    source_kind="",
    document_text="",
    document_filename="",
):
    return CanonicalMailboxMessage(
        message_id=inventory.gmail_message_id,
        sender=inventory.sender,
        recipients=tuple(filter(None, [inventory.recipients, inventory.cc])),
        subject=inventory.subject,
        body=text,
        received_at=inventory.sent_at,
        parsed_rows=rows,
        lpo_references=po_refs,
        quotation_references=quote_refs,
        quotation_references_are_authoritative=True,
        quotation_references_are_review_only=quotation_references_review_only,
        document_total=total,
        parser_warnings=_warning_tuple(parser_warnings),
        material_warnings=_warning_tuple(material_warnings),
        source_kind=source_kind,
        document_text=document_text,
        document_filename=document_filename,
    )


def document_variants(inventory):
    variants = []
    attachment_reference_keys = set()
    base_body = inventory.newest_body_text or inventory.snippet or ""
    try:
        body_portal_recognized, body_rows, body_warnings = _portal_order_rows(
            base_body,
            source="email_body",
        )
        if not body_portal_recognized:
            body_preview = (
                parse_text_preview(base_body)
                if base_body.strip()
                else {"lines": [], "warnings": []}
            )
            body_rows = _canonical_rows(
                _body_item_rows(body_preview.get("lines") or []),
                source="email_body",
            )
            body_warnings = _warning_tuple(body_preview.get("warnings") or [])
    except Exception:
        body_rows = ()
        body_warnings = ("Email body parsing failed and requires staff review.",)
    body_po_refs, body_quote_refs = _references(inventory.subject, base_body)
    body_total = _document_total({}, base_body)
    body_has_order_signal = bool(
        body_po_refs
        or BODY_ORDER_SIGNAL_RE.search(f"{inventory.subject}\n{base_body}")
    )
    body_mirrors_file = False

    for attachment in inventory.attachment_manifest or []:
        if not isinstance(attachment, dict):
            continue
        is_manual_attachment = bool(
            attachment.get("manual_review_required")
            or attachment.get("status") == "manual_review"
        )
        if attachment.get("status") != "parsed" and not is_manual_attachment:
            continue
        filename = str(attachment.get("filename") or "")
        attachment_text = str(attachment.get("original_text") or "")
        portal_recognized, rows, portal_warnings = _portal_order_rows(
            attachment_text,
            source=filename or "attachment",
        )
        if not portal_recognized:
            rows = _canonical_rows(
                attachment.get("lines") or [],
                source=filename or "attachment",
            )
        local_attachment_po_refs, local_attachment_quote_refs = _references(
            filename,
            attachment_text,
        )
        po_refs, quote_refs = _attachment_references(
            inventory.subject,
            base_body,
            filename,
            attachment_text,
        )
        attachment_reference_keys.update(
            (kind, re.sub(r"[^A-Z0-9]+", "", str(value or "").upper()))
            for kind, values in (
                ("po", local_attachment_po_refs),
                ("quote", local_attachment_quote_refs),
            )
            for value in values
            if value
        )
        attachment_total = _document_total(attachment, attachment_text)
        attachment_has_order_signal = bool(
            local_attachment_po_refs
            or BODY_ORDER_SIGNAL_RE.search(f"{filename}\n{attachment_text}")
        )
        attachment_meta = attachment.get("meta") or {}
        mailbox_ai_meta = (
            attachment_meta.get("mailbox_ai_vision")
            if isinstance(attachment_meta, dict)
            else {}
        ) or {}
        quotation_references_review_only = bool(
            attachment.get("result_source") == AI_SOURCE_VISION
            or (
                isinstance(mailbox_ai_meta, dict)
                and mailbox_ai_meta.get("review_only")
            )
        )
        if _body_mirrors_attachment(
            body_rows,
            body_total,
            body_po_refs,
            body_quote_refs,
            body_has_order_signal,
            rows,
            attachment_total,
            local_attachment_po_refs,
            local_attachment_quote_refs,
            attachment_has_order_signal,
        ):
            body_mirrors_file = True
        attachment_id = str(
            attachment.get("part_id")
            or attachment.get("source_gmail_attachment_id")
            or attachment.get("attachment_id")
            or ""
        )
        combined_text = "\n".join(filter(None, [base_body, attachment_text, filename]))
        attachment_warnings = _warning_tuple(
            attachment.get("warnings")
            or ([attachment.get("reason")] if is_manual_attachment else [])
        )
        parser_warnings = _warning_tuple(
            [
                *attachment_warnings,
                *portal_warnings,
            ]
        )
        variants.append(
            DocumentVariant(
                message=_variant_message(
                    inventory,
                    rows=rows,
                    text=combined_text,
                    po_refs=po_refs,
                    quote_refs=quote_refs,
                    total=attachment_total,
                    parser_warnings=parser_warnings,
                    material_warnings=_material_warnings(
                        parser_warnings,
                        attachment.get("meta") or {},
                    ),
                    quotation_references_review_only=quotation_references_review_only,
                    source_kind="attachment",
                    document_text=attachment_text,
                    document_filename=filename,
                ),
                source_kind="attachment",
                attachment_id=attachment_id,
                filename=filename,
                source_sha256=str(attachment.get("source_sha256") or ""),
                extracted_text=attachment_text,
                lpo_references=po_refs,
                quotation_references=quote_refs,
            )
        )

    # A body table can be an order in its own right even when an unrelated file
    # is attached. Keep that source unless its complete commercial row set is
    # already represented by a parsed attachment. For a row-less body alongside
    # files, require an explicit order plus a reference before retaining it;
    # generic "see attached" prose remains context only.
    body_reference_keys = {
        (kind, re.sub(r"[^A-Z0-9]+", "", str(value or "").upper()))
        for kind, values in (("po", body_po_refs), ("quote", body_quote_refs))
        for value in values
        if value
    }
    include_body = not variants
    if variants and body_rows and not body_mirrors_file:
        include_body = True
    elif (
        variants
        and not body_rows
        and body_has_order_signal
        and bool(body_reference_keys - attachment_reference_keys)
    ):
        include_body = True
    if not include_body:
        return tuple(variants)

    body_sha = hashlib.sha256(
        "\n".join([inventory.gmail_message_id, inventory.subject or "", base_body]).encode(
            "utf-8", errors="ignore"
        )
    ).hexdigest()
    # Body evidence matters even without rows: an exact quotation reference and
    # order acknowledgement should remain visible, but can only be ambiguous.
    variants.append(
        DocumentVariant(
            message=_variant_message(
                inventory,
                rows=body_rows,
                text=base_body,
                po_refs=body_po_refs,
                quote_refs=body_quote_refs,
                total=body_total,
                parser_warnings=body_warnings,
                material_warnings=_material_warnings(body_warnings),
                source_kind="email_body",
                document_text=base_body,
            ),
            source_kind="email_body",
            source_sha256=body_sha,
            extracted_text=base_body,
            lpo_references=body_po_refs,
            quotation_references=body_quote_refs,
        )
    )
    return tuple(variants)


def eligible_quotations():
    contacts = Prefetch(
        "company__contacts",
        queryset=CompanyContact.objects.filter(is_active=True).only("company_id", "email"),
        to_attr="active_po_contacts",
    )
    queryset = (
        Quotation.objects.filter(
            is_historical_import=False,
            status__in=[Quotation.STATUS_SENT, Quotation.STATUS_FINALIZED],
        )
        .select_related("company", "contact")
        .prefetch_related(
            Prefetch("lines", queryset=QuotationLine.objects.order_by("sort_order", "id")),
            contacts,
        )
    )
    canonical = []
    for quote in queryset:
        emails = {
            str(getattr(quote.company, "email", "") or "").strip().lower(),
            str(getattr(quote.contact, "email", "") or "").strip().lower(),
        }
        emails.update(
            str(contact.email or "").strip().lower()
            for contact in getattr(quote.company, "active_po_contacts", [])
        )
        lines = tuple(
            EligibleQuoteLine(
                line_id=line.id,
                name=line.item_name_snapshot,
                description=line.description,
                quantity=line.quantity,
                unit_price=line.unit_price,
                line_total=line.line_subtotal,
                gross_line_total=line.line_total,
                unit=line.unit,
            )
            for line in quote.lines.all()
        )
        canonical.append(
            EligibleQuotation(
                quote_id=quote.id,
                quotation_number=quote.quotation_number,
                sent_at=quote.sent_at,
                finalized_at=quote.finalized_at,
                created_at=quote.created_at,
                company_name=quote.company.name,
                customer_emails=tuple(sorted(email for email in emails if email)),
                lines=lines,
                grand_total=(quote.subtotal or Decimal("0"))
                + (quote.vat_total or Decimal("0")),
            )
        )
    return tuple(canonical)


def _proposals_for_message(inventory, quotes):
    # Rank every attachment/body as an independent document. A single Gmail
    # message can legitimately carry LPOs for multiple quotations; collapsing
    # all variants to one message-level winner silently discards those orders.
    best_by_source_and_quote = {}
    variant_count = 0
    for variant in document_variants(inventory):
        variant_count += 1
        result = rank_message_to_quotations(variant.message, quotes)
        variant_decisive = bool(inventory.auto_link_eligible and result.status == AUTOMATIC)
        candidates = result.candidates[:1] if variant_decisive else result.candidates[:3]
        source_key = QuotationPOEvidence.build_source_key(
            source_sha256=variant.source_sha256,
            selected_attachment_id=variant.attachment_id,
            gmail_message_id=inventory.gmail_message_id,
        )
        for candidate in candidates:
            proposal = ProposedEvidence(
                quote_id=int(candidate.quote_id),
                candidate=candidate,
                variant=variant,
                result_status=result.status,
                result_reason=result.reason,
                decisive=variant_decisive,
                automatic_blockers=tuple(result.automatic_blockers),
            )
            identity = (source_key, proposal.quote_id)
            current = best_by_source_and_quote.get(identity)
            if current is None or candidate.score > current.candidate.score:
                best_by_source_and_quote[identity] = proposal

    ranked = sorted(
        best_by_source_and_quote.values(),
        key=lambda proposal: (
            proposal.decisive,
            proposal.candidate.score,
            proposal.candidate.item_coverage,
            proposal.candidate.quantity_exact_count,
            proposal.candidate.price_exact_count,
        ),
        reverse=True,
    )
    if not ranked:
        return (), False, variant_count
    return tuple(ranked), any(proposal.decisive for proposal in ranked), variant_count


def _manifest_with_selection(inventory, variant):
    manifest = []
    for attachment in inventory.attachment_manifest or []:
        copied = dict(attachment) if isinstance(attachment, dict) else attachment
        if isinstance(copied, dict):
            identifiers = {
                str(copied.get("attachment_id") or ""),
                str(copied.get("source_gmail_attachment_id") or ""),
                str(copied.get("part_id") or ""),
            }
            copied["is_selected"] = bool(
                variant.source_kind == "attachment"
                and variant.attachment_id
                and variant.attachment_id in identifiers
            )
        manifest.append(copied)
    return manifest


def _signal_payload(proposal, *, decisive, variant_count):
    candidate = proposal.candidate.as_dict()
    matches = candidate.get("matched_lines") or []
    return {
        "decision": "decisive_review_candidate" if decisive else "ambiguous_review_candidate",
        "reason": proposal.result_reason,
        "automatic_blockers": list(proposal.automatic_blockers),
        "candidate": candidate,
        "components": candidate.get("components") or [],
        "items": [
            {
                "label": match.get("po_name") or "PO item",
                "detail": (
                    f"Matched to {match.get('quote_name') or 'quotation item'} "
                    f"({round(float(match.get('name_similarity') or 0) * 100)}% similarity)"
                ),
                "matched": True,
            }
            for match in matches
        ],
        "quantities": [
            {
                "label": match.get("po_name") or "PO item",
                "value": str(match.get("quantity_result") or "unknown").replace("_", " "),
                "matched": match.get("quantity_result") in {"exact", "reduced"},
            }
            for match in matches
        ],
        "timing": [
            {
                "label": "Quote to LPO",
                "detail": component.get("detail") or "",
                "matched": float(component.get("score") or 0) >= 0,
            }
            for component in candidate.get("components") or []
            if component.get("signal") == "time_distance"
        ],
        "source": {
            "kind": proposal.variant.source_kind,
            "attachment_id": proposal.variant.attachment_id,
            "filename": proposal.variant.filename,
        },
        "lpo_references": list(proposal.variant.lpo_references),
        "quotation_references": list(proposal.variant.quotation_references),
        "document_variant_count": variant_count,
    }


def _attachment_identifiers(attachment):
    if not isinstance(attachment, dict):
        return set()
    return {
        value
        for value in (
            str(attachment.get("attachment_id") or ""),
            str(attachment.get("source_gmail_attachment_id") or ""),
            str(attachment.get("part_id") or ""),
        )
        if value
    }


def _evidence_matches_attachment_variant(evidence, inventory, variant):
    """Recognize one source across the old Gmail-token and stable-part schemes."""

    if variant.source_kind != "attachment" or not variant.attachment_id:
        return False
    evidence_hash = str(evidence.source_sha256 or "").strip().lower()
    variant_hash = str(variant.source_sha256 or "").strip().lower()
    if evidence_hash and variant_hash and evidence_hash == variant_hash:
        return True

    current_sets = [
        identifiers
        for attachment in (inventory.attachment_manifest or [])
        if variant.attachment_id in (identifiers := _attachment_identifiers(attachment))
    ]
    selected_id = str(evidence.selected_attachment_id or "")
    stored_sets = [
        identifiers
        for attachment in (evidence.attachments or [])
        if (
            selected_id in (identifiers := _attachment_identifiers(attachment))
            or (
                isinstance(attachment, dict)
                and attachment.get("is_selected") is True
            )
        )
    ]
    if len(current_sets) != 1 or len(stored_sets) != 1:
        return bool(selected_id and selected_id == variant.attachment_id)
    return bool(current_sets[0].intersection(stored_sets[0]))


def _evidence_review_priority(evidence):
    if evidence.link_approved_at or evidence.status == QuotationPOEvidence.STATUS_PARSED:
        return 3
    if evidence.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
        return 2
    return 0


def _superseded_identity_key(evidence):
    digest = hashlib.sha256(
        f"{evidence.pk}:{evidence.source_key}".encode("utf-8")
    ).hexdigest()
    return f"superseded:{evidence.pk}:{digest}"


def _matching_reason(proposal, decisive):
    candidate = proposal.candidate
    components = "; ".join(
        component.detail for component in candidate.components if component.score != 0
    )
    prefix = "Decisive mailbox-wide review match" if decisive else "Mailbox-wide match needs assignment"
    blockers = "; ".join(proposal.automatic_blockers)
    detail = ". ".join(part for part in [components, blockers] if part)
    return f"{prefix}: score {candidate.score:.1f}. {detail}"[:4000]


def _locked_lineage_message_evidence_queryset(connection, message_id):
    """Lock evidence rows across OAuth rotations without locking a nullable join."""

    return (
        QuotationPOEvidence.objects.select_for_update(of=("self",))
        .filter(
            gmail_connection_lineage_q(connection),
            gmail_message_id=message_id,
        )
        .order_by("id")
    )


def _store_proposal(inventory, proposal, match_run, actor, *, decisive, variant_count):
    candidate = proposal.candidate
    desired_status = (
        QuotationPOEvidence.STATUS_CANDIDATE
        if decisive
        else QuotationPOEvidence.STATUS_AMBIGUOUS
    )
    desired_error = ""
    if not inventory.auto_link_eligible:
        desired_status = QuotationPOEvidence.STATUS_AMBIGUOUS
        desired_error = "Gmail marked this message as Spam or Trash; staff must review it manually."
    elif not decisive:
        desired_error = "Items, quantities, timing, or candidate margin require staff assignment."

    source_key = QuotationPOEvidence.build_source_key(
        source_sha256=proposal.variant.source_sha256,
        selected_attachment_id=proposal.variant.attachment_id,
        gmail_message_id=inventory.gmail_message_id,
    )
    legacy_message_key = QuotationPOEvidence.build_source_key(
        gmail_message_id=inventory.gmail_message_id,
    )
    with transaction.atomic():
        peers = list(
            _locked_lineage_message_evidence_queryset(
                inventory.gmail_connection,
                inventory.gmail_message_id,
            )
        )
        source_peers = [
            peer
            for peer in peers
            if peer.source_key in {source_key, legacy_message_key}
            or _evidence_matches_attachment_variant(
                peer,
                inventory,
                proposal.variant,
            )
        ]
        exact_candidates = [
            peer
            for peer in source_peers
            if peer.quotation_id == proposal.quote_id and peer.source_key == source_key
        ]
        exact_existing = next(
            (
                peer
                for peer in exact_candidates
                if peer.gmail_connection_id == inventory.gmail_connection_id
            ),
            exact_candidates[0] if exact_candidates else None,
        )
        compatible = [
            peer
            for peer in source_peers
            if peer.quotation_id == proposal.quote_id
            and _evidence_matches_attachment_variant(
                peer,
                inventory,
                proposal.variant,
            )
        ]
        reviewed_compatible = [
            peer for peer in compatible if _evidence_review_priority(peer)
        ]
        if reviewed_compatible:
            existing = max(
                reviewed_compatible,
                key=lambda peer: (
                    _evidence_review_priority(peer),
                    peer.source_key == source_key,
                    peer.pk,
                ),
            )
        elif exact_existing is not None:
            existing = exact_existing
        elif len(compatible) == 1:
            existing = compatible[0]
        else:
            existing = None
        legacy_existing = next(
            (
                peer
                for peer in source_peers
                if peer.quotation_id == proposal.quote_id
                and peer.source_key == legacy_message_key
                and peer.source_key != source_key
            ),
            None,
        )
        if (
            existing is not None
            and exact_existing is not None
            and existing.pk != exact_existing.pk
            and _evidence_review_priority(existing)
            > _evidence_review_priority(exact_existing)
        ):
            exact_existing.source_key = _superseded_identity_key(exact_existing)
            update_fields = ["source_key", "updated_at"]
            if not _evidence_review_priority(exact_existing):
                exact_existing.status = QuotationPOEvidence.STATUS_SUPERSEDED
                exact_existing.error = (
                    "Superseded while consolidating a rotating Gmail attachment token "
                    "into the same previously reviewed MIME part."
                )
                update_fields.extend(["status", "error"])
            exact_existing.save(update_fields=update_fields)
        if (
            existing is not None
            and proposal.variant.source_kind == "attachment"
            and _evidence_matches_attachment_variant(
                existing,
                inventory,
                proposal.variant,
            )
            and (
                existing.source_key != source_key
                or existing.selected_attachment_id != proposal.variant.attachment_id
            )
        ):
            existing.source_key = source_key
            existing.selected_attachment_id = proposal.variant.attachment_id
            update_fields = ["source_key", "selected_attachment_id", "updated_at"]
            if existing.mailbox_message_id is None:
                existing.mailbox_message = inventory
                update_fields.append("mailbox_message")
            existing.save(update_fields=update_fields)
        if existing is None and legacy_existing is not None:
            if (
                legacy_existing.status
                in {
                    QuotationPOEvidence.STATUS_PARSED,
                    QuotationPOEvidence.STATUS_NOT_RELEVANT,
                }
                or legacy_existing.link_approved_at
            ):
                if legacy_existing.mailbox_message_id is None:
                    legacy_existing.mailbox_message = inventory
                    legacy_existing.save(update_fields=["mailbox_message", "updated_at"])
                return legacy_existing, False
            # An unreviewed legacy message-level candidate can be safely
            # upgraded to the exact attachment/body identity in place.
            legacy_existing.source_key = source_key
            legacy_existing.save(update_fields=["source_key", "updated_at"])
            existing = legacy_existing
        if existing and existing.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
            # A staff dismissal is a reviewed source record. Reconciliation may
            # attach its canonical inventory pointer, but must not rewrite any
            # source, extraction, selection, status, or review-note fields.
            if existing.mailbox_message_id is None:
                existing.mailbox_message = inventory
                existing.save(update_fields=["mailbox_message", "updated_at"])
            return existing, False
        reviewed_peers = [
            peer
            for peer in source_peers
            if peer.quotation_id != proposal.quote_id
            and (peer.status == QuotationPOEvidence.STATUS_PARSED or peer.link_approved_at)
        ]
        if reviewed_peers:
            desired_status = QuotationPOEvidence.STATUS_AMBIGUOUS
            desired_error = "This Gmail message is already approved for another quotation; review the source manually."
        elif decisive:
            peer_ids = [
                peer.id
                for peer in source_peers
                if peer.quotation_id != proposal.quote_id
                and peer.status
                in {
                    QuotationPOEvidence.STATUS_CANDIDATE,
                    QuotationPOEvidence.STATUS_AMBIGUOUS,
                    QuotationPOEvidence.STATUS_FAILED,
                }
            ]
            if peer_ids:
                QuotationPOEvidence.objects.filter(id__in=peer_ids).update(
                    status=QuotationPOEvidence.STATUS_SUPERSEDED,
                    error="Superseded by a decisive mailbox-wide item/quantity/time match.",
                    updated_at=timezone.now(),
                )

        defaults = {
            "mailbox_message": inventory,
            "mailbox_match_run": match_run,
            "match_signals": _signal_payload(proposal, decisive=decisive, variant_count=variant_count),
            "selected_attachment_id": proposal.variant.attachment_id,
            "selected_attachment_filename": proposal.variant.filename,
            "source_key": source_key,
            "quote_reference_present": bool(candidate.exact_quote_reference),
            "matched_quote_reference": candidate.quotation_number if candidate.exact_quote_reference else "",
            "gmail_connection": inventory.gmail_connection,
            "mailbox_email": inventory.mailbox_email,
            "gmail_thread_id": inventory.gmail_thread_id,
            "sender": inventory.sender[:500],
            "recipients": ", ".join(filter(None, [inventory.recipients, inventory.cc])),
            "subject": inventory.subject[:500],
            "sent_at": inventory.sent_at,
            "snippet": inventory.snippet,
            "extracted_text": proposal.variant.extracted_text,
            "attachments": _manifest_with_selection(inventory, proposal.variant),
            "source_sha256": proposal.variant.source_sha256,
            "matching_reason": _matching_reason(proposal, decisive),
            "confidence": max(0, min(100, int(round(candidate.score)))),
            "status": desired_status,
            "error": desired_error,
            "created_by": actor if getattr(actor, "is_authenticated", False) else None,
        }
        if existing:
            defaults.pop("created_by", None)
            if existing.status == QuotationPOEvidence.STATUS_PARSED or existing.link_approved_at:
                for field in (
                    "attachments",
                    "extracted_text",
                    "source_sha256",
                    "status",
                    "error",
                    "selected_attachment_id",
                    "selected_attachment_filename",
                ):
                    defaults.pop(field, None)
            elif existing.status == QuotationPOEvidence.STATUS_NOT_RELEVANT:
                defaults.pop("status", None)
                defaults.pop("error", None)
            # Keep the credential-row provenance when a same-mailbox OAuth
            # rotation reuses reviewed evidence. The canonical inventory link
            # moves forward, while the single reviewed source row remains the
            # authoritative decision instead of being duplicated.
            defaults.pop("gmail_connection", None)
            update_fields = []
            for field, value in defaults.items():
                if getattr(existing, field) != value:
                    setattr(existing, field, value)
                    update_fields.append(field)
            if update_fields:
                existing.save(update_fields=[*update_fields, "updated_at"])
            evidence, created = existing, False
        else:
            evidence, created = QuotationPOEvidence.objects.update_or_create(
                quotation_id=proposal.quote_id,
                gmail_connection=inventory.gmail_connection,
                gmail_message_id=inventory.gmail_message_id,
                source_key=source_key,
                defaults=defaults,
            )
    return evidence, created


def _link_existing_evidence_to_inventory(audit_run):
    messages = {
        message.gmail_message_id: message
        for message in MailboxPOMessage.objects.filter(
            Q(audit_memberships__audit_run=audit_run) | Q(last_seen_run=audit_run),
            gmail_connection=audit_run.gmail_connection,
        )
        .only("id", "gmail_message_id")
        .distinct()
    }
    linked = 0
    queryset = QuotationPOEvidence.objects.filter(
        gmail_connection_lineage_q(audit_run.gmail_connection),
        gmail_message_id__in=messages.keys(),
    ).exclude(
        mailbox_message_id__in=[message.id for message in messages.values()]
    ).only("id", "gmail_message_id", "mailbox_message_id")
    for evidence in queryset.iterator(chunk_size=200):
        evidence.mailbox_message = messages[evidence.gmail_message_id]
        evidence.save(update_fields=["mailbox_message", "updated_at"])
        linked += 1
    return linked


def _normalized_order_reference(value):
    rendered = str(value or "").strip().upper()
    prefix_match = re.match(
        r"^\s*(?P<prefix>LOCAL\s+PURCHASE\s+ORDER|PURCHASE\s+ORDER|"
        r"L\.?\s*P\.?\s*O\.?|M\.?\s*P\.?\s*O\.?|P\.?\s*O\.?)"
        r"\s*(?:NO\.?|NUMBER|#)?\s*[:#-]?\s*",
        rendered,
    )
    prefix = ""
    if prefix_match:
        prefix_letters = re.sub(r"[^A-Z]+", "", prefix_match.group("prefix"))
        prefix = {
            "LOCALPURCHASEORDER": "LPO",
            "PURCHASEORDER": "PO",
        }.get(prefix_letters, prefix_letters)
        rendered = rendered[prefix_match.end() :]
    key = re.sub(r"[^A-Z0-9]+", "", rendered)
    if prefix and key and (key.isdigit() or len(key) >= 4):
        return f"{prefix}{key}"
    return key if len(key) >= 4 else ""


def _evidence_signatures(evidence):
    signals = evidence.match_signals if isinstance(evidence.match_signals, dict) else {}
    lpo_refs = signals.get("lpo_references") or []
    normalized_refs = sorted(
        {
            key
            for value in lpo_refs
            if (key := _normalized_order_reference(value))
        }
    )
    company_id = getattr(evidence.quotation, "company_id", None)
    signatures = [f"po:company:{company_id}:{key}" for key in normalized_refs]
    source_hash = str(evidence.source_sha256 or "").strip().lower()
    if source_hash:
        signatures.append(f"sha:{source_hash}")
    elif str(evidence.source_key or "").startswith("sha256:"):
        signatures.append(f"sha:{str(evidence.source_key).split(':', 1)[1].lower()}")
    return tuple(dict.fromkeys(signatures)), tuple(normalized_refs)


def _number(value, default=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed == parsed else default


def _evidence_quality(evidence):
    signals = evidence.match_signals if isinstance(evidence.match_signals, dict) else {}
    candidate = signals.get("candidate") if isinstance(signals.get("candidate"), dict) else {}
    components = candidate.get("components") or signals.get("components") or []
    customer_score = max(
        [
            _number(component.get("score"))
            for component in components
            if isinstance(component, dict) and component.get("signal") == "customer_identity"
        ]
        or [0.0]
    )
    time_score = max(
        [
            _number(component.get("score"))
            for component in components
            if isinstance(component, dict) and component.get("signal") == "time_distance"
        ]
        or [0.0]
    )
    matched_count = max(0, int(_number(candidate.get("po_line_count"), 0)))
    safe_quantities = int(_number(candidate.get("quantity_exact_count"), 0)) + int(
        _number(candidate.get("quantity_reduced_count"), 0)
    )
    quantity_coverage = safe_quantities / matched_count if matched_count else 0.0
    return {
        "exact_reference": bool(
            evidence.quote_reference_present or candidate.get("exact_quote_reference")
        ),
        "score": _number(candidate.get("score"), _number(evidence.confidence)),
        "item_coverage": _number(candidate.get("item_coverage")),
        "quantity_coverage": quantity_coverage,
        "commercial_coverage": _number(candidate.get("commercial_row_coverage")),
        "customer_score": customer_score,
        "time_score": time_score,
    }


def _evidence_quality_key(evidence):
    quality = _evidence_quality(evidence)
    return (
        _evidence_review_priority(evidence),
        quality["exact_reference"],
        quality["score"],
        quality["item_coverage"],
        quality["quantity_coverage"],
        quality["commercial_coverage"],
        quality["customer_score"],
        quality["time_score"],
        evidence.sent_at.timestamp() if evidence.sent_at else 0.0,
        -evidence.id,
    )


def _cross_quote_near_tie(winner, contender):
    winner_quality = _evidence_quality(winner)
    contender_quality = _evidence_quality(contender)
    return bool(
        _evidence_review_priority(winner) == _evidence_review_priority(contender)
        and winner_quality["exact_reference"] == contender_quality["exact_reference"]
        and winner_quality["score"] - contender_quality["score"] <= 5.0
        and winner_quality["item_coverage"] - contender_quality["item_coverage"] <= 0.10
        and winner_quality["quantity_coverage"] - contender_quality["quantity_coverage"] <= 0.10
    )


def _explicit_revision_rank(evidence):
    signals = evidence.match_signals if isinstance(evidence.match_signals, dict) else {}
    source = signals.get("source") if isinstance(signals.get("source"), dict) else {}
    source_kind = str(source.get("kind") or "").casefold()
    extracted_text = str(evidence.extracted_text or "")
    if source_kind == "attachment" or (
        not source_kind and evidence.selected_attachment_filename
    ):
        extracted_header = "\n".join(
            line
            for line in extracted_text.splitlines()[:20]
            if line.strip()
        )[:2000]
        chunks = [
            str(evidence.selected_attachment_filename or ""),
            extracted_header,
        ]
    else:
        chunks = [
            str(evidence.subject or ""),
            extracted_text[:4000],
        ]
    text = "\n".join(
        filter(
            None,
            chunks,
        )
    )
    numbered = list(
        re.finditer(
            r"\b(?:rev(?:ision)?|ver(?:sion)?)\s*[:#._-]?\s*(\d{1,3})\b",
            text,
            re.IGNORECASE,
        )
    )
    marker = bool(
        numbered
        or re.search(
            r"\b(?:revised|amended|amendment|change\s+order)\b",
            text,
            re.IGNORECASE,
        )
    )
    if not marker:
        return None
    revision_number = max((int(match.group(1)) for match in numbered), default=0)
    sent_at = evidence.sent_at.timestamp() if evidence.sent_at else 0.0
    return revision_number, sent_at


def _resolve_same_quote_revisions(members, surviving, superseded_reasons, ambiguous_ties):
    members = [evidence for evidence in members if evidence.id in surviving]
    unreviewed = [evidence for evidence in members if not _evidence_review_priority(evidence)]
    if len(unreviewed) <= 1:
        return
    revision_candidates = [
        (rank, evidence)
        for evidence in unreviewed
        if (rank := _explicit_revision_rank(evidence)) is not None
    ]
    if revision_candidates:
        best_rank = max(rank for rank, _evidence in revision_candidates)
        best_revisions = [
            evidence
            for rank, evidence in revision_candidates
            if rank == best_rank
        ]
        if len(best_revisions) == 1:
            revision = best_revisions[0]
            revision_time = revision.sent_at.timestamp() if revision.sent_at else 0.0
            other_times = [
                evidence.sent_at.timestamp() if evidence.sent_at else 0.0
                for evidence in unreviewed
                if evidence.id != revision.id
            ]
            if not other_times or revision_time >= max(other_times):
                for older in unreviewed:
                    if older.id == revision.id:
                        continue
                    surviving.discard(older.id)
                    superseded_reasons[older.id] = (
                        "Superseded by an explicitly revised/amended newer copy of the same PO/LPO."
                    )
                return
    # Different bytes with the same PO number may be amendments. Without an
    # explicit revision marker, retaining them for staff is safer than silently
    # discarding a changed order.
    ambiguous_ties.update(evidence.id for evidence in unreviewed)


def _dedupe_and_cap(active_ids, *, max_per_quote):
    """Choose canonical review evidence across the complete mailbox snapshot.

    A MIME hash identifies an exact source, while a normalized PO/LPO reference
    joins resends and forwards of that source across Gmail messages. Reviewed
    decisions are immutable. Distinct PO references on one quotation remain
    active because they can represent genuine partial/repeat orders.
    """

    evidence_by_id = {
        evidence.id: evidence
        for evidence in QuotationPOEvidence.objects.filter(id__in=active_ids).select_related(
            "quotation__company"
        )
    }
    surviving = set(evidence_by_id)
    superseded_reasons = {}
    ambiguous_ties = set()
    groups = defaultdict(list)
    refs_by_id = {}
    for evidence in evidence_by_id.values():
        signatures, normalized_refs = _evidence_signatures(evidence)
        refs_by_id[evidence.id] = normalized_refs
        for signature in signatures:
            groups[signature].append(evidence)

    for signature in sorted(
        groups,
        key=lambda value: (0 if value.startswith("sha:") else 1, value),
    ):
        members = [evidence for evidence in groups[signature] if evidence.id in surviving]
        if len(members) <= 1:
            continue
        by_quote = defaultdict(list)
        for evidence in members:
            by_quote[evidence.quotation_id].append(evidence)

        # A SHA is globally unique to exact bytes and can be canonicalized
        # aggressively. A PO number is only company-scoped and distinct hashes
        # can be real amendments, so they are resolved below with more care.
        if signature.startswith("sha:"):
            reviewed = [evidence for evidence in members if _evidence_review_priority(evidence)]
            if reviewed:
                for evidence in members:
                    if evidence not in reviewed:
                        surviving.discard(evidence.id)
                        superseded_reasons[evidence.id] = (
                            "Superseded because the exact source bytes already have a reviewed assignment."
                        )
                members = [evidence for evidence in reviewed if evidence.id in surviving]
                by_quote = defaultdict(list)
                for evidence in members:
                    by_quote[evidence.quotation_id].append(evidence)
            representatives = []
            for quote_members in by_quote.values():
                ranked_members = sorted(
                    quote_members,
                    key=_evidence_quality_key,
                    reverse=True,
                )
                representatives.append(ranked_members[0])
                for duplicate in ranked_members[1:]:
                    if _evidence_review_priority(duplicate):
                        continue
                    surviving.discard(duplicate.id)
                    superseded_reasons[duplicate.id] = (
                        "Superseded by a stronger extraction of the exact same source bytes."
                    )
        else:
            representatives = [
                max(quote_members, key=_evidence_quality_key)
                for quote_members in by_quote.values()
            ]

        if len(representatives) <= 1:
            if not signature.startswith("sha:"):
                for quote_members in by_quote.values():
                    _resolve_same_quote_revisions(
                        quote_members,
                        surviving,
                        superseded_reasons,
                        ambiguous_ties,
                    )
            continue
        ranked = sorted(representatives, key=_evidence_quality_key, reverse=True)
        winner = ranked[0]
        near_ties = [
            evidence for evidence in ranked if _cross_quote_near_tie(winner, evidence)
        ]
        if len(near_ties) > 1:
            retained_quote_ids = {evidence.quotation_id for evidence in near_ties}
            ambiguous_ties.update(
                evidence.id
                for evidence in members
                if evidence.quotation_id in retained_quote_ids
                and not _evidence_review_priority(evidence)
            )
        else:
            retained_quote_ids = {winner.quotation_id}
        for contender in ranked[1:]:
            if contender in near_ties:
                continue
            for losing_evidence in by_quote[contender.quotation_id]:
                if _evidence_review_priority(losing_evidence):
                    continue
                surviving.discard(losing_evidence.id)
                superseded_reasons[losing_evidence.id] = (
                    "Superseded by stronger exact-reference, item/quantity, customer, and timing "
                    "evidence for the same company-scoped PO/LPO."
                )
        if not signature.startswith("sha:"):
            for quote_id in retained_quote_ids:
                _resolve_same_quote_revisions(
                    by_quote[quote_id],
                    surviving,
                    superseded_reasons,
                    ambiguous_ties,
                )

    # Preserve every distinct normalized order reference. The cap controls only
    # unreferenced/noisy suggestions, not real multiple orders on one quote.
    unreferenced_counts = defaultdict(int)
    unreviewed = sorted(
        (
            evidence
            for evidence in evidence_by_id.values()
            if evidence.id in surviving and not _evidence_review_priority(evidence)
        ),
        key=_evidence_quality_key,
        reverse=True,
    )
    for evidence in unreviewed:
        if refs_by_id.get(evidence.id):
            continue
        if unreferenced_counts[evidence.quotation_id] >= max_per_quote:
            surviving.discard(evidence.id)
            superseded_reasons[evidence.id] = (
                "Superseded by stronger mailbox-wide matches; the active cap applies to "
                "unreferenced review candidates."
            )
            continue
        unreferenced_counts[evidence.quotation_id] += 1

    ambiguous_ties.intersection_update(surviving)
    if ambiguous_ties:
        QuotationPOEvidence.objects.filter(
            id__in=ambiguous_ties,
            link_approved_at__isnull=True,
            status__in=[
                QuotationPOEvidence.STATUS_CANDIDATE,
                QuotationPOEvidence.STATUS_AMBIGUOUS,
                QuotationPOEvidence.STATUS_FAILED,
            ],
        ).update(
            status=QuotationPOEvidence.STATUS_AMBIGUOUS,
            error=(
                "The same normalized PO/LPO has near-equal quotation matches or distinct "
                "copies without revision proof; staff must verify the correct assignment/version."
            ),
            updated_at=timezone.now(),
        )

    for evidence_id, reason in superseded_reasons.items():
        QuotationPOEvidence.objects.filter(
            id=evidence_id,
            link_approved_at__isnull=True,
        ).exclude(
            status__in=[
                QuotationPOEvidence.STATUS_PARSED,
                QuotationPOEvidence.STATUS_NOT_RELEVANT,
            ]
        ).update(
            status=QuotationPOEvidence.STATUS_SUPERSEDED,
            error=reason,
            updated_at=timezone.now(),
        )
    superseded = sorted(set(evidence_by_id) - surviving)
    return surviving, superseded


class MailboxPOMatchBusy(RuntimeError):
    """Another worker owns the short reconciliation lease."""


def _initial_summary(*, eligible_count=0):
    return {
        "eligible_quotations": eligible_count,
        "relevant_messages": 0,
        "document_variants": 0,
        "decisive_messages": 0,
        "ambiguous_messages": 0,
        "unmatched_messages": 0,
        "spam_or_trash_messages": 0,
        "evidence_created": 0,
        "evidence_updated": 0,
        "evidence_superseded": 0,
        "existing_evidence_linked": 0,
        "active_evidence": 0,
    }


def _run_messages(audit_run):
    return (
        MailboxPOMessage.objects.filter(
            Q(audit_memberships__audit_run=audit_run) | Q(last_seen_run=audit_run),
            gmail_connection=audit_run.gmail_connection,
            is_relevant=True,
        )
        .select_related("gmail_connection")
        .distinct()
    )


def _claim_match_run(audit_run, *, requested_by=None, match_run=None, force=False):
    """Claim a short lease without holding a database lock during matching."""

    now = timezone.now()
    token = uuid.uuid4().hex
    with transaction.atomic():
        locked_audit = (
            MailboxPOAuditRun.objects.select_for_update()
            .select_related("gmail_connection")
            .get(pk=audit_run.pk)
        )
        if locked_audit.status != MailboxPOAuditRun.STATUS_COMPLETED or not locked_audit.exhausted:
            raise ValueError("Finish the mailbox inventory before matching quotations.")

        if match_run is not None:
            current = MailboxPOMatchRun.objects.select_for_update().get(
                pk=match_run.pk,
                audit_run=locked_audit,
                algorithm_version=ALGORITHM_VERSION,
            )
        else:
            current = (
                MailboxPOMatchRun.objects.select_for_update()
                .filter(audit_run=locked_audit, algorithm_version=ALGORITHM_VERSION)
                .first()
            )
            if current and current.status == MailboxPOMatchRun.STATUS_COMPLETED and not force:
                return locked_audit, current, ""
            if current and current.status == MailboxPOMatchRun.STATUS_RUNNING:
                # A force request must not fork a second writer while an
                # existing reconciliation is still resumable.
                pass
            elif force or current is None or current.status == MailboxPOMatchRun.STATUS_FAILED:
                current = MailboxPOMatchRun.objects.create(
                    audit_run=locked_audit,
                    requested_by=requested_by,
                    algorithm_version=ALGORITHM_VERSION,
                    summary=_initial_summary(),
                )

        if current.status == MailboxPOMatchRun.STATUS_COMPLETED:
            return locked_audit, current, ""
        if (
            current.lease_token
            and current.lease_expires_at
            and current.lease_expires_at > now
        ):
            raise MailboxPOMatchBusy("Mailbox quotation matching is already running in another tab.")
        current.lease_token = token
        current.lease_expires_at = now + timedelta(seconds=MATCH_LEASE_SECONDS)
        current.last_heartbeat_at = now
        current.save(
            update_fields=["lease_token", "lease_expires_at", "last_heartbeat_at"]
        )
    return locked_audit, current, token


def _ownership_lost_error():
    return MailboxPOMatchBusy(
        "Mailbox quotation matching ownership changed; this stale worker stopped without saving progress."
    )


def _locked_owned_match_run(match_run_id, lease_token):
    try:
        current = MailboxPOMatchRun.objects.select_for_update().get(pk=match_run_id)
    except MailboxPOMatchRun.DoesNotExist as exc:
        raise _ownership_lost_error() from exc
    if (
        current.status != MailboxPOMatchRun.STATUS_RUNNING
        or not lease_token
        or current.lease_token != lease_token
    ):
        raise _ownership_lost_error()
    return current


def _renew_match_lease(match_run_id, lease_token):
    """Heartbeat only while this worker still owns the reconciliation row."""

    now = timezone.now()
    updated = MailboxPOMatchRun.objects.filter(
        pk=match_run_id,
        status=MailboxPOMatchRun.STATUS_RUNNING,
        lease_token=lease_token,
    ).update(
        lease_expires_at=now + timedelta(seconds=MATCH_LEASE_SECONDS),
        last_heartbeat_at=now,
    )
    if not updated:
        raise _ownership_lost_error()


def _store_owned_proposal(
    match_run_id,
    lease_token,
    inventory,
    proposal,
    actor,
    *,
    decisive,
    variant_count,
):
    """Persist one proposal while preventing a newly claimed worker racing it."""

    with transaction.atomic():
        current = _locked_owned_match_run(match_run_id, lease_token)
        now = timezone.now()
        current.lease_expires_at = now + timedelta(seconds=MATCH_LEASE_SECONDS)
        current.last_heartbeat_at = now
        current.save(update_fields=["lease_expires_at", "last_heartbeat_at"])
        return _store_proposal(
            inventory,
            proposal,
            current,
            actor,
            decisive=decisive,
            variant_count=variant_count,
        )


def _failed_message_ids(errors):
    return {
        str(error.get("gmail_message_id") or "")
        for error in (errors or [])
        if isinstance(error, dict) and error.get("gmail_message_id")
    }


def _supersede_stale_evidence(stale_ids):
    """Conditionally supersede stale rows without racing a staff review."""

    if not stale_ids:
        return 0
    return QuotationPOEvidence.objects.filter(
        id__in=stale_ids,
        link_approved_at__isnull=True,
        status__in=[
            QuotationPOEvidence.STATUS_CANDIDATE,
            QuotationPOEvidence.STATUS_AMBIGUOUS,
            QuotationPOEvidence.STATUS_FAILED,
        ],
    ).update(
        status=QuotationPOEvidence.STATUS_SUPERSEDED,
        error=(
            "Superseded by the completed mailbox-wide scan because item, quantity, "
            "commercial value, customer, and timing checks did not keep it active."
        ),
        updated_at=timezone.now(),
    )


def _finalize_match_run(match_run, audit_run, summary, errors, *, max_active_per_quote):
    summary["existing_evidence_linked"] = _link_existing_evidence_to_inventory(audit_run)
    active_ids = set(
        QuotationPOEvidence.objects.filter(mailbox_match_run=match_run)
        .exclude(
            status__in=[
                QuotationPOEvidence.STATUS_NOT_RELEVANT,
                QuotationPOEvidence.STATUS_SUPERSEDED,
            ]
        )
        .values_list("id", flat=True)
    )
    active_ids, deduped = _dedupe_and_cap(
        active_ids,
        max_per_quote=max(
            1,
            min(int(max_active_per_quote or 1), MAX_ACTIVE_EVIDENCE_PER_QUOTE),
        ),
    )
    summary["evidence_superseded"] += len(deduped)

    snapshot_message_ids = MailboxPOMessage.objects.filter(
        Q(audit_memberships__audit_run=audit_run) | Q(last_seen_run=audit_run),
        gmail_connection=audit_run.gmail_connection,
    ).values_list("gmail_message_id", flat=True)
    stale = QuotationPOEvidence.objects.filter(
        gmail_connection_lineage_q(audit_run.gmail_connection),
        gmail_message_id__in=snapshot_message_ids,
        link_approved_at__isnull=True,
        status__in=[
            QuotationPOEvidence.STATUS_CANDIDATE,
            QuotationPOEvidence.STATUS_AMBIGUOUS,
            QuotationPOEvidence.STATUS_FAILED,
        ],
    )
    failed_ids = _failed_message_ids(errors)
    if failed_ids:
        stale = stale.exclude(gmail_message_id__in=failed_ids)
    if active_ids:
        stale = stale.exclude(id__in=active_ids)
    stale_ids = list(stale.values_list("id", flat=True).distinct())
    summary["evidence_superseded"] += _supersede_stale_evidence(stale_ids)
    summary["active_evidence"] = len(active_ids)


def _persist_owned_match_page(
    match_run_id,
    lease_token,
    audit_run,
    summary,
    errors,
    *,
    cursor_message_id,
    has_more,
    max_active_per_quote,
):
    """Commit page state only if the claimed lease still belongs to this worker."""

    with transaction.atomic():
        current = _locked_owned_match_run(match_run_id, lease_token)
        now = timezone.now()
        current.cursor_message_id = cursor_message_id
        current.lease_expires_at = now + timedelta(seconds=MATCH_LEASE_SECONDS)
        current.last_heartbeat_at = now
        if not has_more:
            _finalize_match_run(
                current,
                audit_run,
                summary,
                errors,
                max_active_per_quote=max_active_per_quote,
            )
            current.status = MailboxPOMatchRun.STATUS_COMPLETED
            current.completed_at = timezone.now()
        current.summary = summary
        current.errors = errors[-MAX_MATCH_ERRORS:]
        current.lease_token = ""
        current.lease_expires_at = None
        current.save(
            update_fields=[
                "cursor_message_id",
                "status",
                "summary",
                "errors",
                "completed_at",
                "last_heartbeat_at",
                "lease_token",
                "lease_expires_at",
            ]
        )
    return current


def _persist_owned_match_failure(match_run_id, lease_token, summary, errors):
    """Mark a run failed only when the failing worker still owns its lease."""

    with transaction.atomic():
        try:
            current = _locked_owned_match_run(match_run_id, lease_token)
        except MailboxPOMatchBusy:
            return None
        now = timezone.now()
        current.status = MailboxPOMatchRun.STATUS_FAILED
        current.summary = summary
        current.errors = errors[-MAX_MATCH_ERRORS:]
        current.completed_at = now
        current.last_heartbeat_at = now
        current.lease_token = ""
        current.lease_expires_at = None
        current.save(
            update_fields=[
                "status",
                "summary",
                "errors",
                "completed_at",
                "last_heartbeat_at",
                "lease_token",
                "lease_expires_at",
            ]
        )
    return current


def reconcile_mailbox_po_audit_page(
    audit_run,
    *,
    requested_by=None,
    match_run=None,
    page_size=DEFAULT_MATCH_PAGE_SIZE,
    force=False,
    max_active_per_quote=MAX_ACTIVE_EVIDENCE_PER_QUOTE,
):
    """Match one bounded message page and persist a resumable cursor.

    The lease is held only in the ledger, never as an open database
    transaction while CPU-heavy comparisons run. Replaying a page after a
    killed worker is safe because evidence upserts are source-key idempotent.
    """

    audit_run, match_run, lease_token = _claim_match_run(
        audit_run,
        requested_by=requested_by,
        match_run=match_run,
        force=force,
    )
    if not lease_token:
        return match_run

    page_size = max(1, min(int(page_size or DEFAULT_MATCH_PAGE_SIZE), MAX_MATCH_PAGE_SIZE))
    summary = {**_initial_summary(), **(match_run.summary or {})}
    errors = list(match_run.errors or [])[-MAX_MATCH_ERRORS:]
    try:
        quotes = eligible_quotations()
        _renew_match_lease(match_run.id, lease_token)
        summary["eligible_quotations"] = len(quotes)
        page = list(
            _run_messages(audit_run)
            .filter(id__gt=match_run.cursor_message_id)
            .order_by("id")[: page_size + 1]
        )
        _renew_match_lease(match_run.id, lease_token)
        has_more = len(page) > page_size
        page = page[:page_size]
        for inventory in page:
            _renew_match_lease(match_run.id, lease_token)
            summary["relevant_messages"] += 1
            if not inventory.auto_link_eligible:
                summary["spam_or_trash_messages"] += 1
            try:
                proposals, decisive, variant_count = _proposals_for_message(inventory, quotes)
                # Matching can be CPU-heavy. Verify ownership again before any
                # evidence write so an expired/stolen worker cannot persist its
                # stale page after returning from the comparison.
                _renew_match_lease(match_run.id, lease_token)
                summary["document_variants"] += variant_count
                if not proposals:
                    summary["unmatched_messages"] += 1
                else:
                    if decisive:
                        summary["decisive_messages"] += 1
                    else:
                        summary["ambiguous_messages"] += 1
                    for proposal in proposals:
                        evidence, created = _store_owned_proposal(
                            match_run.id,
                            lease_token,
                            inventory,
                            proposal,
                            requested_by,
                            decisive=proposal.decisive,
                            variant_count=variant_count,
                        )
                        summary["evidence_created" if created else "evidence_updated"] += 1
                _renew_match_lease(match_run.id, lease_token)
            except MailboxPOMatchBusy:
                raise
            except Exception as exc:
                _renew_match_lease(match_run.id, lease_token)
                if len(errors) < MAX_MATCH_ERRORS:
                    errors.append(
                        {
                            "gmail_message_id": inventory.gmail_message_id,
                            "error": str(exc)[:1000],
                        }
                    )

        cursor_message_id = page[-1].id if page else match_run.cursor_message_id
        match_run = _persist_owned_match_page(
            match_run.id,
            lease_token,
            audit_run,
            summary,
            errors,
            cursor_message_id=cursor_message_id,
            has_more=has_more,
            max_active_per_quote=max_active_per_quote,
        )
    except Exception as exc:
        if not isinstance(exc, MailboxPOMatchBusy):
            errors.append({"error": str(exc)[:1000]})
        _persist_owned_match_failure(match_run.id, lease_token, summary, errors)
        raise
    return match_run


def reconcile_mailbox_po_audit(
    audit_run,
    *,
    requested_by=None,
    max_active_per_quote=MAX_ACTIVE_EVIDENCE_PER_QUOTE,
    page_size=MAX_MATCH_PAGE_SIZE,
):
    """Drain resumable match pages for management commands and focused tests."""

    match_run = None
    force = True
    while match_run is None or match_run.status == MailboxPOMatchRun.STATUS_RUNNING:
        match_run = reconcile_mailbox_po_audit_page(
            audit_run,
            requested_by=requested_by,
            match_run=match_run,
            page_size=page_size,
            force=force,
            max_active_per_quote=max_active_per_quote,
        )
        force = False
    return match_run
