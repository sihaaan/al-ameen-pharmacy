import re
import secrets
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from quotations.gmail_analysis_jobs import (
    bounded_lease_seconds,
    claim_next_gmail_analysis_job,
    fail_exhausted_gmail_analysis_jobs,
    process_claimed_gmail_analysis_job,
)
from quotations.workflow_features import gmail_background_analysis_enabled


class Command(BaseCommand):
    help = "Run the durable, read-only Gmail inquiry analysis worker."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--max-jobs", type=int, default=0)
        parser.add_argument("--poll-seconds", type=float, default=2.0)
        parser.add_argument("--lease-seconds", type=int, default=600)
        parser.add_argument("--worker-id", default="")

    def handle(self, *args, **options):
        if not gmail_background_analysis_enabled():
            raise CommandError(
                "QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED is disabled."
            )
        max_jobs = max(0, int(options["max_jobs"] or 0))
        poll_seconds = min(30.0, max(0.1, float(options["poll_seconds"])))
        lease_seconds = bounded_lease_seconds(options["lease_seconds"])
        worker_id = re.sub(
            r"[^A-Za-z0-9_.:-]",
            "-",
            str(options["worker_id"] or "")[:128],
        ).strip("-._:")
        worker_id = worker_id or f"gmail-worker-{secrets.token_hex(8)}"
        processed = 0
        try:
            while True:
                close_old_connections()
                fail_exhausted_gmail_analysis_jobs()
                job, lease_token = claim_next_gmail_analysis_job(
                    worker_id,
                    lease_seconds=lease_seconds,
                )
                if job is None:
                    close_old_connections()
                    if options["once"] or (max_jobs and processed >= max_jobs):
                        break
                    time.sleep(poll_seconds)
                    continue
                try:
                    process_claimed_gmail_analysis_job(
                        job,
                        lease_token,
                        lease_seconds=lease_seconds,
                    )
                finally:
                    processed += 1
                    close_old_connections()
                if options["once"] or (max_jobs and processed >= max_jobs):
                    break
        except KeyboardInterrupt:
            self.stdout.write("Gmail inquiry worker stopped.")
        finally:
            close_old_connections()
        self.stdout.write(
            self.style.SUCCESS(
                f"Gmail inquiry worker processed {processed} job(s)."
            )
        )
