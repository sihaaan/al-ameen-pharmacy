from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("quotations", "0047_productalias_identity_confirmation")]
    operations = [
        migrations.AddField(
            model_name="deliverynote", name="lpo_import",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
    ]
