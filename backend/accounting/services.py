from collections import defaultdict
from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from .models import (
    AccountCustomer,
    AccountingCategory,
    AccountingImport,
    AccountingImportCustomer,
    AccountingInvoiceRow,
)
from .parsers import normalize_customer_name, parse_category_upload, parse_outstanding_upload


def customer_lookup_key(row):
    if row.customer_code:
        return ("code", row.customer_code.strip().lower())
    return ("name", normalize_customer_name(row.customer_name))


def find_or_create_customer(row, category_map=None, category_code_map=None):
    normalized_name = normalize_customer_name(row.customer_name)
    normalized_code = row.customer_code.strip().lower() if row.customer_code else ""
    customer = None
    if row.customer_code:
        customer = AccountCustomer.objects.filter(customer_code__iexact=row.customer_code.strip()).first()
    if customer is None and normalized_name:
        name_match = AccountCustomer.objects.filter(normalized_name=normalized_name).first()
        if name_match and (not row.customer_code or not name_match.customer_code):
            customer = name_match

    mapped_category = (category_code_map or {}).get(normalized_code) or (category_map or {}).get(normalized_name)
    if customer is None:
        customer = AccountCustomer.objects.create(
            customer_code=row.customer_code.strip(),
            name=row.customer_name.strip(),
            normalized_name=normalized_name,
            category=mapped_category or AccountingCategory.UNKNOWN,
        )
    else:
        changed = False
        if row.customer_code and not customer.customer_code:
            customer.customer_code = row.customer_code.strip()
            changed = True
        if mapped_category and mapped_category != AccountingCategory.UNKNOWN and customer.category != mapped_category:
            customer.category = mapped_category
            changed = True
        if changed:
            customer.save(update_fields=["customer_code", "category", "updated_at"])
    return customer


def invoice_is_due(row):
    return row.days > 30 or row.bucket_30_60 != 0 or row.bucket_60_90 != 0 or row.bucket_over_90 != 0


def build_summary_status(customer, rows, overdue_amount, max_days):
    if customer.is_ignored:
        return AccountingImportCustomer.STATUS_IGNORED, False
    is_due = overdue_amount != 0 or max_days > 30 or any(invoice_is_due(row) for row in rows)
    return (AccountingImportCustomer.STATUS_DUE if is_due else AccountingImportCustomer.STATUS_NOT_DUE), is_due


def find_duplicate_import(parsed):
    return (
        AccountingImport.objects.filter(source_sha256=parsed.sha256)
        .order_by("-created_at")
        .first()
    )


def _update_customer_category(customer, category):
    if category and category != AccountingCategory.UNKNOWN and customer.category != category:
        customer.category = category
        customer.save(update_fields=["category", "updated_at"])
        return True
    return False


def apply_category_map_to_import(import_record, parsed_category):
    if not parsed_category:
        return {"updated": 0, "matched": 0, "unchanged": 0, "unmatched": 0, "warnings": []}
    updated = 0
    unchanged = 0
    matched = 0
    unmatched = 0
    warnings = list(parsed_category.warnings)
    entries = parsed_category.entries or {}
    code_entries = parsed_category.code_entries or {}

    for import_customer in import_record.customers.select_related("customer"):
        normalized_name = normalize_customer_name(import_customer.customer_name)
        normalized_code = import_customer.customer_code.strip().lower() if import_customer.customer_code else ""
        category = code_entries.get(normalized_code) or entries.get(normalized_name)
        if not category:
            unmatched += 1
            continue
        matched += 1
        changed = _update_customer_category(import_customer.customer, category)
        if import_customer.category != category:
            import_customer.category = category
            import_customer.save(update_fields=["category", "updated_at"])
            changed = True
        if changed:
            updated += 1
        else:
            unchanged += 1

    import_record.category_filename = parsed_category.filename
    import_record.category_sha256 = parsed_category.sha256
    import_record.parse_meta = {
        **(import_record.parse_meta or {}),
        "category": parsed_category.parse_meta,
    }
    combined_warnings = [*(import_record.warnings or []), *warnings]
    import_record.warnings = combined_warnings[:100]
    import_record.save(update_fields=["category_filename", "category_sha256", "parse_meta", "warnings", "updated_at"])
    return {"updated": updated, "matched": matched, "unchanged": unchanged, "unmatched": unmatched, "warnings": warnings[:50]}


