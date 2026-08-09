from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0039_gmailworkflowmetric"),
    ]

    operations = [
        migrations.AddField(
            model_name="gmailinquiryimport",
            name="analysis_progress_stage",
            field=models.CharField(
                blank=True,
                db_default="",
                default="",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name="gmailinquiryimport",
            name="analysis_progress_attempt",
            field=models.PositiveIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="gmailinquiryimport",
            name="analysis_progress_generation",
            field=models.CharField(
                blank=True,
                db_default="",
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="gmailinquiryimport",
            name="analysis_progress_error_category",
            field=models.CharField(
                blank=True,
                db_default="",
                default="",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="gmailinquiryimport",
            name="analysis_progress_updated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
