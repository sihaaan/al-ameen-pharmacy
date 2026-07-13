from django.db import migrations, models


LEGACY_RETIREMENT_MARKER = (
    "Superseded during the safe Gmail matching upgrade. "
    "Run a new scan to recreate only currently qualified links."
)


def retire_legacy_unapproved_candidates(apps, schema_editor):
    QuotationPOEvidence = apps.get_model("quotations", "QuotationPOEvidence")
    QuotationPOEvidence.objects.filter(
        status="candidate",
        link_approved_at__isnull=True,
    ).update(
        status="superseded",
        error=LEGACY_RETIREMENT_MARKER,
    )


def restore_legacy_candidate_status(apps, schema_editor):
    QuotationPOEvidence = apps.get_model("quotations", "QuotationPOEvidence")
    QuotationPOEvidence.objects.filter(
        status="superseded",
        link_approved_at__isnull=True,
        error=LEGACY_RETIREMENT_MARKER,
    ).update(status="candidate", error="")


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0026_shared_gmail_lpo_provenance"),
    ]

    operations = [
        migrations.AlterField(
            model_name="quotationpoevidence",
            name="status",
            field=models.CharField(
                choices=[
                    ("candidate", "Candidate"),
                    ("ambiguous", "Ambiguous"),
                    ("superseded", "Superseded"),
                    ("parsed", "Parsed"),
                    ("not_relevant", "Not relevant"),
                    ("failed", "Failed"),
                ],
                default="candidate",
                max_length=30,
            ),
        ),
        migrations.RunPython(
            retire_legacy_unapproved_candidates,
            restore_legacy_candidate_status,
        ),
    ]
