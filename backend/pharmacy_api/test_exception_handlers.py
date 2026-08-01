from unittest import mock

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings
from psycopg import errors as psycopg_errors
from rest_framework.test import APIRequestFactory
from rest_framework.views import APIView

from pharmacy_api.exception_handlers import (
    _database_interruption_sqlstate,
    json_exception_handler,
)


INTERRUPTED_DETAIL = (
    "The database interrupted this request. Its current state may be uncertain; "
    "refresh and verify it before taking further action."
)


class PostgreSQLLikeError(Exception):
    def __init__(self, message, *, sqlstate=None, pgcode=None):
        super().__init__(message)
        self.sqlstate = sqlstate
        self.pgcode = pgcode


def wrapped_database_error(sqlstate, *, message="private SQL detail"):
    driver_error = PostgreSQLLikeError(message, sqlstate=sqlstate)
    django_error = OperationalError("Django database wrapper")
    django_error.__cause__ = driver_error
    return django_error


class DatabaseInterruptionClassificationTests(SimpleTestCase):
    def test_maps_only_the_three_explicit_postgresql_interruption_states(self):
        for sqlstate in ("55P03", "40P01", "57014"):
            with self.subTest(sqlstate=sqlstate):
                self.assertEqual(
                    _database_interruption_sqlstate(wrapped_database_error(sqlstate)),
                    sqlstate,
                )

    def test_pinned_psycopg_errors_expose_the_expected_driver_contract(self):
        error_contracts = (
            (psycopg_errors.LockNotAvailable, "55P03"),
            (psycopg_errors.DeadlockDetected, "40P01"),
            (psycopg_errors.QueryCanceled, "57014"),
        )

        for error_type, sqlstate in error_contracts:
            with self.subTest(error_type=error_type.__name__):
                django_error = OperationalError("Django database wrapper")
                django_error.__cause__ = error_type("private database detail")
                self.assertEqual(
                    _database_interruption_sqlstate(django_error),
                    sqlstate,
                )

    def test_supports_legacy_pgcode_on_a_nested_context(self):
        driver_error = PostgreSQLLikeError(
            "private SQL detail",
            pgcode="55p03",
        )
        middle_error = RuntimeError("middle wrapper")
        middle_error.__context__ = driver_error
        django_error = OperationalError("Django database wrapper")
        django_error.__cause__ = middle_error

        self.assertEqual(_database_interruption_sqlstate(django_error), "55P03")

    def test_requires_a_django_database_wrapper(self):
        driver_error = PostgreSQLLikeError(
            "not raised by Django",
            sqlstate="55P03",
        )

        self.assertIsNone(_database_interruption_sqlstate(driver_error))

    def test_does_not_map_other_or_missing_database_states(self):
        for sqlstate in (None, "40001", "23505"):
            with self.subTest(sqlstate=sqlstate):
                self.assertIsNone(
                    _database_interruption_sqlstate(wrapped_database_error(sqlstate))
                )

    def test_exception_chain_walk_is_cycle_safe_and_bounded(self):
        django_error = OperationalError("Django database wrapper")
        linked_errors = [RuntimeError(f"wrapper {index}") for index in range(20)]
        django_error.__cause__ = linked_errors[0]
        for current, following in zip(linked_errors, linked_errors[1:]):
            current.__cause__ = following
        linked_errors[-1].__cause__ = django_error

        self.assertIsNone(_database_interruption_sqlstate(django_error))


class DatabaseInterruptionResponseTests(SimpleTestCase):
    def setUp(self):
        self.request = APIRequestFactory().get("/api/quotations/quotes/1/")
        self.context = {"request": self.request, "view": self}

    def test_recognized_interruption_returns_generic_503_without_retry_header(self):
        with self.assertLogs("pharmacy_api.exception_handlers", "WARNING") as logs:
            response = json_exception_handler(
                wrapped_database_error("55P03", message="SELECT secret_customer"),
                self.context,
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.data,
            {
                "detail": INTERRUPTED_DETAIL,
                "code": "database_request_interrupted",
            },
        )
        self.assertNotIn("Retry-After", response.headers)
        combined_logs = "\n".join(logs.output)
        self.assertIn("sqlstate=55P03", combined_logs)
        self.assertNotIn("secret_customer", combined_logs)
        self.assertNotIn("Django database wrapper", combined_logs)

    @override_settings(DEBUG=True)
    def test_debug_mode_does_not_expose_recognized_database_details(self):
        with mock.patch("pharmacy_api.exception_handlers.logger.warning"):
            response = json_exception_handler(
                wrapped_database_error("57014", message="private SQL detail"),
                self.context,
            )

        self.assertEqual(response.status_code, 503)
        self.assertNotIn("error", response.data)
        self.assertNotIn("private", str(response.data))

    def test_unclassified_database_error_keeps_existing_generic_500(self):
        with mock.patch("pharmacy_api.exception_handlers.logger.exception"):
            response = json_exception_handler(
                wrapped_database_error("40001"),
                self.context,
            )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(
            response.data,
            {"detail": "Server error. Please try again or contact support."},
        )

    def test_configured_drf_handler_applies_the_same_contract(self):
        interrupted = wrapped_database_error("40P01")

        class InterruptedView(APIView):
            authentication_classes = []
            permission_classes = []

            def get(self, request):
                raise interrupted

        request = APIRequestFactory().get("/api/interrupted/")
        with mock.patch("pharmacy_api.exception_handlers.logger.warning"):
            response = InterruptedView.as_view()(request)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "database_request_interrupted")
        self.assertEqual(response.data["detail"], INTERRUPTED_DETAIL)