def category_update_message(result):
    if not result:
        return ""
    return (
        f"Category workbook applied. Matched {result.get('matched', 0)} customers: "
        f"{result.get('updated', 0)} updated, "
        f"{result.get('unchanged', 0)} already up to date, "
        f"{result.get('unmatched', 0)} unmatched."
    )


@transaction.atomic
def create_accounting_import(*, outstanding_file, category_file=None, actor=None):
    parsed = parse_outstanding_upload(outstanding_file)
    duplicate = find_duplicate_import(parsed)
    if duplicate:
        category_meta = {}
        if category_file:
            parsed_category = parse_category_upload(category_file)
            category_meta = apply_category_map_to_import(duplicate, parsed_category)
        return duplicate, {
            "duplicate": True,
            "message": (
                "This outstanding file has already been uploaded before. No duplicate import was created."
                + (f" {category_update_message(category_meta)}" if category_meta else "")
            ),
            "previous_import_id": duplicate.id,
            "category_update": category_meta,
            "category_update_message": category_update_message(category_meta),
        }

    parsed_category = parse_category_upload(category_file) if category_file else None
    category_map = parsed_category.entries if parsed_category else {}
    category_code_map = parsed_category.code_entries if parsed_category else {}
    grouped = defaultdict(list)
    for row in parsed.rows:
        grouped[customer_lookup_key(row)].append(row)

    import_record = AccountingImport.objects.create(
        source_filename=parsed.filename,
        source_sha256=parsed.sha256,
        source_size=parsed.size,
        category_filename=parsed_category.filename if parsed_category else "",
        category_sha256=parsed_category.sha256 if parsed_category else "",
        report_date=parsed.report_date,
        uploaded_by=actor if getattr(actor, "is_authenticated", False) else None,
        parsed_row_count=len(parsed.rows),
        skipped_row_count=parsed.skipped_row_count,
        customer_count=len(grouped),
        warnings=(parsed.warnings + (parsed_category.warnings if parsed_category else []))[:100],
        parse_meta={
            "outstanding": parsed.parse_meta,
            "category": parsed_category.parse_meta if parsed_category else {},
            "source_retention": "parsed_and_discarded",
        },
    )

    due_count = 0
    invoice_count = 0
    for rows in grouped.values():
        first = rows[0]
        customer = find_or_create_customer(first, category_map, category_code_map)
        bucket_0_30 = sum((row.bucket_0_30 for row in rows), Decimal("0.00"))
        bucket_30_60 = sum((row.bucket_30_60 for row in rows), Decimal("0.00"))
        bucket_60_90 = sum((row.bucket_60_90 for row in rows), Decimal("0.00"))
        bucket_over_90 = sum((row.bucket_over_90 for row in rows), Decimal("0.00"))
        total = sum((row.total for row in rows), Decimal("0.00"))
        overdue = bucket_30_60 + bucket_60_90 + bucket_over_90
        max_days = max((row.days for row in rows), default=0)
        status, is_due = build_summary_status(customer, rows, overdue, max_days)
        if is_due and not customer.is_ignored:
            due_count += 1

        import_customer = AccountingImportCustomer.objects.create(
            accounting_import=import_record,
            customer=customer,
            customer_code=customer.customer_code or first.customer_code,
            customer_name=customer.name or first.customer_name,
            category=customer.category,
            email=customer.email,
            total_outstanding=total,
            bucket_0_30=bucket_0_30,
            bucket_30_60=bucket_30_60,
            bucket_60_90=bucket_60_90,
            bucket_over_90=bucket_over_90,
            overdue_amount=overdue,
            max_days=max_days,
            invoice_count=len(rows),
            is_due=is_due,
            is_ignored=customer.is_ignored,
            status=status,
            warnings=[warning for row in rows for warning in row.warnings][:25],
        )
        AccountingInvoiceRow.objects.bulk_create(
            [
                AccountingInvoiceRow(
                    import_customer=import_customer,
                    source_row_number=row.source_row_number,
                    customer_code=row.customer_code,
                    customer_name=row.customer_name,
                    place=row.place,
                    bill_number=row.bill_number,
                    invoice_number=row.invoice_number,
                    lpo_reference=row.lpo_reference,
                    invoice_date=row.invoice_date,
                    amount=row.amount,
                    bucket_0_30=row.bucket_0_30,
                    bucket_30_60=row.bucket_30_60,
                    bucket_60_90=row.bucket_60_90,
                    bucket_over_90=row.bucket_over_90,
                    total=row.total,
                    days=row.days,
                    raw_data=row.raw_data,
                    warnings=row.warnings,
                )
                for row in rows
            ]
        )
        invoice_count += len(rows)

    import_record.due_customer_count = due_count
    import_record.generated_statement_count = due_count
    import_record.parsed_row_count = invoice_count
    import_record.save(update_fields=["due_customer_count", "generated_statement_count", "parsed_row_count", "updated_at"])
    return import_record, {"duplicate": False, "message": "Accounting import parsed successfully."}


