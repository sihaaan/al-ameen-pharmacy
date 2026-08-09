import hashlib
import threading
import time
import urllib.error
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from .gmail_inquiry_import import (
    STAGE_FETCHING_MESSAGES,
    STAGE_INSPECTING_DOCUMENTS,
    GmailInquiryImportError,
    _build_source_analysis,
    _content_fingerprint,
    _fetch_analysis_messages,
    _gmail_intake_json_get,
    _gmail_parallel_fetch_limit,
    _gmail_semantic_source_sha256,
    _native_thread_context,
    _prefetch_native_ai_attachments,
    _workflow_analysis_dimensions,
)
from .models import GmailInquiryImport, GmailOAuthConnection, QuotationSettings


MAILBOX_EMAIL = "quotes@example.com"


def message(message_id, *, thread_id="thread-1", sent_at=None, attachments=None):
    return {
        "gmail_message_id": message_id,
        "gmail_thread_id": thread_id,
        "label_ids": ["INBOX"],
        "full_headers": [{"name": "From", "value": "Buyer <buyer@example.com>"}],
        "subject": "RFQ",
        "sender": "Buyer <buyer@example.com>",
        "recipients": MAILBOX_EMAIL,
        "cc": "",
        "reply_to": "",
        "sent_at": sent_at or timezone.now(),
        "snippet": "Please quote",
        "newest_body_text": "Please quote",
        "newest_body_html": "",
        "attachment_manifest": list(attachments or []),
        "_attachment_refs": list(attachments or []),
    }


def metadata(message_id, *, thread_id="thread-1", sent_at=None):
    return {
        **message(message_id, thread_id=thread_id, sent_at=sent_at),
        "newest_body_text": "",
        "newest_body_html": "",
        "attachment_manifest": [],
        "_attachment_refs": [],
        "_metadata_only": True,
    }


def thread_metadata(*message_ids, thread_id="thread-1"):
    rows = [metadata(message_id, thread_id=thread_id) for message_id in message_ids]
    return {
        "messages": rows,
        "total_count": len(rows),
        "returned_count": len(rows),
        "limit": 50,
        "truncated": False,
        "gmail_thread_id": thread_id,
        "message_ids": list(message_ids),
    }


def import_record(*, mode, anchor="message-1", selected=None, thread="thread-1"):
    return SimpleNamespace(
        mode=mode,
        anchor_message_id=anchor,
        selected_message_ids=list(selected or []),
        gmail_thread_id=thread,
    )


def chained_http_error(status, *, retry_after=""):
    headers = {"Retry-After": retry_after} if retry_after else {}
    cause = urllib.error.HTTPError(
        "https://gmail.example.test",
        status,
        "private Google detail",
        headers,
        None,
    )
    try:
        raise RuntimeError(
            f"Google API request failed with HTTP {status}: private body"
        ) from cause
    except RuntimeError as exc:
        return exc


