from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0031_alter_inquiry_source_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="show_brand_column",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="quotationline",
            name="brand_name_snapshot",
            field=models.CharField(blank=True, default="", max_length=200),
            preserve_default=False,
        ),
    ]
