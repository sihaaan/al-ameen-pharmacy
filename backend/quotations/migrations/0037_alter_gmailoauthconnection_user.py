import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0036_quotationoutcomepoimport_parsed_meta"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AlterField(
            model_name="gmailoauthconnection",
            name="user",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="quotation_gmail_connection",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
