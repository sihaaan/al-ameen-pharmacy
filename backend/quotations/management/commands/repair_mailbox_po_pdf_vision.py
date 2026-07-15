import json

from django.core.management.base import BaseCommand, CommandError

from quotations.ai_parsing import AIParseError
from quotations.mailbox_po_audit import repair_mailbox_po_audit_pdf_vision
from quotations.models import MailboxPOAuditRun


class Command(BaseCommand):
    help = (
        "Read-only Gmail repair for OCR-required, legacy-size-skipped, or page-limit PDF "
        "attachment manifests in one mailbox PO audit run. No quotes, orders or outcomes change."
    )

    def add_arguments(self, parser):
        parser.add_argument("--audit-run", type=int, required=True)
        parser.add_argument(
            "--message-id",
            action="append",
            default=[],
            help="Optionally repair only this Gmail message id (repeatable).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=20,
            help=(
                "Maximum targeted attachments to inspect (default: 20). Rerun explicitly while "
                "repair_remaining is non-zero; use a different positive limit only after reviewing cost."
            ),
        )
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        run = (
            MailboxPOAuditRun.objects.select_related("gmail_connection")
            .filter(pk=options["audit_run"])
            .first()
        )
        if not run:
            raise CommandError("The requested mailbox PO audit run does not exist.")
        try:
            summary = repair_mailbox_po_audit_pdf_vision(
                run,
                message_ids=options.get("message_id") or [],
                limit=options.get("limit"),
                dry_run=options.get("dry_run", False),
                actor=run.requested_by,
            )
        except (AIParseError, RuntimeError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(json.dumps(summary, default=str, sort_keys=True))
        if summary.get("repair_remaining"):
            self.stdout.write(
                self.style.WARNING(
                    f"{summary['repair_remaining']} repair target(s) remain; rerun the command to resume."
                )
            )
        if summary["attachments_retryable"] or summary["attachments_missing"]:
            self.stdout.write(
                self.style.WARNING(
                    "Some targeted attachments remain retryable; rerun this command after resolving provider/Gmail errors."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Mailbox PDF repair completed; Gmail remained read-only and no commercial records changed."
                )
            )
