"""Delivery quantities are evidence of fulfilment, separate from quotation acceptance."""
from collections import defaultdict
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import DeliveryNote, DeliveryNoteLine, Quotation, QuotationAuditLog, QuotationLine


def accepted_quantity(line):
    if line.match_status == QuotationLine.MATCH_IGNORED or line.outcome_status not in {
        QuotationLine.OUTCOME_ACCEPTED, QuotationLine.OUTCOME_QUANTITY_CHANGED,
    }:
        return Decimal("0")
    return max(line.accepted_quantity or Decimal("0"), Decimal("0"))


def delivery_allocations(quotation, *, exclude_note=None):
    """Issued quantities reserve stock on the order; only confirmed receipts fulfil it."""
    rows = DeliveryNoteLine.objects.filter(
        delivery_note__quotation=quotation,
        delivery_note__status__in=[DeliveryNote.STATUS_ISSUED, DeliveryNote.STATUS_DELIVERED],
    )
    if exclude_note:
        rows = rows.exclude(delivery_note=exclude_note)
    totals = defaultdict(lambda: {"issued": Decimal("0"), "delivered": Decimal("0")})
    for row in rows.values("quotation_line_id", "delivery_note__status").annotate(
        sent=Sum("quantity"), received=Sum("received_quantity"),
    ):
        key = "issued" if row["delivery_note__status"] == DeliveryNote.STATUS_ISSUED else "delivered"
        totals[row["quotation_line_id"]][key] += (row["sent"] if key == "issued" else row["received"]) or Decimal("0")
    return totals


def assert_accepted_quantity_covers_deliveries(line):
    if not line.pk:
        return
    rows = DeliveryNoteLine.objects.filter(quotation_line=line)
    issued = rows.filter(delivery_note__status=DeliveryNote.STATUS_ISSUED).aggregate(total=Sum("quantity"))["total"] or 0
    delivered = rows.filter(delivery_note__status=DeliveryNote.STATUS_DELIVERED).aggregate(total=Sum("received_quantity"))["total"] or 0
    if accepted_quantity(line) < issued + delivered:
        raise ValidationError(
            f"{line.item_name_snapshot}: accepted quantity cannot be lower than quantities already issued or delivered. "
            "Cancel any incorrect delivery note first; its history will be retained."
        )


def order_progress(quotation, *, allocations=None):
    totals = allocations if allocations is not None else delivery_allocations(quotation)
    lines = []
    for line in quotation.lines.all():
        accepted = accepted_quantity(line)
        if accepted <= 0:
            continue
        counts = totals.get(line.pk, {})
        delivered = counts.get("delivered", Decimal("0"))
        issued = counts.get("issued", Decimal("0"))
        lines.append({
            "id": line.pk, "item_name": line.item_name_snapshot, "description": line.description,
            "unit": line.unit, "accepted_quantity": str(accepted), "delivered_quantity": str(delivered),
            "issued_quantity": str(issued), "remaining_quantity": str(max(accepted - delivered, Decimal("0"))),
            "available_quantity": str(max(accepted - delivered - issued, Decimal("0"))),
        })
    if quotation.status == Quotation.STATUS_CANCELLED:
        state = "cancelled"
    elif not lines:
        state = "needs_review"
    elif all(Decimal(row["remaining_quantity"]) == 0 for row in lines):
        state = "completed"
    elif any(Decimal(row["delivered_quantity"]) > 0 for row in lines):
        state = "partially_delivered"
    elif any(Decimal(row["issued_quantity"]) > 0 for row in lines):
        state = "in_progress"
    else:
        state = "accepted"
    return {
        "id": quotation.pk, "quotation_number": quotation.quotation_number,
        "company": quotation.company_id, "company_name": quotation.company.name,
        "outcome_status": quotation.outcome_status, "accepted_date": quotation.outcome_date,
        "delivery_status": state, "line_count": len(lines),
        "completed_line_count": sum(Decimal(row["remaining_quantity"]) == 0 for row in lines),
        "lpo_numbers": [lpo.lpo_number for lpo in quotation.lpos.all() if lpo.lpo_number],
        "lines": lines,
    }


def _audit(note, actor, message, changes=None):
    from .services import audit_log
    audit_log(actor, QuotationAuditLog.ACTION_UPDATED, note, message=message, changes=changes)


def lock_note(note):
    # Match the acceptance workflow's lock order and avoid locking nullable outer joins.
    if note.quotation_id:
        Quotation.objects.select_for_update().get(pk=note.quotation_id)
    return DeliveryNote.objects.select_for_update().get(pk=note.pk)


