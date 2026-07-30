"""Google Workspace Gmail add-on HTTP endpoints for inquiry intake.

The endpoints intentionally have no Django session authentication. Google
authenticates every callback with a system ID token, and the event includes a
separate end-user ID token. Both signatures, issuers, audiences, and email
claims are verified before any mailbox or quotation data is accessed.
"""

from __future__ import annotations

import hmac
import json
import re
import urllib.parse
from email.utils import parseaddr
from functools import lru_cache

import jwt
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .contract_intelligence import (
    GMAIL_API_BASE,
    _header,
    _json_request,
    _message_datetime,
    get_valid_access_token,
    resolve_gmail_connection,
)
from .models import GmailOAuthConnection


GOOGLE_ID_TOKEN_CERTS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GOOGLE_ID_TOKEN_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
MAX_EVENT_BYTES = 256 * 1024
MAX_SELECTED_MESSAGE_IDS = 25
REQUIRED_GOOGLE_OAUTH_SCOPES = (
    "https://www.googleapis.com/auth/gmail.addons.execute",
    "https://www.googleapis.com/auth/gmail.addons.current.message.metadata",
    "https://www.googleapis.com/auth/userinfo.email",
)

MODE_CURRENT_MESSAGE = "current_message"
MODE_SELECTED_MESSAGES = "selected_messages"
MODE_AI_THREAD = "ai_thread"
ALLOWED_MODES = {
    MODE_CURRENT_MESSAGE,
    MODE_SELECTED_MESSAGES,
    MODE_AI_THREAD,
}


class GmailAddonError(Exception):
    status_code = 400
    public_message = "The Gmail add-on request could not be processed."

    def __init__(self, public_message=None):
        super().__init__(public_message or self.public_message)
        self.public_message = public_message or self.public_message


class GmailAddonAuthenticationError(GmailAddonError):
    status_code = 401
    public_message = "Google could not authenticate this add-on request."


class GmailAddonPermissionError(GmailAddonError):
    status_code = 403
    public_message = "This Gmail account is not allowed to import inquiries."


class GmailAddonConfigurationError(GmailAddonError):
    status_code = 503
    public_message = "The quotation Gmail add-on is not fully configured."


class GmailAddonSharedConnectionUnavailable(GmailAddonError):
    """The authenticated add-on user must reconnect the website mailbox."""

    public_message = "Shared Gmail must be reconnected."


class GmailAddonInputError(GmailAddonError):
    status_code = 400


@lru_cache(maxsize=1)
def _google_jwks_client():
    return jwt.PyJWKClient(
        GOOGLE_ID_TOKEN_CERTS_URL,
        cache_keys=True,
        lifespan=300,
        timeout=5,
    )


