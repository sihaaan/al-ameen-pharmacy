from datetime import date, datetime

from django.utils import timezone
from django.utils.dateparse import parse_date as parse_iso_date


def parse_accounting_date(value):
    value = (value or "").strip()
    if not value:
        return None
    parsed = parse_iso_date(value)
    if parsed:
        return parsed
    for date_format in ("%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, date_format).date()
        except ValueError:
            continue
    return None


def format_accounting_date(value):
    if not value:
        return ""
    if isinstance(value, datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        value = value.date()
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return ""


def format_accounting_datetime(value):
    if not value:
        return ""
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.strftime("%d/%m/%Y, %I:%M:%S %p")


def format_accounting_period(start, end):
    if start and end:
        if start == end:
            return format_accounting_date(start)
        return f"{format_accounting_date(start)} to {format_accounting_date(end)}"
    if start:
        return format_accounting_date(start)
    if end:
        return format_accounting_date(end)
    return "No invoice rows"