@transaction.atomic
def issue_delivery_note(note, actor):
    note = lock_note(note)
    if note.status == DeliveryNote.STATUS_ISSUED:
        return note
    if note.status != DeliveryNote.STATUS_DRAFT:
        raise ValidationError("Only a draft delivery note can be issued.")
    lines = list(note.lines.filter(deliver_later=False).select_related("quotation_line"))
    if not lines:
        raise ValidationError("Choose at least one item to deliver now before issuing the note. Items marked Deliver later can stay in a draft.")
    if note.quotation_id:
        if note.quotation.status not in {Quotation.STATUS_FINALIZED, Quotation.STATUS_SENT}:
            raise ValidationError("Deliveries require a finalized or sent quotation with reviewed accepted quantities.")
        totals = delivery_allocations(note.quotation, exclude_note=note)
        for line in lines:
            source = line.quotation_line
            if not source or source.quotation_id != note.quotation_id:
                raise ValidationError("Every delivery item must belong to this quotation.")
            counts = totals.get(source.pk, {})
            available = accepted_quantity(source) - counts.get("issued", 0) - counts.get("delivered", 0)
            if line.quantity > available:
                raise ValidationError(f"{line.item_name}: only {max(available, 0)} {line.unit} remains available to issue. Refresh the order.")
    later_lines = list(note.lines.filter(deliver_later=True))
    if later_lines:
        later = DeliveryNote.objects.create(
            continued_from=note, created_by=actor,
            **{field: getattr(note, field) for field in (
                "company", "quotation", "delivery_date", "lpo_number", "invoice_number", "quotation_reference",
                "customer_name", "customer_address", "customer_trn", "delivery_address", "attention",
                "contact_phone", "notes", "lpo_import",
            )},
        )
        # Move the saved rows into a linked draft in the same transaction as issue.
        # They cannot count as issued quantities or disappear when the page closes.
        note.lines.filter(pk__in=[line.pk for line in later_lines]).update(
            delivery_note=later, deliver_later=False,
        )
        _audit(note, actor, f"Saved deferred items in {later.delivery_number}.", {
            "later_delivery_id": later.pk,
            "lines": [{"id": line.pk, "item_name": line.item_name, "quantity": str(line.quantity)} for line in later_lines],
        })
        from .services import audit_log
        audit_log(actor, QuotationAuditLog.ACTION_CREATED, later,
                  message=f"Created {later.delivery_number} for items deferred from {note.delivery_number}.")
    note.status = DeliveryNote.STATUS_ISSUED
    note.issued_at = timezone.now()
    note.issued_by = actor
    note.save(update_fields=["status", "issued_at", "issued_by", "updated_at"])
    _audit(note, actor, f"Issued delivery note {note.delivery_number}.", {"status": note.status})
    return note


@transaction.atomic
def confirm_delivery_note(note, data, actor):
    note = lock_note(note)
    if note.status == DeliveryNote.STATUS_DELIVERED:
        return note
    if note.status != DeliveryNote.STATUS_ISSUED:
        raise ValidationError("Issue the delivery note before confirming receipt.")
    received_date = data["received_date"]
    if received_date > timezone.localdate():
        raise ValidationError("Receipt date cannot be in the future.")
    lines = list(note.lines.all())
    updates = data.get("lines")
    received = {line.pk: line.quantity for line in lines}
    if updates is not None:
        ids = [row["id"] for row in updates]
        if len(ids) != len(set(ids)) or set(ids) != set(received):
            raise ValidationError("Confirm the received quantity for every delivery item exactly once.")
        received = {row["id"]: row["received_quantity"] for row in updates}
    if not any(qty > 0 for qty in received.values()):
        raise ValidationError("At least one item must be received. Cancel the note if nothing was delivered.")
    for line in lines:
        qty = received[line.pk]
        if qty < 0 or qty > line.quantity:
            raise ValidationError(f"{line.item_name}: received quantity must be between zero and {line.quantity}.")
        line.received_quantity = qty
        line.save(update_fields=["received_quantity"])
    note.status = DeliveryNote.STATUS_DELIVERED
    note.received_by = data["received_by"]
    note.received_date = received_date
    note.receipt_reference = data.get("receipt_reference", "")
    note.receipt_notes = data.get("receipt_notes", "")
    note.confirmed_by = actor
    note.confirmed_at = timezone.now()
    note.save()
    _audit(note, actor, f"Confirmed receipt of {note.delivery_number}.", {
        "received_by": note.received_by, "received_date": str(received_date),
        "quantities": {str(key): str(value) for key, value in received.items()},
    })
    return note


@transaction.atomic
def cancel_delivery_note(note, reason, actor):
    note = lock_note(note)
    if note.status == DeliveryNote.STATUS_CANCELLED:
        return note
    previous = note.status
    note.status = DeliveryNote.STATUS_CANCELLED
    note.cancellation_reason = reason
    note.cancelled_at = timezone.now()
    note.save(update_fields=["status", "cancellation_reason", "cancelled_at", "updated_at"])
    _audit(note, actor, f"Cancelled delivery note {note.delivery_number}.", {
        "previous_status": previous, "reason": reason,
    })
    return note