def _verify_google_id_token(token, *, audiences):
    """Verify a Google ID token locally against Google's cached signing keys."""

    token = str(token or "").strip()
    valid_audiences = [str(value).strip() for value in audiences or [] if str(value).strip()]
    if not token or not valid_audiences:
        raise GmailAddonAuthenticationError()
    try:
        signing_key = _google_jwks_client().get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=valid_audiences,
            leeway=60,
            options={"require": ["exp", "iat", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError, ValueError, TypeError) as exc:
        raise GmailAddonAuthenticationError() from exc
    if claims.get("iss") not in GOOGLE_ID_TOKEN_ISSUERS:
        raise GmailAddonAuthenticationError()
    return claims


def _normalized_email(value):
    return str(value or "").strip().casefold()


def _verified_email(claims):
    email = _normalized_email((claims or {}).get("email"))
    if not email or (claims or {}).get("email_verified") is not True:
        raise GmailAddonAuthenticationError()
    return email


def _constant_time_equal(left, right):
    left_value = str(left or "").encode("utf-8")
    right_value = str(right or "").encode("utf-8")
    return bool(left_value and right_value and hmac.compare_digest(left_value, right_value))


def _configured_https_url(setting_name):
    value = str(getattr(settings, setting_name, "") or "").strip()
    parsed = urllib.parse.urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise GmailAddonConfigurationError()
    return value


def _require_configuration():
    if not getattr(settings, "GMAIL_ADDON_ENABLED", False):
        raise GmailAddonError("Not found.")

    service_account_email = _normalized_email(
        getattr(settings, "GMAIL_ADDON_SERVICE_ACCOUNT_EMAIL", "")
    )
    mailbox_email = _normalized_email(
        getattr(settings, "GMAIL_ADDON_SHARED_MAILBOX_EMAIL", "")
    )
    oauth_client_id = str(getattr(settings, "GMAIL_ADDON_OAUTH_CLIENT_ID", "") or "").strip()
    allowed_audiences = list(getattr(settings, "GMAIL_ADDON_ALLOWED_AUDIENCES", []) or [])
    if not service_account_email or not mailbox_email or not oauth_client_id or not allowed_audiences:
        raise GmailAddonConfigurationError()

    contextual_url = _configured_https_url("GMAIL_ADDON_CONTEXTUAL_URL")
    action_url = _configured_https_url("GMAIL_ADDON_ACTION_URL")
    _configured_https_url("GMAIL_ADDON_HANDOFF_URL")
    if contextual_url not in allowed_audiences or action_url not in allowed_audiences:
        raise GmailAddonConfigurationError()
    return {
        "service_account_email": service_account_email,
        "mailbox_email": mailbox_email,
        "oauth_client_id": oauth_client_id,
        "allowed_audiences": allowed_audiences,
        "contextual_url": contextual_url,
        "action_url": action_url,
    }


def _parse_event(request):
    content_length = request.META.get("CONTENT_LENGTH")
    try:
        if content_length and int(content_length) > MAX_EVENT_BYTES:
            raise GmailAddonInputError("The Gmail add-on event was too large.")
    except (TypeError, ValueError):
        raise GmailAddonInputError("The Gmail add-on event was invalid.") from None
    if len(request.body) > MAX_EVENT_BYTES:
        raise GmailAddonInputError("The Gmail add-on event was too large.")
    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise GmailAddonInputError("The Gmail add-on event was invalid.") from None
    if not isinstance(payload, dict):
        raise GmailAddonInputError("The Gmail add-on event was invalid.")
    return payload


def _authenticate_system_event(request, event, config, *, expected_audience):
    """Authenticate Google before trusting consent or end-user event fields."""

    authorization = str(request.META.get("HTTP_AUTHORIZATION", "") or "")
    scheme, separator, system_token = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        raise GmailAddonAuthenticationError()
    system_token = system_token.strip()

    authorization_event = event.get("authorizationEventObject")
    if not isinstance(authorization_event, dict):
        raise GmailAddonAuthenticationError()
    body_system_token = str(authorization_event.get("systemIdToken") or "").strip()
    if (
        not system_token
        or not body_system_token
        or not _constant_time_equal(system_token, body_system_token)
    ):
        raise GmailAddonAuthenticationError()

    system_claims = _verify_google_id_token(
        system_token,
        audiences=[expected_audience],
    )
    if not _constant_time_equal(
        _verified_email(system_claims),
        config["service_account_email"],
    ):
        raise GmailAddonAuthenticationError()
    return authorization_event


def _missing_required_google_scopes(authorization_event):
    authorized_scopes = authorization_event.get("authorizedScopes")
    if not isinstance(authorized_scopes, list):
        authorized_scopes = []
    authorized = {
        str(scope).strip()
        for scope in authorized_scopes
        if isinstance(scope, str) and str(scope).strip()
    }
    return [
        scope
        for scope in REQUIRED_GOOGLE_OAUTH_SCOPES
        if scope not in authorized
    ]


def _requesting_google_scopes_response(scopes):
    """Return Google's granular-consent response using its wire field names."""

    return JsonResponse(
        {
            "requesting_google_scopes": {
                "scopes": list(scopes),
            }
        }
    )


def _authenticate_user_event(authorization_event, config):
    user_token = str(authorization_event.get("userIdToken") or "").strip()
    user_claims = _verify_google_id_token(
        user_token,
        audiences=[config["oauth_client_id"]],
    )
    if not _constant_time_equal(_verified_email(user_claims), config["mailbox_email"]):
        raise GmailAddonPermissionError()
    return user_claims


def _shared_connection(mailbox_email):
    connection = resolve_gmail_connection(
        None,
        connected_only=False,
        shared_only=True,
    )
    if (
        connection is None
        or getattr(connection, "status", "") != GmailOAuthConnection.STATUS_CONNECTED
    ):
        raise GmailAddonSharedConnectionUnavailable()
    if not _constant_time_equal(
        _normalized_email(getattr(connection, "email", "")),
        _normalized_email(mailbox_email),
    ):
        raise GmailAddonConfigurationError(
            "The connected shared Gmail mailbox does not match the add-on configuration."
        )
    return connection


def _shared_access_token(connection):
    try:
        return get_valid_access_token(connection)
    except RuntimeError as exc:
        # OAuth/token internals must never enter an add-on card or notification.
        raise GmailAddonSharedConnectionUnavailable() from exc


def _valid_gmail_id(value, *, field_label):
    value = str(value or "")
    if (
        not value
        or len(value) > 255
        or any(character.isspace() or ord(character) < 32 for character in value)
        or any(character in value for character in ("/", "\\", "?", "#"))
    ):
        raise GmailAddonInputError(f"The Gmail {field_label} was invalid.")
    return value


def _gmail_context(event):
    gmail = event.get("gmail")
    if not isinstance(gmail, dict):
        raise GmailAddonInputError("Open a Gmail message before using this action.")
    message_id = _valid_gmail_id(gmail.get("messageId"), field_label="message")
    thread_id = _valid_gmail_id(gmail.get("threadId"), field_label="thread")
    return message_id, thread_id


def _safe_card_text(value, *, maximum=120):
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) > maximum:
        return f"{cleaned[: maximum - 3].rstrip()}..."
    return cleaned


