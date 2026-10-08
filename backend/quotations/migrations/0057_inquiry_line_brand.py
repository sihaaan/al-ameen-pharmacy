from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotations", "0056_inquiry_line_expiry")]

    operations = [
        migrations.AddField(
            model_name="inquiryline",
            name="brand_name",
            field=models.CharField(blank=True, max_length=200),
        ),
    ]
