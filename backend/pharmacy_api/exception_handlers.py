import logging

from django.conf import settings
from django.db import DatabaseError
from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.response import Response


logger = logging.getLogger(__name__)


DATABASE_INTERRUPTION_SQLSTATES = frozenset(
    {
        "40P01",  # deadlock_detected
        "55P03",  # lock_not_available, including lock_timeout
        "57014",  # query_canceled, including statement_timeout
    }
)
MAX_EXCEPTION_CHAIN_NODES = 12


def _database_interruption_sqlstate(exc):
    """Return a narrowly recognized PostgreSQL interruption SQLSTATE.

    Django wraps psycopg exceptions and retains the driver exception as a
    cause. Walk both exception links defensively, but classify an interruption
    only when the chain also contains Django's DatabaseError wrapper. This
    avoids turning unrelated application errors with a coincidental attribute
    into retry-looking database responses.
    """

    pending = [exc]
    seen = set()
    states = []
    saw_database_error = False

    while pending and len(seen) < MAX_EXCEPTION_CHAIN_NODES:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        saw_database_error = saw_database_error or isinstance(current, DatabaseError)

        for attribute in ("sqlstate", "pgcode"):
            value = getattr(current, attribute, None)
            if isinstance(value, str):
                normalized = value.strip().upper()
                if len(normalized) == 5:
                    states.append(normalized)

        for attribute in ("__cause__", "__context__"):
            linked = getattr(current, attribute, None)
            if isinstance(linked, BaseException) and id(linked) not in seen:
                pending.append(linked)

    if not saw_database_error:
        return None
    return next(
        (state for state in states if state in DATABASE_INTERRUPTION_SQLSTATES),
        None,
    )


def json_exception_handler(exc, context):
    """
    Keep API failures JSON-shaped.

    DRF already handles validation/auth/permission errors. For unexpected
    exceptions, return a generic JSON response so the frontend never receives a
    raw Django HTML 500 page.
    """
    response = exception_handler(exc, context)
    if response is not None:
        return response

    request = context.get("request")
    path = getattr(request, "path", "") if request else ""
    view = context.get("view")
    view_name = view.__class__.__name__ if view else "unknown view"
    sqlstate = _database_interruption_sqlstate(exc)
    if sqlstate:
        # Do not attach exc_info here. Database error strings can include SQL or
        # customer-derived values; the stable state/path/view are sufficient
        # for operations while the client gets no database internals.
        logger.warning(
            "database_request_interrupted sqlstate=%s path=%s view=%s",
            sqlstate,
            path,
            view_name,
        )
        return Response(
            {
                "detail": (
                    "The database interrupted this request. Its current state may "
                    "be uncertain; refresh and verify it before taking further action."
                ),
                "code": "database_request_interrupted",
            },
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    logger.exception(
        "Unhandled API exception at %s in %s",
        path,
        view_name,
        exc_info=exc,
    )

    payload = {"detail": "Server error. Please try again or contact support."}
    if getattr(settings, "DEBUG", False):
        payload["error"] = str(exc)
    return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
