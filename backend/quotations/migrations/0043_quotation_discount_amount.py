import decimal

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0042_preserve_gmail_progress_db_defaults"),
    ]

    operations = [
        migrations.AddField(
            model_name="quotation",
            name="discount_amount",
            field=models.DecimalField(
                db_default=decimal.Decimal("0.00"),
                decimal_places=2,
                default=decimal.Decimal("0.00"),
                max_digits=12,
                validators=[
                    django.core.validators.MinValueValidator(
                        decimal.Decimal("0.00")
                    )
                ],
            ),
        ),
        migrations.AddConstraint(
            model_name="quotation",
            constraint=models.CheckConstraint(
                condition=models.Q(discount_amount__gte=0),
                name="quotation_discount_amount_nonnegative",
            ),
        ),
    ]