@transaction.atomic
def apply_category_upload_to_import(*, import_record, category_file):
    parsed_category = parse_category_upload(category_file)
    return apply_category_map_to_import(import_record, parsed_category)


@transaction.atomic
def update_import_customer(import_customer, *, email=None, category=None, is_ignored=None, notes=None):
    customer = import_customer.customer
    fields = []
    if email is not None:
        customer.email = email.strip()
        import_customer.email = customer.email
        fields.append("email")
    if category is not None:
        valid_categories = {choice.value for choice in AccountingCategory}
        if category not in valid_categories:
            raise ValidationError("Invalid customer category.")
        customer.category = category
        import_customer.category = category
        fields.append("category")
    if is_ignored is not None:
        if isinstance(is_ignored, str):
            customer.is_ignored = is_ignored.strip().lower() in {"1", "true", "yes", "on"}
        else:
            customer.is_ignored = bool(is_ignored)
        import_customer.is_ignored = customer.is_ignored
        fields.append("is_ignored")
    if notes is not None:
        customer.notes = notes
        fields.append("notes")
    if fields:
        customer.save(update_fields=[*set(fields), "updated_at"])

    status, is_due = build_summary_status(
        customer,
        list(import_customer.invoice_rows.all()),
        import_customer.overdue_amount,
        import_customer.max_days,
    )
    import_customer.is_due = is_due
    import_customer.status = status
    import_customer.save(update_fields=["email", "category", "is_ignored", "is_due", "status", "updated_at"])
    refresh_import_counts(import_customer.accounting_import)
    return import_customer


def refresh_import_counts(import_record):
    summaries = import_record.customers.all()
    import_record.customer_count = summaries.count()
    import_record.due_customer_count = summaries.filter(is_due=True, is_ignored=False).count()
    import_record.generated_statement_count = import_record.due_customer_count
    import_record.save(update_fields=["customer_count", "due_customer_count", "generated_statement_count", "updated_at"])


def email_preview_for_import_customer(import_customer):
    company = import_customer.customer_name
    filename = statement_filename(import_customer)
    return {
        "ready": bool(import_customer.email and import_customer.is_due and not import_customer.is_ignored),
        "email": import_customer.email,
        "email_status": "Ready" if import_customer.email else "Email missing",
        "subject": f"Overdue Payment - {company} with Al Ameen",
        "attachment_filename": filename,
        "body": (
            "Greetings,\n\n"
            "Please find attached the statement of account for your company. Kindly verify the statement "
            "and clear the outstanding amount at the earliest to keep in line with the agreed payment terms.\n\n"
            "Please make the payment on an urgent basis.\n\n"
            "Your cooperation in this regard is highly appreciated.\n\n"
            "If payment has recently been made, please accept our thanks and ignore this reminder.\n\n"
            "Regards,\nAl Ameen Pharmacy"
        ),
    }


