import django.db.models.deletion
from django.db import migrations, models

import quotations.gmail_workflow_metrics


class Migration(migrations.Migration):
    dependencies = [
        ("quotations", "0038_ensure_po_import_parsed_meta_db_default"),
    ]

    operations = [
        migrations.CreateModel(
            name="GmailWorkflowMetric",
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
                    "event_name",
                    models.CharField(
                        choices=[
                            ("handoff_created", "Add-on handoff created"),
                            ("handoff_claimed", "Handoff claimed"),
                            ("analysis_requested", "Analysis requested"),
                            ("analysis_started", "Analysis started"),
                            ("analysis_completed", "Analysis completed"),
                            ("analysis_failed", "Analysis failed"),
                            ("company_approved", "Company approved"),
                            ("reviewed_rows_saved", "Reviewed rows saved"),
                            (
                                "quotation_created_or_reused",
                                "Quotation created or reused",
                            ),
                            ("pricing_saved", "Pricing saved"),
                            ("email_preview_opened", "Email preview opened"),
                            ("send_initiated", "Send initiated"),
                            ("send_confirmed", "Send confirmed"),
                            ("send_failed", "Send failed"),
                            ("send_left_unknown", "Send left unknown"),
                            (
                                "reconciliation_completed",
                                "Reconciliation completed",
                            ),
                        ],
                        max_length=40,
                    ),
                ),
                (
                    "duration_ms",
                    models.PositiveBigIntegerField(blank=True, null=True),
                ),
                (
                    "counts",
                    models.JSONField(
                        blank=True,
                        default=dict,
                        validators=[
                            quotations.gmail_workflow_metrics.validate_metric_counts
                        ],
                    ),
                ),
                (
                    "selection_mode",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", "Not applicable"),
                            ("current_message", "Current message"),
                            ("selected_messages", "Selected messages"),
                            ("ai_thread", "AI-assisted thread"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "cache_state",
                    models.CharField(
                        choices=[
                            ("not_applicable", "Not applicable"),
                            ("hit", "Hit"),
                            ("miss", "Miss"),
                            ("bypassed", "Bypassed"),
                            ("unknown", "Unknown"),
                        ],
                        default="not_applicable",
                        max_length=20,
                    ),
                ),
                (
                    "feature_flags",
                    models.JSONField(
                        default=quotations.gmail_workflow_metrics.default_metric_feature_flags,
                        validators=[
                            quotations.gmail_workflow_metrics.validate_metric_feature_flags
                        ],
                    ),
                ),
                (
                    "outcome_code",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("", "Not specified"),
                            ("success", "Success"),
                            ("failure", "Failure"),
                            ("blocked", "Blocked"),
                            ("created", "Created"),
                            ("reused", "Reused"),
                            ("ready", "Ready"),
                            ("review_required", "Review required"),
                            ("confirmed", "Confirmed"),
                            ("unknown", "Unknown"),
                            ("reconciled_sent", "Reconciled sent"),
                            ("not_found", "Not found"),
                        ],
                        max_length=30,
                    ),
                ),
                (
                    "contract_versions",
                    models.JSONField(
                        default=quotations.gmail_workflow_metrics.default_metric_contract_versions,
                        validators=[
                            quotations.gmail_workflow_metrics.validate_metric_contract_versions
                        ],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "gmail_import",
                    models.ForeignKey(
                        db_index=False,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="workflow_metrics",
                        to="quotations.gmailinquiryimport",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "pk"],
                "indexes": [
                    models.Index(
                        fields=["event_name", "created_at"],
                        name="quotations__event_n_4aa97d_idx",
                    ),
                    models.Index(
                        fields=["gmail_import", "created_at"],
                        name="quotations__gmail_i_a7a382_idx",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        condition=(
                            models.Q(("duration_ms__isnull", True))
                            | models.Q(("duration_ms__lte", 604800000))
                        ),
                        name="gmail_workflow_metric_duration_bounded",
                    )
                ],
            },
        ),
    ]
