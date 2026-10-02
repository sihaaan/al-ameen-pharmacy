from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotations", "0054_pharmacy_document_address")]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="show_expiry_column",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="quotationline",
            name="expiry_date",
            field=models.CharField(blank=True, max_length=40),
        ),
    ]
