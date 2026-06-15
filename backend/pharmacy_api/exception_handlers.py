import logging

from django.conf import settings
from rest_framework import status
from rest_framework.views import exception_handler
from rest_framework.response import Response


logger = logging.getLogger(__name__)


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
    logger.exception(
        "Unhandled API exception at %s in %s",
        path,
        view.__class__.__name__ if view else "unknown view",
        exc_info=exc,
    )

    payload = {"detail": "Server error. Please try again or contact support."}
    if getattr(settings, "DEBUG", False):
        payload["error"] = str(exc)
    return Response(payload, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
