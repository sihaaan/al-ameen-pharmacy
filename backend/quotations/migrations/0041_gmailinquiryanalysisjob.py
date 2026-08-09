import uuid

import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("quotations", "0040_gmailinquiryimport_analysis_progress"),
    ]

    operations = [
        migrations.CreateModel(
            name="GmailInquiryAnalysisJob",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "job_uuid",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        unique=True,
                    ),
                ),
                ("source_fingerprint", models.CharField(max_length=64)),
                (
                    "result_source_fingerprint",
                    models.CharField(blank=True, max_length=64),
                ),
                ("analysis_attempt", models.PositiveIntegerField()),
                ("source_generation", models.CharField(max_length=32)),
                ("force_requested", models.BooleanField(default=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("completed", "Completed"),
                            ("failed", "Failed"),
                            ("superseded", "Superseded"),
                            ("cancelled", "Cancelled"),
                        ],
                        db_index=True,
                        default="queued",
                        max_length=20,
                    ),
                ),
                (
                    "progress_stage",
                    models.CharField(default="queued", max_length=40),
                ),
                ("attempt_count", models.PositiveSmallIntegerField(default=0)),
                ("lease_owner", models.CharField(blank=True, max_length=128)),
                ("lease_token", models.CharField(blank=True, max_length=64)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("heartbeat_at", models.DateTimeField(blank=True, null=True)),
                (
                    "safe_error_category",
                    models.CharField(blank=True, max_length=64),
                ),
                ("queued_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "gmail_import",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="analysis_jobs",
                        to="quotations.gmailinquiryimport",
                    ),
                ),
                (
                    "requested_by",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="requested_gmail_inquiry_analysis_jobs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["status", "queued_at"],
                        name="gmail_job_status_queued_idx",
                    ),
                    models.Index(
                        fields=["gmail_import", "created_at"],
                        name="gmail_job_imp_created_idx",
                    ),
                    models.Index(
                        fields=["status", "lease_expires_at"],
                        name="gmail_job_status_lease_idx",
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("gmail_import", "source_generation"),
                        name="uniq_gmail_analysis_import_generation",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(status__in=("queued", "running")),
                        fields=("gmail_import",),
                        name="uniq_active_gmail_analysis_job",
                    ),
                ],
            },
        ),
    ]
