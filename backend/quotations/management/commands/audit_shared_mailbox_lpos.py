import json

from django.core.management.base import BaseCommand, CommandError

from quotations.contract_intelligence import resolve_gmail_connection
from quotations.mailbox_po_audit import scan_mailbox_po_audit_page, start_mailbox_po_audit
from quotations.mailbox_po_reconciliation import ALGORITHM_VERSION, reconcile_mailbox_po_audit
from quotations.models import MailboxPOAuditRun, MailboxPOMatchRun


class Command(BaseCommand):
    help = (
        "Inventory every incoming Gmail message since the first quotation, then create "
        "review-only PO/LPO evidence using item, quantity, commercial-value and timing checks."
    )

    def add_arguments(self, parser):
        parser.add_argument("--page-size", type=int, default=100)
        parser.add_argument("--max-pages", type=int, default=None)
        parser.add_argument("--resume-run", type=int, default=None)
        parser.add_argument("--restart", action="store_true")
        parser.add_argument("--inventory-only", action="store_true")
        parser.add_argument("--rematch", action="store_true")

    def handle(self, *args, **options):
        connection = resolve_gmail_connection(None, shared_only=True)
        if not connection or connection.status != connection.STATUS_CONNECTED:
            raise CommandError("No connected shared Gmail mailbox is configured.")

        run = None
        if options.get("resume_run"):
            run = MailboxPOAuditRun.objects.filter(
                pk=options["resume_run"],
                gmail_connection=connection,
            ).first()
            if not run:
                raise CommandError("The requested mailbox audit run does not exist for the shared mailbox.")
        elif not options.get("restart"):
            run = (
                MailboxPOAuditRun.objects.filter(
                    gmail_connection=connection,
                    status__in=[
                        MailboxPOAuditRun.STATUS_PENDING,
                        MailboxPOAuditRun.STATUS_RUNNING,
                        MailboxPOAuditRun.STATUS_FAILED,
                    ],
                )
                .order_by("-created_at")
                .first()
            )
        if not run:
            run = start_mailbox_po_audit(connection, requested_by=connection.user)
            self.stdout.write(f"Started mailbox audit run {run.id}: {run.gmail_query}")
        else:
            self.stdout.write(f"Using mailbox audit run {run.id} at page {run.pages_scanned + 1}.")

        max_pages = options.get("max_pages")
        pages_this_call = 0
        page_size = max(1, min(int(options.get("page_size") or 100), 500))
        while run.status != MailboxPOAuditRun.STATUS_COMPLETED and not run.exhausted:
            if max_pages is not None and pages_this_call >= max(0, int(max_pages)):
                break
            run = scan_mailbox_po_audit_page(run, page_size=page_size)
            pages_this_call += 1
            self.stdout.write(
                f"Run {run.id}: pages={run.pages_scanned}, messages={run.messages_scanned}, "
                f"relevant={run.relevant_messages}, status={run.status}"
            )
            if run.status == MailboxPOAuditRun.STATUS_FAILED:
                latest = (run.errors or [{}])[-1]
                raise CommandError(f"Mailbox audit stopped without advancing the cursor: {latest.get('error', 'unknown error')}")

        payload = {
            "audit_run_id": run.id,
            "audit_status": run.status,
            "exhausted": run.exhausted,
            "pages_scanned": run.pages_scanned,
            "messages_scanned": run.messages_scanned,
            "messages_created": run.messages_created,
            "relevant_messages": run.relevant_messages,
            "incomplete_messages": run.incomplete_messages,
            "inventory_complete": bool(run.exhausted and run.incomplete_messages == 0),
            "attachment_candidates": run.attachment_candidates,
            "attachment_bytes_fetched": run.attachment_bytes_fetched,
            "audit_errors": len(run.errors or []),
        }
        if run.status != MailboxPOAuditRun.STATUS_COMPLETED or not run.exhausted:
            self.stdout.write(json.dumps(payload, default=str, sort_keys=True))
            self.stdout.write(self.style.WARNING("Inventory is not exhausted yet; run the command again to resume."))
            return
        if options.get("inventory_only"):
            self.stdout.write(json.dumps(payload, default=str, sort_keys=True))
            return

        match_run = run.match_runs.filter(
            algorithm_version=ALGORITHM_VERSION,
            status=MailboxPOMatchRun.STATUS_COMPLETED,
        ).first()
        if not match_run or options.get("rematch"):
            match_run = reconcile_mailbox_po_audit(run, requested_by=connection.user)
        payload.update(
            {
                "match_run_id": match_run.id,
                "match_status": match_run.status,
                "match_summary": match_run.summary,
                "match_errors": len(match_run.errors or []),
            }
        )
        self.stdout.write(json.dumps(payload, default=str, sort_keys=True))
        if match_run.status != MailboxPOMatchRun.STATUS_COMPLETED:
            raise CommandError("Mailbox inventory completed, but review-evidence matching failed.")
        self.stdout.write(
            self.style.SUCCESS(
                "Mailbox-wide LPO audit completed. All associations remain review-only; no outcomes or orders changed."
            )
        )