@override_settings(QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=True)
class GmailParallelMessageFetchTests(SimpleTestCase):
    def gmail_import(self, *, mode=GmailInquiryImport.MODE_AI_THREAD, **kwargs):
        return import_record(mode=mode, **kwargs)

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_wrong_thread_preflight_starts_no_body_worker(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        anchor_metadata.return_value = metadata(
            "canonical-anchor",
            thread_id="canonical-correct-thread",
        )
        timeline_metadata.return_value = thread_metadata(
            "different-message",
            thread_id="canonical-forged-thread",
        )

        with self.assertRaisesMessage(
            GmailInquiryImportError,
            "The Gmail handoff thread does not match the selected message.",
        ):
            _fetch_analysis_messages(
                self.gmail_import(
                    anchor="msg-f:anchor",
                    thread="thread-f:forged",
                ),
                object(),
            )

        fetch_body.assert_not_called()

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_selected_mode_fetches_only_selected_body_and_keeps_anchor_metadata_only(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        anchor_metadata.return_value = metadata("open-anchor")
        timeline_metadata.return_value = thread_metadata(
            "open-anchor",
            "chosen-message",
            "excluded-message",
        )
        fetch_body.return_value = message("chosen-message")

        _thread, selected, timeline, _meta = _fetch_analysis_messages(
            self.gmail_import(
                mode=GmailInquiryImport.MODE_SELECTED_MESSAGES,
                anchor="open-anchor",
                selected=["chosen-message"],
            ),
            object(),
        )

        self.assertEqual(
            [row["gmail_message_id"] for row in selected],
            ["chosen-message"],
        )
        self.assertEqual(
            {row["gmail_message_id"] for row in timeline},
            {"open-anchor", "chosen-message", "excluded-message"},
        )
        self.assertTrue(
            next(
                row for row in timeline if row["gmail_message_id"] == "open-anchor"
            )["_metadata_only"]
        )
        self.assertEqual(fetch_body.call_count, 1)
        self.assertEqual(fetch_body.call_args.args[:2], (None, "chosen-message"))
        self.assertEqual(fetch_body.call_args.kwargs["access_token"], "token")

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_current_mode_fetches_only_canonical_anchor_body(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        anchor_metadata.return_value = metadata("canonical-anchor")
        timeline_metadata.return_value = thread_metadata(
            "canonical-anchor",
            "excluded-message",
        )
        fetch_body.return_value = message("canonical-anchor")

        _thread, selected, timeline, _meta = _fetch_analysis_messages(
            self.gmail_import(
                mode=GmailInquiryImport.MODE_CURRENT_MESSAGE,
                anchor="msg-f:anchor",
            ),
            object(),
        )

        self.assertEqual(
            [row["gmail_message_id"] for row in selected],
            ["canonical-anchor"],
        )
        self.assertEqual(fetch_body.call_count, 1)
        self.assertEqual(fetch_body.call_args.args[1], "canonical-anchor")
        self.assertTrue(
            next(
                row
                for row in timeline
                if row["gmail_message_id"] == "excluded-message"
            )["_metadata_only"]
        )

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_selected_nonmember_fails_before_any_body_worker(
        self,
        _token,
        message_metadata,
        timeline_metadata,
        fetch_body,
    ):
        message_metadata.side_effect = [
            metadata("canonical-anchor"),
            metadata("canonical-outsider", thread_id="other-thread"),
        ]
        timeline_metadata.return_value = thread_metadata("canonical-anchor")

        with self.assertRaisesMessage(
            GmailInquiryImportError,
            "Every selected Gmail message must belong to the same thread.",
        ):
            _fetch_analysis_messages(
                self.gmail_import(
                    mode=GmailInquiryImport.MODE_SELECTED_MESSAGES,
                    anchor="msg-f:anchor",
                    selected=["msg-f:anchor", "msg-f:outsider"],
                ),
                object(),
            )

        fetch_body.assert_not_called()

    @override_settings(QUOTATION_GMAIL_PARALLEL_FETCH_LIMIT=2)
    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_body_fetch_is_bounded_and_reduced_in_chronological_order(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        ids = ["message-1", "message-2", "message-3", "message-4"]
        base = timezone.now()
        anchor_metadata.return_value = metadata(ids[0], sent_at=base)
        timeline_metadata.return_value = thread_metadata(*ids)
        active = 0
        peak = 0
        lock = threading.Lock()

        def fetch(_connection, message_id, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            # Reverse completion pressure; final output must ignore it.
            time.sleep(0.01 * (5 - int(message_id.rsplit("-", 1)[1])))
            with lock:
                active -= 1
            index = ids.index(message_id)
            return message(message_id, sent_at=base + timedelta(minutes=index))

        fetch_body.side_effect = fetch
        _thread, selected, _timeline, _meta = _fetch_analysis_messages(
            self.gmail_import(),
            object(),
        )

        self.assertGreaterEqual(peak, 2)
        self.assertLessEqual(peak, 2)
        self.assertEqual(
            [row["gmail_message_id"] for row in selected],
            ids,
        )

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_metadata_and_body_heartbeats_run_only_on_coordinator(
        self,
        _token,
        message_metadata,
        timeline_metadata,
        fetch_body,
    ):
        message_metadata.side_effect = [
            metadata("canonical-anchor"),
            metadata("canonical-selected"),
        ]
        timeline_metadata.return_value = thread_metadata(
            "canonical-anchor",
            "canonical-selected",
        )
        fetch_body.return_value = message("canonical-selected")
        caller_thread = threading.get_ident()
        pulses = []

        _fetch_analysis_messages(
            self.gmail_import(
                mode=GmailInquiryImport.MODE_SELECTED_MESSAGES,
                anchor="msg-f:anchor",
                selected=["msg-f:selected"],
            ),
            object(),
            coordinator_heartbeat=lambda stage: pulses.append(
                (stage, threading.get_ident())
            ),
        )

        # Anchor metadata, thread metadata, selected-message metadata, body.
        self.assertEqual(len(pulses), 4)
        self.assertEqual(
            pulses,
            [(STAGE_FETCHING_MESSAGES, caller_thread)] * 4,
        )

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_one_body_failure_fails_the_whole_batch(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        anchor_metadata.return_value = metadata("message-1")
        timeline_metadata.return_value = thread_metadata("message-1", "message-2")
        def fetch(_connection, message_id, **_kwargs):
            if message_id == "message-2":
                raise GmailInquiryImportError("safe Gmail read failure")
            return message(message_id)

        fetch_body.side_effect = fetch

        with self.assertRaisesMessage(
            GmailInquiryImportError,
            "safe Gmail read failure",
        ):
            _fetch_analysis_messages(self.gmail_import(), object())

    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import._message_metadata")
    @patch("quotations.gmail_inquiry_import.get_valid_access_token", return_value="token")
    def test_body_response_cannot_substitute_another_thread_member(
        self,
        _token,
        anchor_metadata,
        timeline_metadata,
        fetch_body,
    ):
        anchor_metadata.return_value = metadata("message-1")
        timeline_metadata.return_value = thread_metadata("message-1", "message-2")
        fetch_body.side_effect = lambda *_args, **_kwargs: message("message-2")

        with self.assertRaisesMessage(
            GmailInquiryImportError,
            "Every selected Gmail message must belong to the same thread.",
        ):
            _fetch_analysis_messages(
                self.gmail_import(mode=GmailInquiryImport.MODE_CURRENT_MESSAGE),
                object(),
            )


class GmailParallelFlagAndRetryTests(SimpleTestCase):
    @override_settings(
        QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=False,
        QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import.get_valid_access_token")
    @patch("quotations.gmail_inquiry_import._fetch_analysis_messages_sequential")
    def test_flag_off_is_exact_sequential_fallback(self, sequential, token):
        sentinel = ("thread", [], [], {})
        sequential.return_value = sentinel
        gmail_import = import_record(mode=GmailInquiryImport.MODE_CURRENT_MESSAGE)

        self.assertIs(_fetch_analysis_messages(gmail_import, object()), sentinel)
        sequential.assert_called_once()
        token.assert_not_called()

    @override_settings(
        QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=False,
        QUOTATION_GMAIL_BACKGROUND_ANALYSIS_ENABLED=True,
    )
    @patch("quotations.gmail_inquiry_import._thread_message_metadata")
    @patch("quotations.gmail_inquiry_import.fetch_mailbox_message")
    def test_sequential_reads_heartbeat_after_each_bounded_result(
        self,
        fetch_message,
        timeline_metadata,
    ):
        fetch_message.side_effect = [
            message("message-1"),
            message("message-2"),
        ]
        timeline_metadata.return_value = thread_metadata(
            "message-1",
            "message-2",
        )
        pulses = []

        _fetch_analysis_messages(
            import_record(mode=GmailInquiryImport.MODE_AI_THREAD),
            object(),
            coordinator_heartbeat=pulses.append,
        )

        # Anchor result, thread metadata, and both selected body results.
        self.assertEqual(
            pulses,
            [STAGE_FETCHING_MESSAGES] * 4,
        )

    def test_parallel_limit_defaults_and_clamps(self):
        for configured, expected in (
            (None, 4),
            ("invalid", 4),
            (0, 1),
            (1, 1),
            (4, 4),
            (20, 8),
        ):
            with self.subTest(configured=configured), override_settings(
                QUOTATION_GMAIL_PARALLEL_FETCH_LIMIT=configured
            ):
                self.assertEqual(_gmail_parallel_fetch_limit(), expected)

    def test_analysis_metrics_record_parallel_and_background_flag_state(self):
        gmail_import = SimpleNamespace(
            analysis_attempts=1,
            message_manifest=[],
            selected_message_ids=[],
            attachment_manifest=[],
        )
        for enabled in (False, True):
            with self.subTest(enabled=enabled), override_settings(
                QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=enabled
            ):
                dimensions = _workflow_analysis_dimensions(gmail_import)
                self.assertEqual(
                    dimensions["feature_flags"],
                    {
                        "background_analysis": False,
                        "compact_schema_shadow": False,
                        "gmail_parallel_fetch": enabled,
                        "xlsx_preextract_shadow": False,
                    },
                )

    @patch("quotations.gmail_inquiry_import.time.sleep")
    @patch("quotations.gmail_inquiry_import._json_request")
    def test_429_and_5xx_are_bounded_and_retry_after_is_capped(
        self,
        request_json,
        sleep,
    ):
        request_json.side_effect = [
            chained_http_error(429, retry_after="30"),
            chained_http_error(503, retry_after="1"),
            {"ok": True},
        ]

        self.assertEqual(
            _gmail_intake_json_get("https://gmail.example.test", token="token"),
            {"ok": True},
        )
        self.assertEqual(request_json.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [2.0, 1.0])

    @patch("quotations.gmail_inquiry_import.time.sleep")
    @patch("quotations.gmail_inquiry_import._json_request")
    def test_auth_and_not_found_errors_are_terminal_and_redacted(
        self,
        request_json,
        sleep,
    ):
        for status in (401, 403, 404):
            request_json.reset_mock()
            request_json.side_effect = chained_http_error(status)
            with self.subTest(status=status), self.assertRaises(
                GmailInquiryImportError
            ) as raised:
                _gmail_intake_json_get(
                    "https://gmail.example.test/private-id",
                    token="secret-token",
                )
            self.assertEqual(request_json.call_count, 1)
            self.assertNotIn("private", str(raised.exception).lower())
            self.assertNotIn("secret-token", str(raised.exception))
        sleep.assert_not_called()

    @patch("quotations.gmail_inquiry_import.time.sleep")
    @patch("quotations.gmail_inquiry_import._json_request")
    def test_transient_network_error_retries_but_stays_bounded(
        self,
        request_json,
        sleep,
    ):
        request_json.side_effect = [
            urllib.error.URLError("temporary private network detail"),
            {"ok": True},
        ]

        self.assertEqual(
            _gmail_intake_json_get("https://gmail.example.test", token="token"),
            {"ok": True},
        )
        self.assertEqual(request_json.call_count, 2)
        sleep.assert_called_once_with(0.2)


@override_settings(
    QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=True,
    QUOTATION_GMAIL_PARALLEL_FETCH_LIMIT=2,
)
class GmailParallelAttachmentTests(SimpleTestCase):
    def attachments(self, count=4):
        return [
            {
                "filename": f"rfq-{index}.pdf",
                "mime_type": "application/pdf",
                "size": 0,
                "attachment_id": f"attachment-{index}",
                "part_id": str(index),
            }
            for index in range(count)
        ]

    @patch("quotations.gmail_inquiry_import._inspect_native_ai_attachment")
    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_attachment_reads_are_bounded_and_inspection_is_serialized(
        self,
        fetch,
        inspect,
    ):
        attachments = self.attachments()
        active = 0
        peak = 0
        lock = threading.Lock()
        caller_thread = threading.get_ident()
        inspection_threads = []

        def fetch_bytes(_connection, _message_id, attachment, **_kwargs):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            index = int(attachment["part_id"])
            time.sleep(0.01 * (len(attachments) - index))
            with lock:
                active -= 1
            return bytes([index + 1])

        def inspect_bytes(attachment, content, **_kwargs):
            inspection_threads.append(threading.get_ident())
            index = int(attachment["part_id"])
            return {
                "filename": attachment["filename"],
                "mime_type": "application/pdf",
                "content": content,
                "source_sha256": str(index) * 64,
                "size": 1,
            }, ""

        fetch.side_effect = fetch_bytes
        inspect.side_effect = inspect_bytes
        outcomes = _prefetch_native_ai_attachments(
            [message("message-1", attachments=attachments)],
            mailbox_email=MAILBOX_EMAIL,
            access_token="token",
            max_bytes=100,
            max_total_bytes=100,
            max_native_files=10,
            coordinator_heartbeat=lambda stage: inspection_threads.append(
                (stage, threading.get_ident())
            ),
        )

        self.assertEqual(peak, 2)
        self.assertEqual(
            list(outcomes),
            [(1, 0), (1, 1), (1, 2), (1, 3)],
        )
        self.assertEqual(
            inspection_threads,
            [
                (STAGE_INSPECTING_DOCUMENTS, caller_thread),
                caller_thread,
            ]
            * 4,
        )
        self.assertTrue(
            all(call.kwargs["access_token"] == "token" for call in fetch.call_args_list)
        )
        self.assertTrue(
            all(call.args[0] is None for call in fetch.call_args_list)
        )

    @patch("quotations.gmail_inquiry_import._inspect_native_ai_attachment")
    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_source_order_combined_byte_failure_stops_sliding_window(
        self,
        fetch,
        inspect,
    ):
        attachments = self.attachments(6)
        fetch.return_value = b"1234"
        inspect.return_value = (
            {
                "filename": "rfq.pdf",
                "mime_type": "application/pdf",
                "content": b"1234",
                "source_sha256": "a" * 64,
                "size": 4,
            },
            "",
        )

        outcomes = _prefetch_native_ai_attachments(
            [message("message-1", attachments=attachments)],
            mailbox_email=MAILBOX_EMAIL,
            access_token="token",
            max_bytes=10,
            max_total_bytes=5,
            max_native_files=10,
        )

        self.assertEqual(list(outcomes), [(1, 0), (1, 1)])
        # Two initial workers may finish and one can already be in flight, but
        # the remaining source set is never submitted after the ordered cap.
        self.assertLessEqual(fetch.call_count, 3)

    @patch("quotations.gmail_inquiry_import._inspect_native_ai_attachment")
    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_outbound_attachments_and_file_limit_are_not_fetched(
        self,
        fetch,
        inspect,
    ):
        attachments = self.attachments(3)
        outbound = message("sent-message", attachments=[attachments[0]])
        outbound["label_ids"] = ["SENT"]
        outbound["sender"] = MAILBOX_EMAIL
        outbound["full_headers"] = [{"name": "From", "value": MAILBOX_EMAIL}]
        fetch.return_value = b"pdf"
        inspect.return_value = (
            {
                "filename": "rfq.pdf",
                "mime_type": "application/pdf",
                "content": b"pdf",
                "source_sha256": "a" * 64,
                "size": 3,
            },
            "",
        )

        outcomes = _prefetch_native_ai_attachments(
            [outbound, message("inbound", attachments=attachments[1:])],
            mailbox_email=MAILBOX_EMAIL,
            access_token="token",
            max_bytes=10,
            max_total_bytes=10,
            max_native_files=1,
        )

        self.assertEqual(list(outcomes), [(2, 0)])
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(fetch.call_args.args[1], "inbound")
        self.assertEqual(fetch.call_args.args[2]["attachment_id"], "attachment-1")

    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_declared_oversize_blocks_later_attachment_reads(self, fetch):
        attachments = self.attachments(2)
        attachments[0]["size"] = 11

        outcomes = _prefetch_native_ai_attachments(
            [message("message-1", attachments=attachments)],
            mailbox_email=MAILBOX_EMAIL,
            access_token="token",
            max_bytes=10,
            max_total_bytes=20,
            max_native_files=2,
        )

        self.assertEqual(outcomes, {})
        fetch.assert_not_called()

    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_image_unsupported_and_metadata_overflow_are_not_fetched(self, fetch):
        image = {
            "filename": "photo.png",
            "mime_type": "image/png",
            "size": 5,
            "attachment_id": "image",
            "part_id": "1",
        }
        unsupported = {
            "filename": "notes.txt",
            "mime_type": "text/plain",
            "size": 5,
            "attachment_id": "text",
            "part_id": "2",
        }
        overflow = [self.attachments(1)[0], image, unsupported] + [
            {
                **image,
                "filename": f"image-{index}.png",
                "attachment_id": f"image-{index}",
                "part_id": str(index + 3),
            }
            for index in range(98)
        ]

        outcomes = _prefetch_native_ai_attachments(
            [message("message-1", attachments=overflow)],
            mailbox_email=MAILBOX_EMAIL,
            access_token="token",
            max_bytes=10,
            max_total_bytes=20,
            max_native_files=2,
        )

        self.assertEqual(len(overflow), 101)
        self.assertEqual(outcomes, {})
        fetch.assert_not_called()


@override_settings(
    QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=True,
    QUOTATION_GMAIL_PARALLEL_FETCH_LIMIT=2,
    QUOTATION_MAILBOX_AI_VISION_ENABLED=True,
)
class GmailParallelBuildTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username="parallel-gmail-staff",
            password="unused",
            is_staff=True,
        )
        self.connection = GmailOAuthConnection.objects.create(
            user=self.staff,
            is_shared=True,
            email=MAILBOX_EMAIL,
            status=GmailOAuthConnection.STATUS_CONNECTED,
        )
        quotation_settings = QuotationSettings.get_solo()
        quotation_settings.ai_pdf_vision_enabled = True
        quotation_settings.save(update_fields=["ai_pdf_vision_enabled", "updated_at"])
        self.gmail_import = GmailInquiryImport.objects.create(
            gmail_connection=self.connection,
            mailbox_email=MAILBOX_EMAIL,
            gmail_thread_id="thread-1",
            anchor_message_id="message-1",
            source_fingerprint="parallel-build-test",
            claimed_by=self.staff,
            status=GmailInquiryImport.STATUS_CLAIMED,
        )

    @patch("quotations.gmail_inquiry_import._run_native_thread_analysis")
    @patch("quotations.gmail_inquiry_import._inspect_native_ai_attachment")
    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    def test_required_attachment_failure_prevents_provider_call(
        self,
        fetch_bytes,
        inspect,
        provider,
    ):
        attachments = [
            {
                "filename": f"required-rfq-{index}.pdf",
                "mime_type": "application/pdf",
                "size": 10,
                "attachment_id": f"attachment-{index}",
                "part_id": str(index),
            }
            for index in range(2)
        ]
        caller_thread = threading.get_ident()
        events = []

        def fetch_content(*_args, **_kwargs):
            events.append(("fetch", "", threading.get_ident()))
            return b"%PDF"

        def inspect_content(attachment, content, **_kwargs):
            events.append(("inspect", attachment["part_id"], threading.get_ident()))
            if attachment["part_id"] == "1":
                return None, "Attachment safety inspection failed."
            return (
                {
                    "filename": attachment["filename"],
                    "mime_type": "application/pdf",
                    "content": content,
                    "source_sha256": "a" * 64,
                    "size": len(content),
                    "inspection_warnings": [],
                },
                "",
            )

        def progress(stage):
            events.append(("progress", stage, threading.get_ident()))

        fetch_bytes.side_effect = fetch_content
        inspect.side_effect = inspect_content

        result = _build_source_analysis(
            [message("message-1", attachments=attachments)],
            self.connection,
            self.gmail_import,
            self.staff,
            gmail_access_token="token",
            progress_callback=progress,
        )

        provider.assert_not_called()
        self.assertFalse(result["preview"]["meta"]["ai_used"])
        self.assertEqual(
            [row["parse_status"] for row in result["attachment_manifest"]],
            ["skipped", "failed"],
        )
        inspecting_progress = next(
            event
            for event in events
            if event[:2] == ("progress", STAGE_INSPECTING_DOCUMENTS)
        )
        self.assertEqual(inspecting_progress[2], caller_thread)
        self.assertLess(events.index(inspecting_progress), next(
            index for index, event in enumerate(events) if event[0] == "fetch"
        ))
        self.assertTrue(
            all(event[2] == caller_thread for event in events if event[0] == "inspect")
        )

    @patch("quotations.gmail_inquiry_import._inspect_native_ai_attachment")
    @patch("quotations.gmail_inquiry_import._fetch_native_attachment_bytes")
    @patch("quotations.gmail_inquiry_import._run_native_thread_analysis")
    def test_parallel_toggle_does_not_change_attachment_fingerprint_or_ai_order(
        self,
        provider,
        fetch_bytes,
        inspect,
    ):
        provider_file_orders = []
        semantic_inputs = []

        def provider_result(source_messages, evidence, file_inputs, *_args, **_kwargs):
            provider_file_orders.append(
                [file_input["filename"] for file_input in file_inputs]
            )
            semantic_inputs.append(
                (
                    list(source_messages),
                    [dict(source) for source in evidence],
                    [dict(file_input) for file_input in file_inputs],
                )
            )
            return {
                "messages": {
                    "message-1": {
                        "classification": "initial_inquiry",
                        "usage": "used",
                        "reason": "Customer request.",
                        "confidence": 1.0,
                    }
                },
                "rows": [],
                "warnings": [],
                "thread_summary": "No rows.",
                "usage": {},
                "customer_identity": {
                    "company_name": "",
                    "contact_name": "",
                    "contact_email": "",
                    "source_keys": [],
                    "confidence": 0.0,
                    "reason": "",
                },
            }

        def fetch_content(_connection, _message_id, attachment, **_kwargs):
            # The second parallel GET wins the network race; source reduction
            # must still submit first.pdf before second.pdf.
            if attachment["part_id"] == "0":
                time.sleep(0.02)
            return f"content-{attachment['part_id']}".encode()

        def inspect_content(attachment, content, **_kwargs):
            return (
                {
                    "filename": attachment["filename"],
                    "mime_type": "application/pdf",
                    "content": content,
                    "source_sha256": (
                        "a" * 64 if attachment["part_id"] == "0" else "b" * 64
                    ),
                    "size": len(content),
                    "inspection_warnings": [],
                    "attachment_safety": {},
                    "pdf_fidelity": {},
                    "spreadsheet_fidelity": {},
                },
                "",
            )

        provider.side_effect = provider_result
        fetch_bytes.side_effect = fetch_content
        inspect.side_effect = inspect_content
        attachments = [
            {
                "filename": f"{name}.pdf",
                "mime_type": "application/pdf",
                "size": 9,
                "attachment_id": f"attachment-{index}",
                "part_id": str(index),
            }
            for index, name in enumerate(("first", "second"))
        ]
        source_message = message("message-1", attachments=attachments)

        with override_settings(QUOTATION_GMAIL_PARALLEL_FETCH_ENABLED=False):
            sequential = _build_source_analysis(
                [source_message],
                self.connection,
                self.gmail_import,
                self.staff,
            )
        parallel = _build_source_analysis(
            [source_message],
            self.connection,
            self.gmail_import,
            self.staff,
            gmail_access_token="token",
        )

        sequential_fingerprint = _content_fingerprint(
            MAILBOX_EMAIL,
            "thread-1",
            GmailInquiryImport.MODE_CURRENT_MESSAGE,
            ["message-1"],
            sequential["message_manifest"],
            sequential["attachment_manifest"],
        )
        parallel_fingerprint = _content_fingerprint(
            MAILBOX_EMAIL,
            "thread-1",
            GmailInquiryImport.MODE_CURRENT_MESSAGE,
            ["message-1"],
            parallel["message_manifest"],
            parallel["attachment_manifest"],
        )
        self.assertEqual(parallel_fingerprint, sequential_fingerprint)
        self.assertEqual(
            provider_file_orders,
            [["first.pdf", "second.pdf"], ["first.pdf", "second.pdf"]],
        )
        semantic_hashes = []
        for source_messages, evidence, file_inputs in semantic_inputs:
            context = _native_thread_context(
                source_messages,
                evidence,
                self.gmail_import.mode,
            )
            semantic_hashes.append(
                _gmail_semantic_source_sha256(
                    gmail_import=self.gmail_import,
                    message_ids=["message-1"],
                    sources=evidence,
                    file_inputs=file_inputs,
                    context_hash=hashlib.sha256(context.encode("utf-8")).hexdigest(),
                )
            )
        self.assertEqual(len(semantic_hashes), 2)
        self.assertEqual(semantic_hashes[0], semantic_hashes[1])
