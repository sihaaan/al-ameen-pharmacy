from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("quotations", "0028_mailbox_po_inventory"),
    ]

    operations = [
        migrations.AlterField(
            model_name="mailboxpomatchrun",
            name="algorithm_version",
            field=models.CharField(default="mailbox_match_v2", max_length=50),
        ),
        migrations.AddField(
            model_name="mailboxpomatchrun",
            name="cursor_message_id",
            field=models.PositiveBigIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="mailboxpomatchrun",
            name="last_heartbeat_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mailboxpomatchrun",
            name="lease_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="mailboxpomatchrun",
            name="lease_token",
            field=models.CharField(blank=True, max_length=64),
        ),
    ]
