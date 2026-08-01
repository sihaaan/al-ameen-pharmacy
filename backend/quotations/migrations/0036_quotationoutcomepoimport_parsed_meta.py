from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0035_quotationemailoutboundsnapshot_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotationoutcomepoimport",
            name="parsed_meta",
            field=models.JSONField(
                blank=True,
                db_default=models.Value({}, output_field=models.JSONField()),
                default=dict,
            ),
        ),
    ]
