from django.db import migrations, models
from django.conf import settings
import django.db.models.deletion


def designate_existing_shared_mailbox(apps, schema_editor):
    GmailOAuthConnection = apps.get_model("quotations", "GmailOAuthConnection")
    connection = (
        GmailOAuthConnection.objects.filter(status="connected")
        .order_by("-updated_at", "-id")
        .first()
    )
    if connection:
        GmailOAuthConnection.objects.filter(pk=connection.pk).update(is_shared=True)


def clear_shared_designation(apps, schema_editor):
    GmailOAuthConnection = apps.get_model("quotations", "GmailOAuthConnection")
    GmailOAuthConnection.objects.filter(is_shared=True).update(is_shared=False)


class Migration(migrations.Migration):

    dependencies = [
        ("quotations", "0025_alter_companypricehistory_unit_price_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="gmailoauthconnection",
            name="is_shared",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.RunPython(designate_existing_shared_mailbox, clear_shared_designation),
        migrations.AddConstraint(
            model_name="gmailoauthconnection",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_shared", True)),
                fields=("is_shared",),
                name="unique_shared_gmail_connection",
            ),
        ),
        migrations.AddField(
            model_name="contractintelligencesource",
            name="gmail_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="contract_sources",
                to="quotations.gmailoauthconnection",
            ),
        ),
        migrations.AddField(
            model_name="contractintelligencesource",
            name="mailbox_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="quotationpoevidence",
            name="gmail_connection",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="po_evidence",
                to="quotations.gmailoauthconnection",
            ),
        ),
        migrations.AddField(
            model_name="quotationpoevidence",
            name="mailbox_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="quotationpoevidence",
            name="link_approved_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quotationpoevidence",
            name="link_approved_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="approved_quote_po_evidence",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="quotationlpo",
            name="gmail_evidence",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="canonical_lpo",
                to="quotations.quotationpoevidence",
            ),
        ),
        migrations.AddField(
            model_name="quotationlpo",
            name="gmail_message_id",
            field=models.CharField(blank=True, db_index=True, max_length=255),
        ),
        migrations.AddField(
            model_name="quotationlpo",
            name="mailbox_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AlterField(
            model_name="quotationlpo",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("file", "File"),
                    ("pasted_text", "Pasted text"),
                    ("gmail", "Gmail evidence"),
                ],
                default="file",
                max_length=30,
            ),
        ),
    ]