def _attachment_filenames(payload):
    filenames = []
    pending = [payload] if isinstance(payload, dict) else []
    inspected_parts = 0
    while pending and len(filenames) < 4 and inspected_parts < 200:
        part = pending.pop(0)
        inspected_parts += 1
        filename = _safe_card_text(part.get("filename"), maximum=55)
        if filename and filename not in filenames:
            filenames.append(filename)
        pending.extend(
            child
            for child in (part.get("parts") or [])
            if isinstance(child, dict)
        )
    return filenames


def _fetch_thread_message_summaries(connection, thread_id):
    """Fetch safe snippets and MIME names, but no body or attachment bytes."""

    token = _shared_access_token(connection)
    params = [
        ("format", "full"),
        (
            "fields",
            (
                "messages(id,internalDate,snippet,"
                "payload(headers(name,value),filename,"
                "parts(filename,parts(filename,parts(filename,parts(filename))))))"
            ),
        ),
    ]
    payload = _json_request(
        (
            f"{GMAIL_API_BASE}/threads/{urllib.parse.quote(thread_id)}"
            f"?{urllib.parse.urlencode(params, doseq=True)}"
        ),
        token=token,
        timeout=10,
    )
    summaries = []
    for message in payload.get("messages") or []:
        try:
            message_id = _valid_gmail_id(
                message.get("id"),
                field_label="thread message",
            )
        except GmailAddonInputError:
            continue
        message_payload = message.get("payload") or {}
        headers = message_payload.get("headers") or []
        sender = _safe_card_text(_header(headers, "From"), maximum=90)
        sender_name, sender_email = parseaddr(sender)
        sender_label = _safe_card_text(sender_name or sender_email or sender, maximum=45)
        subject = _safe_card_text(_header(headers, "Subject") or "(no subject)", maximum=70)
        snippet = _safe_card_text(message.get("snippet"), maximum=85)
        attachment_names = _attachment_filenames(message_payload)
        details = []
        if snippet:
            details.append(snippet)
        if attachment_names:
            attachment_label = ", ".join(attachment_names[:3])
            if len(attachment_names) > 3:
                attachment_label += f" (+{len(attachment_names) - 3})"
            details.append(f"Attachments: {attachment_label}")
        sent_at = _message_datetime(message)
        when = sent_at.strftime("%d %b %Y, %H:%M") if sent_at else "Date unavailable"
        label = f"{when} | {sender_label or 'Unknown sender'} | {subject}"
        if details:
            label = f"{label} | {' | '.join(details)}"
        summaries.append(
            {
                "message_id": message_id,
                "label": _safe_card_text(label, maximum=240),
            }
        )
    maximum = int(getattr(settings, "GMAIL_ADDON_MAX_THREAD_MESSAGES", 50) or 50)
    return summaries[-maximum:]


def _action_button(text, mode, action_url, *, primary=False):
    button = {
        "text": text,
        "onClick": {
            "action": {
                "function": action_url,
                "parameters": [{"key": "selection_mode", "value": mode}],
                "loadIndicator": "SPINNER",
            }
        },
    }
    if primary:
        button["color"] = {"red": 0.05, "green": 0.55, "blue": 0.50}
    return button


