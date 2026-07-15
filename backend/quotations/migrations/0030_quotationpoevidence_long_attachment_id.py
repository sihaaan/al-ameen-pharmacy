import hashlib

from django.db import migrations, models
from django.db.models import Case, IntegerField, Value, When


def normalize_attachment_identities(apps, schema_editor):
    evidence_model = apps.get_model("quotations", "QuotationPOEvidence")

    def review_priority(evidence):
        if evidence.link_approved_at or evidence.status == "parsed":
            return 3
        if evidence.status == "not_relevant":
            return 2
        return 0

    def archived_key(evidence):
        digest = hashlib.sha256(
            f"{evidence.pk}:{evidence.source_key}".encode("utf-8")
        ).hexdigest()
        return f"superseded:{evidence.pk}:{digest}"

    queryset = (
        evidence_model.objects.exclude(selected_attachment_id="")
        .annotate(
            _review_priority=Case(
                When(link_approved_at__isnull=False, then=Value(0)),
                When(status="parsed", then=Value(0)),
                When(status="not_relevant", then=Value(1)),
                default=Value(2),
                output_field=IntegerField(),
            )
        )
        .order_by("_review_priority", "id")
    )
    for evidence in queryset.only(
        "id",
        "quotation_id",
        "gmail_connection_id",
        "gmail_message_id",
        "selected_attachment_id",
        "source_sha256",
        "source_key",
        "attachments",
        "status",
        "error",
        "link_approved_at",
    ).iterator(chunk_size=500):
        attachment_id = str(evidence.selected_attachment_id or "").strip()
        selected_parts = set()
        for attachment in evidence.attachments or []:
            if not isinstance(attachment, dict):
                continue
            identifiers = {
                str(attachment.get("attachment_id") or ""),
                str(attachment.get("source_gmail_attachment_id") or ""),
                str(attachment.get("part_id") or ""),
            }
            if attachment_id in identifiers or attachment.get("is_selected") is True:
                part_id = str(attachment.get("part_id") or "").strip()
                if part_id:
                    selected_parts.add(part_id)
        if len(selected_parts) == 1:
            stable_id = selected_parts.pop()
            source_hash = str(evidence.source_sha256 or "").strip().lower()
            source_key = (
                f"sha256:{source_hash}"[:255]
                if source_hash
                else f"attachment:{stable_id}"[:255]
            )
            collision = evidence_model.objects.filter(
                quotation_id=evidence.quotation_id,
                gmail_connection_id=evidence.gmail_connection_id,
                gmail_message_id=evidence.gmail_message_id,
                source_key=source_key,
            ).exclude(pk=evidence.pk).first()
            if collision and review_priority(evidence) > review_priority(collision):
                collision_updates = {"source_key": archived_key(collision)}
                if not review_priority(collision):
                    collision_updates.update(
                        {
                            "status": "superseded",
                            "error": (
                                "Superseded while consolidating a rotating Gmail "
                                "attachment token into the same reviewed MIME part."
                            ),
                        }
                    )
                evidence_model.objects.filter(pk=collision.pk).update(
                    **collision_updates
                )
                collision = None
            if not collision:
                evidence_model.objects.filter(pk=evidence.pk).update(
                    selected_attachment_id=stable_id,
                    source_key=source_key,
                )
                continue
            if (
                not review_priority(evidence)
                and evidence.status in {"candidate", "ambiguous", "failed"}
            ):
                evidence_model.objects.filter(pk=evidence.pk).update(
                    status="superseded",
                    error=(
                        "Superseded while consolidating a rotating Gmail attachment "
                        "token into the same reviewed MIME part."
                    ),
                )
                continue

        if evidence.source_sha256:
            continue
        legacy_key = f"attachment:{attachment_id}"[:255]
        if len(f"attachment:{attachment_id}") <= 255 or evidence.source_key != legacy_key:
            continue
        digest = hashlib.sha256(attachment_id.encode("utf-8")).hexdigest()
        evidence_model.objects.filter(pk=evidence.pk).update(
            source_key=f"attachment-sha256:{digest}"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0029_mailbox_po_match_resume"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quotationpoevidence",
            name="selected_attachment_id",
            field=models.TextField(blank=True),
        ),
        migrations.RunPython(
            normalize_attachment_identities,
            migrations.RunPython.noop,
        ),
    ]
