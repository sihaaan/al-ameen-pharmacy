"""Shared LPO cleanup and delivery fields for quotation and standalone workflows."""
import re
from decimal import Decimal, InvalidOperation

from django.core import signing
from django.core.exceptions import ValidationError
from django.utils import timezone

from .company_matching import find_similar_companies
from .models import Company

IMPORT_SALT = "delivery-note-lpo-v1"
DELIVERY_FIELDS = ("customer_name", "customer_address", "customer_trn", "delivery_address",
                   "attention", "contact_phone", "lpo_number", "lpo_date", "requested_delivery_date")


def _field(text, labels):
    match = re.search(rf"(?im)^[ \t]*(?:{labels})[ \t]*:[ \t]*([^\n]*)", text)
    # Do not let a blank label consume the next field.
    return match.group(1).strip() if match and ":" not in match.group(1) else ""


def _pdf_header_columns(preview):
    """Read the header columns separately so supplier contacts cannot bleed into Ship To."""
    if not str(preview.get("source_filename", "")).lower().endswith(".pdf") or not preview.get("source_file_ref"):
        return ""
    from .import_parsers import fitz
    from .private_storage import read_private_ref
    if fitz is None:
        return ""
    data = read_private_ref(preview["source_file_ref"], expected_sha256=preview.get("source_sha256", ""))
    if not data:
        return ""
    from .attachment_inspection import inspect_pdf_attachment, validate_pdf_page_geometry, validate_pdf_word_output
    inspect_pdf_attachment(data)
    chunks = []
    with fitz.open(stream=data, filetype="pdf") as document:
        for index in range(min(2, len(document))):
            page = document[index]
            validate_pdf_page_geometry(page.rect.width, page.rect.height)
            words = page.get_text("words")
            validate_pdf_word_output(len(words), page_number=index + 1)
            right_starts = [hit.x0 for label in ("Bill To:", "Payment Terms:")
                            for hit in page.search_for(label) if hit.x0 > page.rect.width * .35]
            divider = min(right_starts) - 3 if right_starts else page.rect.width * .48
            # Only use a column crop when explicit purchaser/shipping labels identify it.
            for label in ("Bill To:", "Ship To:", "Deliver To:"):
                hits = page.search_for(label)
                if len(hits) != 1:
                    continue
                rect = hits[0]
                right = page.rect.width if rect.x0 >= divider else divider
                bottom = min(page.rect.height, rect.y0 + 160)
                for stop in ("Ship To:", "Payment Terms:", "Table Of Particulars", "SCHEDULE OF DETAILS", "Supplier:"):
                    for hit in page.search_for(stop):
                        if hit.y0 > rect.y0 + 5 and rect.x0 - 10 <= hit.x0 < right:
                            bottom = min(bottom, hit.y0)
                lines = {}
                for word in words:
                    if word[0] >= rect.x0 - 1 and word[2] <= right and rect.y0 - 1 <= word[1] < bottom:
                        lines.setdefault((word[5], word[6]), []).append(word)
                ordered = sorted(lines.values(), key=lambda line: (min(w[1] for w in line), min(w[0] for w in line)))
                chunks.append("\n".join(" ".join(w[4] for w in sorted(line, key=lambda w: w[0])) for line in ordered))
    return "\n".join(chunks)