def _contextual_card(*, summaries, anchor_message_id, action_url):
    if not any(item["message_id"] == anchor_message_id for item in summaries):
        summaries.append(
            {
                "message_id": anchor_message_id,
                "label": "Currently open message",
            }
        )
    selection_items = [
        {
            "text": item["label"],
            "value": item["message_id"],
            "selected": item["message_id"] == anchor_message_id,
        }
        for item in summaries
    ]
    card = {
        "name": "quotation-inquiry-intake",
        "header": {
            "title": "Create quotation",
            "subtitle": f"Choose from {len(selection_items)} message(s) in this thread",
        },
        "sections": [
            {
                "widgets": [
                    {
                        "textParagraph": {
                            "text": (
                                "Import the inquiry into Al Ameen. The website will "
                                "open with the company and item rows ready for review."
                            )
                        }
                    },
                    {
                        "selectionInput": {
                            "name": "message_ids",
                            "label": "Messages to include",
                            "type": "CHECK_BOX",
                            "items": selection_items,
                        }
                    },
                    {
                        "buttonList": {
                            "buttons": [
                                _action_button(
                                    "Let AI choose",
                                    MODE_AI_THREAD,
                                    action_url,
                                    primary=True,
                                ),
                                _action_button(
                                    "Import selected",
                                    MODE_SELECTED_MESSAGES,
                                    action_url,
                                ),
                                _action_button(
                                    "Current only",
                                    MODE_CURRENT_MESSAGE,
                                    action_url,
                                ),
                            ]
                        }
                    },
                ]
            }
        ],
    }
    return {
        "renderActions": {
            "action": {
                "navigations": [{"pushCard": card}],
            }
        }
    }


def _shared_gmail_settings_url():
    """Build the quotation settings URL from the trusted handoff origin/path."""

    base_url = _configured_https_url("GMAIL_ADDON_HANDOFF_URL")
    parsed = urllib.parse.urlsplit(base_url)
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(
                [
                    ("admin_tab", "quotations"),
                    ("quotation_tab", "contract-intelligence"),
                ]
            ),
            "",
        )
    )


def _shared_gmail_reconnect_response(*, action_callback=False):
    card = {
        "name": "quotation-shared-gmail-reconnect",
        "header": {
            "title": "Reconnect shared Gmail",
            "subtitle": "Quotation inquiry imports are temporarily paused",
        },
        "sections": [
            {
                "widgets": [
                    {
                        "textParagraph": {
                            "text": (
                                "Shared Gmail must be reconnected in the Al Ameen "
                                "quotation settings before email inquiries can be "
                                "imported."
                            )
                        }
                    },
                    {
                        "buttonList": {
                            "buttons": [
                                {
                                    "text": "Open Gmail settings",
                                    "onClick": {
                                        "openLink": {
                                            "url": _shared_gmail_settings_url(),
                                            "openAs": "FULL_SIZE",
                                            "onClose": "RELOAD_ADD_ON",
                                        }
                                    },
                                }
                            ]
                        }
                    },
                ]
            }
        ],
    }
    action = {
        "navigations": [{"pushCard": card}],
    }
    if action_callback:
        action["notification"] = {
            "text": "Shared Gmail must be reconnected."
        }
    return JsonResponse(
        {
            "renderActions": {
                "action": action,
            }
        }
    )


def _notification_response(text, *, status=200):
    return JsonResponse(
        {
            "renderActions": {
                "action": {
                    "notification": {
                        "text": str(text or "The Gmail add-on action could not be completed.")
                    }
                }
            }
        },
        status=status,
    )


def _selected_message_ids(event):
    common = event.get("commonEventObject")
    if not isinstance(common, dict):
        return []
    form_inputs = common.get("formInputs")
    if not isinstance(form_inputs, dict):
        return []
    message_input = form_inputs.get("message_ids")
    if not isinstance(message_input, dict):
        return []
    string_inputs = message_input.get("stringInputs")
    values = string_inputs.get("value") if isinstance(string_inputs, dict) else []
    if not isinstance(values, list):
        return []
    selected = []
    for value in values:
        message_id = _valid_gmail_id(value, field_label="message selection")
        if message_id not in selected:
            selected.append(message_id)
    if len(selected) > MAX_SELECTED_MESSAGE_IDS:
        raise GmailAddonInputError(
            f"Choose no more than {MAX_SELECTED_MESSAGE_IDS} messages."
        )
    return selected


def _selection_mode(event):
    common = event.get("commonEventObject")
    parameters = common.get("parameters") if isinstance(common, dict) else {}
    mode = str((parameters or {}).get("selection_mode") or "").strip()
    if mode not in ALLOWED_MODES:
        raise GmailAddonInputError("Choose how Gmail should import this inquiry.")
    return mode


def _issue_handoff(connection, **kwargs):
    # Imported lazily so this public callback module never introduces a
    # circular import into the quotation domain service.
    from .gmail_inquiry_import import issue_gmail_inquiry_handoff

    return issue_gmail_inquiry_handoff(connection, **kwargs)


