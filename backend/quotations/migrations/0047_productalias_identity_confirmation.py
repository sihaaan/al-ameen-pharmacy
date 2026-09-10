from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotations", "0046_companyproductidentitymatch")]

    operations = [
        migrations.AddField(
            model_name="productalias",
            name="identity_confirmation",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
    ]