def statement_filename(import_customer, style="professional"):
    name_parts = [import_customer.customer_code, import_customer.customer_name]
    raw_name = "_".join(part for part in name_parts if part)
    safe_name = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in raw_name)
    safe_name = "_".join(part for part in safe_name.split("_") if part)[:90] or f"statement_{import_customer.id}"
    report_date = import_customer.accounting_import.report_date.isoformat() if import_customer.accounting_import.report_date else "statement"
    return f"{safe_name}_{report_date}_statement.pdf"


def filter_invoice_rows_for_period(import_customer, *, date_from=None, date_to=None):
    rows = list(import_customer.invoice_rows.all())
    if date_from:
        rows = [row for row in rows if row.invoice_date and row.invoice_date >= date_from]
    if date_to:
        rows = [row for row in rows if row.invoice_date and row.invoice_date <= date_to]
    return sorted(
        rows,
        key=lambda row: (
            row.invoice_date or date.min,
            row.invoice_number or row.bill_number or "",
            row.source_row_number,
            row.id or 0,
        ),
    )


def statement_ledger(import_customer, *, date_from=None, date_to=None):
    rows = filter_invoice_rows_for_period(import_customer, date_from=date_from, date_to=date_to)
    invoice_dates = [row.invoice_date for row in rows if row.invoice_date]
    period_start = date_from or (min(invoice_dates) if invoice_dates else None)
    period_end = date_to or (max(invoice_dates) if invoice_dates else None)
    running_balance = Decimal("0.00")
    lines = []
    total_debit = Decimal("0.00")
    total_credit = Decimal("0.00")
    for row in rows:
        row_value = row.total if row.total is not None else row.amount
        if row_value >= 0:
            debit = row_value
            credit = Decimal("0.00")
        else:
            debit = Decimal("0.00")
            credit = abs(row_value)
        running_balance += debit - credit
        total_debit += debit
        total_credit += credit
        lines.append(
            {
                "row": row,
                "doc_type": "Invoice" if debit else "Credit",
                "debit": debit,
                "credit": credit,
                "balance": running_balance,
            }
        )

    bucket_0_30 = sum((row.bucket_0_30 for row in rows), Decimal("0.00"))
    bucket_30_60 = sum((row.bucket_30_60 for row in rows), Decimal("0.00"))
    bucket_60_90 = sum((row.bucket_60_90 for row in rows), Decimal("0.00"))
    bucket_over_90 = sum((row.bucket_over_90 for row in rows), Decimal("0.00"))
    overdue_amount = bucket_30_60 + bucket_60_90 + bucket_over_90
    max_days = max((row.days for row in rows), default=0)
    net_value = total_debit - total_credit
    is_due = overdue_amount != 0 or max_days > 30 or any(invoice_is_due(row) for row in rows)
    if import_customer.is_ignored:
        status = AccountingImportCustomer.STATUS_IGNORED
        is_due = False
    else:
        status = AccountingImportCustomer.STATUS_DUE if is_due else AccountingImportCustomer.STATUS_NOT_DUE

    return {
        "lines": lines,
        "invoice_count": len(rows),
        "total_debit": total_debit,
        "total_credit": total_credit,
        "net_value": net_value,
        "final_balance": running_balance,
        "total_outstanding": net_value,
        "bucket_0_30": bucket_0_30,
        "bucket_30_60": bucket_30_60,
        "bucket_60_90": bucket_60_90,
        "bucket_over_90": bucket_over_90,
        "overdue_amount": overdue_amount,
        "max_days": max_days,
        "is_due": is_due,
        "status": status,
        "date_from": date_from,
        "date_to": date_to,
        "period_start": period_start,
        "period_end": period_end,
    }
