from django.db import migrations, models


PHARMACY_ADDRESS = "P.O. Box 39547, Frij Murar, Somali Street, Diera, Dubai"


def update_placeholder_address(apps, schema_editor):
    settings = apps.get_model("quotations", "QuotationSettings")
    query = settings.objects.using(schema_editor.connection.alias).filter(pk=1)
    previous = query.values_list("address", flat=True).first()
    if previous is not None and previous.strip().casefold() in {
        "", "dubai, united arab emirates", "dubai, uae", "dubai, u.a.e.",
    }:
        # Keep a separately maintained address and every issued invoice intact.
        query.filter(address=previous).update(address=PHARMACY_ADDRESS)


class Migration(migrations.Migration):
    dependencies = [("quotations", "0053_manual_tax_invoice_number")]

    operations = [
        migrations.AlterField(
            model_name="quotationsettings",
            name="address",
            field=models.TextField(blank=True, default=PHARMACY_ADDRESS),
        ),
        migrations.RunPython(update_placeholder_address, migrations.RunPython.noop),
    ]
