from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotations", "0057_inquiry_line_brand")]

    operations = [
        # Preserve the terms on existing quotations; only new quotations default to immediate supply.
        migrations.AddField(
            model_name="quotation",
            name="delivery_time",
            field=models.CharField(blank=True, default="", db_default="", max_length=200),
        ),
        migrations.AlterField(
            model_name="quotation",
            name="delivery_time",
            field=models.CharField(
                blank=True, default="Immediate (subject to stock availability)", db_default="", max_length=200,
            ),
        ),
    ]