def extract_delivery_details(preview, *, read_pdf=True):
    text = str(preview.get("original_text") or "")
    column_text = _pdf_header_columns(preview) if read_pdf else ""
    header = column_text or text
    details = {key: "" for key in DELIVERY_FIELDS}
    bill = re.search(r"(?im)^\s*Bill\s+To\s*:[ \t]*\n?([^\n]+)", header)
    if bill:
        details["customer_name"] = bill.group(1).strip()
        block = header[bill.end():]
        block = re.split(r"(?im)^\s*(?:Supplier|Ship To|Deliver To|Payment Terms|Requestor|Contract|Table Of Particulars)\b", block)[0]
        trn = re.search(r"\b\d{15}\b", block)
        details["customer_trn"] = trn.group(0) if trn else ""
        address = block[:trn.start()] if trn else block
        details["customer_address"] = re.sub(r"(?:TRN|VAT(?: Reg\.? No\.?)?)\s*:?\s*$", "", address, flags=re.I).strip()
    ship = re.search(r"(?im)^\s*(?:Ship|Deliver)\s+To\s*:[ \t]*(.*)", header)
    if ship:
        block = ship.group(1) + "\n" + header[ship.end():]
        block = re.split(r"(?im)^\s*(?:Requestor|Requester|Attention|Attn|Contact|Payment Terms|Supplier|Contract|Table Of Particulars)\b", block)[0]
        details["delivery_address"] = block.strip()
    details["attention"] = _field(header, r"Requestor|Requester|Attention|Attn|Contact Person") or _field(text, r"Requestor|Requester|Attention|Attn|Contact Person")
    details["contact_phone"] = _field(header, r"Requestor Phone(?: Number)?|Requester Phone(?: Number)?|Contact Phone|Contact Number")
    if not details["customer_name"]:
        details["customer_name"] = _field(text, r"Customer(?: Name)?|Purchaser(?: Name)?")
    # Explicit labels also support pasted text and spreadsheets.
    details["delivery_address"] = details["delivery_address"] or _field(text, r"Delivery Address|Shipping Address")
    from .views import _parse_lpo_business_date
    dates = set()
    for row in preview.get("lines") or []:
        candidates = [row.get("requested_delivery_date"), row.get("delivery_date"),
                      *str(row.get("raw_line") or "").split("|")]
        for candidate in candidates:
            candidate = re.sub(r"\s+", "", str(candidate or ""))
            if re.fullmatch(r"\d{1,2}-[A-Za-z]{3}-\d{2,4}|\d{4}-\d{2}-\d{2}", candidate):
                try:
                    parsed = _parse_lpo_business_date(candidate)
                    if parsed:
                        dates.add(parsed.isoformat())
                except ValueError:
                    pass
    if len(dates) == 1:
        details["requested_delivery_date"] = dates.pop()
    for key, value in ((preview.get("meta") or {}).get("delivery_details") or {}).items():
        if key in details and value and not details[key]:
            details[key] = str(value).strip()[:2000]
    return details


def normalize_lpo_preview(preview, *, read_pdf=True):
    from .import_rules import summarize_lines
    result = {**preview, "meta": dict(preview.get("meta") or {})}
    rows = []
    skipped = 0
    for original in preview.get("lines") or []:
        row = dict(original)
        name = str(row.get("requested_item_name") or row.get("raw_name") or row.get("item_name") or "").strip()
        flat = re.sub(r"[^a-z0-9]", "", name.lower())
        if (re.fullmatch(r"page\d+(?:of\d+)?", flat)
                or flat in {"tableofparticulars", "scheduleofdetails"}
                or re.match(r"^(?:pototal|taxtotal|grandtotal)(?:aed|usd|eur|gbp|\d|inwords)", flat)):
            skipped += 1
            continue
        source = str(row.get("raw_line") or name)
        cells = source.split("|")
        item_source = cells[1].strip() if len(cells) > 2 and cells[0].strip().isdigit() else name
        split = re.split(r"\s+(?:Product\s+Code|BPA|Note\s+from\s+Requester)\s*:", item_source, maxsplit=1, flags=re.I)
        if len(split) > 1:
            name = split[0].strip()
            references = []
            for label, pattern in [("Product code", r"Product\s+Code:\s*([A-Z0-9_-]+)"),
                                   ("BPA", r"\bBPA:\s*([A-Z0-9_-]+)")]:
                found = re.search(pattern, item_source, re.I)
                if found:
                    references.append(f"{label}: {found.group(1)}")
            requester_note = re.search(r"Note\s+from\s+Requester:\s*(.+)$", item_source, re.I)
            if requester_note:
                references.append(requester_note.group(1).strip())
            row["description"] = "\n".join(dict.fromkeys(filter(None, [*str(row.get("description") or "").splitlines(), *references])))
        for key in ("raw_name", "requested_item_name", "item_name"):
            if key in row:
                row[key] = name
        rows.append(row)
    result["lines"] = rows
    result["meta"]["line_count"] = len(rows)
    result["meta"]["delivery_details"] = extract_delivery_details(result, read_pdf=read_pdf)
    result["meta"]["lpo_metadata_rows_removed"] = skipped + int(result["meta"].get("lpo_metadata_rows_removed") or 0)
    result["summary"] = summarize_lines(rows, skipped_count=result["meta"]["lpo_metadata_rows_removed"])
    return result


