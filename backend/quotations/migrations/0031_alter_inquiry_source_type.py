from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0030_quotationpoevidence_long_attachment_id"),
    ]

    operations = [
        migrations.AlterField(
            model_name="inquiry",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("manual", "Manual"),
                    ("pasted_text", "Pasted Text"),
                    ("excel", "Excel"),
                    ("pdf", "PDF"),
                    ("image", "Image"),
                ],
                default="manual",
                max_length=30,
            ),
        ),
    ]
