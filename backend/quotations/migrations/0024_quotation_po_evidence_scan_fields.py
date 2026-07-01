from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotations", "0023_alter_quotationoutcomepoimport_source_type_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="po_evidence_last_scan_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="quotation",
            name="po_evidence_last_scan_error",
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name="quotation",
            name="po_evidence_last_scanned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