def _build_handoff_url(raw_token):
    raw_token = str(raw_token or "").strip()
    if not raw_token or len(raw_token) > 2048 or any(character.isspace() for character in raw_token):
        raise GmailAddonConfigurationError(
            "The website handoff could not be created. Please try again."
        )
    base_url = _configured_https_url("GMAIL_ADDON_HANDOFF_URL")
    parsed = urllib.parse.urlsplit(base_url)
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if key != "gmail_import"
    ]
    query.append(("gmail_import", raw_token))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _error_response(error):
    if isinstance(error, GmailAddonError):
        status_code = error.status_code
        message = error.public_message
        if message == "Not found.":
            return JsonResponse({"detail": message}, status=404)
    else:
        status_code = 503
        message = "The quotation Gmail add-on is temporarily unavailable."
    return JsonResponse({"detail": message}, status=status_code)


@csrf_exempt
@require_POST
def gmail_addon_contextual(request):
    try:
        config = _require_configuration()
        event = _parse_event(request)
        authorization_event = _authenticate_system_event(
            request,
            event,
            config,
            expected_audience=config["contextual_url"],
        )
        missing_scopes = _missing_required_google_scopes(authorization_event)
        if missing_scopes:
            return _requesting_google_scopes_response(missing_scopes)
        _authenticate_user_event(authorization_event, config)
        anchor_message_id, thread_id = _gmail_context(event)
        try:
            connection = _shared_connection(config["mailbox_email"])
            summaries = _fetch_thread_message_summaries(connection, thread_id)
        except GmailAddonSharedConnectionUnavailable:
            return _shared_gmail_reconnect_response()
        return JsonResponse(
            _contextual_card(
                summaries=summaries,
                anchor_message_id=anchor_message_id,
                action_url=config["action_url"],
            )
        )
    except GmailAddonError as exc:
        return _error_response(exc)
    except Exception:
        # Do not expose or log event contents, Gmail identifiers, or tokens.
        return _error_response(GmailAddonConfigurationError(
            "The quotation Gmail add-on is temporarily unavailable."
        ))


@csrf_exempt
@require_POST
def gmail_addon_action(request):
    try:
        config = _require_configuration()
        event = _parse_event(request)
        authorization_event = _authenticate_system_event(
            request,
            event,
            config,
            expected_audience=config["action_url"],
        )
        missing_scopes = _missing_required_google_scopes(authorization_event)
        if missing_scopes:
            return _requesting_google_scopes_response(missing_scopes)
        _authenticate_user_event(authorization_event, config)
        anchor_message_id, thread_id = _gmail_context(event)
        mode = _selection_mode(event)
        selected_message_ids = (
            _selected_message_ids(event) if mode == MODE_SELECTED_MESSAGES else []
        )
        if mode == MODE_SELECTED_MESSAGES and not selected_message_ids:
            return _notification_response(
                "Select at least one message, then choose Import selected."
            )

        try:
            connection = _shared_connection(config["mailbox_email"])
            _shared_access_token(connection)
            if mode == MODE_SELECTED_MESSAGES:
                thread_message_ids = {
                    item["message_id"]
                    for item in _fetch_thread_message_summaries(connection, thread_id)
                }
                if any(
                    message_id not in thread_message_ids
                    for message_id in selected_message_ids
                ):
                    return _notification_response(
                        "The thread changed. Reopen the add-on and select the "
                        "messages again."
                    )
        except GmailAddonSharedConnectionUnavailable:
            return _shared_gmail_reconnect_response(action_callback=True)
        _import_record, raw_token = _issue_handoff(
            connection,
            anchor_message_id=anchor_message_id,
            gmail_thread_id=thread_id,
            mode=mode,
            selected_message_ids=selected_message_ids,
            ttl_seconds=int(
                getattr(settings, "GMAIL_ADDON_HANDOFF_TTL_SECONDS", 1800) or 1800
            ),
        )
        handoff_url = _build_handoff_url(raw_token)
        return JsonResponse(
            {
                "stateChanged": True,
                "renderActions": {
                    "action": {
                        "link": {
                            "url": handoff_url,
                            "openAs": "FULL_SIZE",
                            "onClose": "NOTHING",
                        },
                        "notification": {
                            "text": "Opening the quotation inquiry workspace..."
                        },
                    }
                },
            }
        )
    except GmailAddonInputError as exc:
        return _notification_response(exc.public_message)
    except GmailAddonError as exc:
        return _error_response(exc)
    except Exception:
        # Keep service/parser details and all Gmail context out of the response.
        return _error_response(GmailAddonConfigurationError(
            "The quotation Gmail add-on is temporarily unavailable."
        ))