def delivery_lpo_preview(preview, actor):
    from .views import _extract_lpo_details
    details = extract_delivery_details(preview, read_pdf=False)
    identifiers = _extract_lpo_details(preview)
    details["lpo_number"] = identifiers["lpo_number"] or details["lpo_number"]
    details["lpo_date"] = identifiers["lpo_date"].isoformat() if identifiers["lpo_date"] else details["lpo_date"]
    warnings = list(preview.get("warnings") or [])
    rows = []
    if len(preview.get("lines") or []) > 300:
        raise ValidationError("This LPO contains more than 300 rows. Split it before creating delivery notes.")
    for row in preview.get("lines") or []:
        if row.get("parse_status") == "ignored":
            continue
        name = str(row.get("requested_item_name") or row.get("raw_name") or row.get("item_name") or "").strip()
        if not name:
            continue
        quantity = str(row.get("quantity") or "").strip()
        try:
            parsed = Decimal(quantity)
            valid = parsed.is_finite() and 0 < parsed <= Decimal("999999999.999") and parsed == parsed.quantize(Decimal(".001"))
        except (InvalidOperation, ValueError):
            valid = False
        if not valid:
            quantity = ""
            warnings.append(f"{name}: check the quantity against the LPO.")
        rows.append({"item_name": name[:255], "description": str(row.get("description") or "")[:2000],
                     "quantity": quantity, "unit": str(row.get("unit") or "")[:50], "quotation_line": None})
    if not rows:
        raise ValidationError("No delivery items were found. Try a clearer LPO or paste its item table.")
    candidates = find_similar_companies(details["customer_name"], queryset=Company.objects.filter(is_active=True)) if details["customer_name"] else []
    exact = [entry for entry in candidates if entry["score"] >= 96]
    company = exact[0]["id"] if len(exact) == 1 else None
    if not company:
        warnings.append("Confirm the customer before saving this delivery note.")
    if not details["lpo_number"]:
        warnings.append("The LPO number was not found. Enter it from the document.")
    source = {
        "source_filename": preview.get("source_filename") or "Pasted LPO",
        "source_sha256": preview.get("source_sha256", ""), "source_file_ref": preview.get("source_file_ref", ""),
        "parse_method": preview.get("parse_method", ""), "details": details, "rows": rows,
        "warnings": list(dict.fromkeys(warnings)), "parsed_by": actor.pk, "parsed_at": timezone.now().isoformat(),
    }
    return {"details": details, "lines": rows, "company": company, "company_candidates": candidates,
            "warnings": source["warnings"], "source_filename": source["source_filename"],
            "parse_method": source["parse_method"],
            "import_token": signing.dumps(source, salt=IMPORT_SALT, compress=True)}


def preserve_lpo_line_details(original, cleaned):
    """Keep source references when AI returns the same item without its notes."""
    from collections import defaultdict
    from .pricing import pricing_unit

    def key(row):
        name = row.get("requested_item_name") or row.get("raw_name") or row.get("item_name") or ""
        try:
            quantity = Decimal(str(row.get("quantity") or ""))
            if not quantity.is_finite():
                return None
        except InvalidOperation:
            return None
        name = re.sub(r"[^a-z0-9]", "", str(name).lower())
        return (name, quantity, pricing_unit(row.get("unit"))) if name else None

    sources = defaultdict(list)
    for row in original.get("lines") or []:
        if key(row):
            sources[key(row)].append(row)
    rows = []
    for source in cleaned.get("lines") or []:
        row = dict(source)
        matches = sources.get(key(row), [])
        if len(matches) == 1:
            row["description"] = "\n".join(dict.fromkeys(filter(None, [
                *str(row.get("description") or "").splitlines(),
                *str(matches[0].get("description") or "").splitlines(),
            ])))
        rows.append(row)
    return {**cleaned, "lines": rows}


def read_delivery_import_token(token, actor):
    try:
        source = signing.loads(token, salt=IMPORT_SALT, max_age=86400)
        if source.get("parsed_by") != actor.pk:
            raise signing.BadSignature()
        return source
    except (signing.BadSignature, AttributeError):
        raise ValidationError("This LPO preview expired or changed. Upload and review the LPO again.")
