"""Fail-closed email identities used only for sender/company matching.

These helpers deliberately do not rewrite stored addresses or addresses used
for delivery.  They provide a stable comparison form for untrusted mailbox
headers and saved company/contact addresses.
"""

from __future__ import annotations

import ipaddress
import unicodedata
from email.utils import getaddresses
from typing import Sequence

import idna


# Exact, maintained provider domains are safer than treating every domain with
# a familiar-looking first label as public.  Subdomains of these domains are
# also public; lookalikes such as ``gmail.com.attacker.example`` are not.
_PUBLIC_EMAIL_DOMAINS = frozenset(
    {
        "aim.com",
        "aol.com",
        "fastmail.com",
        "gmail.com",
        "gmx.com",
        "gmx.net",
        "googlemail.com",
        "hey.com",
        "hotmail.ae",
        "hotmail.co.uk",
        "hotmail.com",
        "hotmail.de",
        "hotmail.fr",
        "hotmail.it",
        "icloud.com",
        "inbox.com",
        "live.ae",
        "live.co.uk",
        "live.com",
        "live.com.au",
        "live.fr",
        "mac.com",
        "mail.com",
        "mail.ru",
        "me.com",
        "msn.com",
        "outlook.ae",
        "outlook.com",
        "outlook.com.au",
        "outlook.de",
        "outlook.fr",
        "outlook.in",
        "outlook.jp",
        "pm.me",
        "proton.me",
        "protonmail.ch",
        "protonmail.com",
        "rediffmail.com",
        "rocketmail.com",
        "tuta.com",
        "tutanota.com",
        "yahoo.ae",
        "yahoo.co.in",
        "yahoo.co.jp",
        "yahoo.co.nz",
        "yahoo.co.uk",
        "yahoo.com",
        "yahoo.com.au",
        "yahoo.com.br",
        "yahoo.com.hk",
        "yahoo.com.mx",
        "yahoo.com.sg",
        "yahoo.de",
        "yahoo.es",
        "yahoo.fr",
        "yahoo.it",
        "yandex.com",
        "yandex.ru",
        "ymail.com",
        "zoho.com",
    }
)

_INVALID_LOCAL_CHARACTERS = frozenset('"(),:;<>@[\\]')


def canonicalize_email_domain(value: object) -> str:
    """Return a strict lowercase ASCII comparison form for a DNS domain.

    UTS #46 mapping is followed by non-transitional IDNA 2008 processing.  A
    single terminal DNS root dot is accepted and removed.  Invalid domains,
    IP literals and single-label names fail closed by returning ``""``.
    """

    domain = str(value or "").strip()
    if not domain:
        return ""
    try:
        canonical = idna.encode(
            domain,
            uts46=True,
            std3_rules=True,
            transitional=False,
        ).decode("ascii").lower()
    except (idna.IDNAError, UnicodeError):
        return ""

    if canonical.endswith("."):
        canonical = canonical[:-1]
    if not canonical or canonical.endswith(".") or "." not in canonical:
        return ""
    if len(canonical) > 253:
        return ""

    labels = canonical.split(".")
    if any(not label or len(label) > 63 for label in labels):
        return ""
    # IDNA accepts dotted decimal text because each numeric component is a
    # valid DNS label.  It must not become company-domain evidence.
    if all(label.isdigit() for label in labels):
        return ""
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        pass
    else:
        return ""
    return canonical


def canonicalize_email_address(value: object) -> str:
    """Return an address comparison key without applying provider aliases.

    Local-part case follows the matcher's existing case-insensitive behavior,
    but dots and ``+tags`` are preserved.  In particular, ``a+b@example.com``
    never becomes an exact match for ``a@example.com``.
    """

    address = str(value or "").strip()
    if address.count("@") != 1:
        return ""
    local, domain = address.rsplit("@", 1)
    local = unicodedata.normalize("NFC", local).lower()
    if (
        not local
        or local.startswith(".")
        or local.endswith(".")
        or ".." in local
        or any(
            character.isspace()
            or ord(character) < 32
            or ord(character) == 127
            or character in _INVALID_LOCAL_CHARACTERS
            for character in local
        )
    ):
        return ""
    try:
        if len(local.encode("utf-8")) > 64:
            return ""
    except UnicodeError:
        return ""

    canonical_domain = canonicalize_email_domain(domain)
    if not canonical_domain:
        return ""
    canonical = f"{local}@{canonical_domain}"
    if len(canonical.encode("utf-8")) > 254:
        return ""
    return canonical


def canonical_email_addresses(value: str | Sequence[str]) -> frozenset[str]:
    """Extract and canonicalize all valid addresses in one or more headers."""

    values = [value] if isinstance(value, str) else [str(item) for item in value]
    addresses = {
        canonical
        for _name, address in getaddresses(values)
        if (canonical := canonicalize_email_address(address))
    }
    return frozenset(addresses)


def canonical_singleton_header_address(
    value: object = "",
    *,
    header_values: Sequence[str] | None = None,
) -> str:
    """Return one canonical address from exactly one physical header field.

    ``value`` is the compatibility path for callers that only have the raw
    value of one physical field. When the original header list is available,
    callers must pass every value so duplicate fields cannot be hidden by a
    first-value projection.

    A valid identity requires exactly one physical field and exactly one
    parsed address token.  Duplicate addresses remain ambiguous even when
    they canonicalize to the same value, and a malformed token beside a valid
    address invalidates the whole field.
    """

    values = (
        (str(value or ""),)
        if header_values is None
        else tuple(str(item or "") for item in header_values)
    )
    if len(values) != 1 or not values[0].strip():
        return ""
    parsed = getaddresses([values[0]])
    if len(parsed) != 1:
        return ""
    _display_name, address = parsed[0]
    return canonicalize_email_address(address)


def canonical_singleton_from_address(
    sender: object = "",
    *,
    from_header_values: Sequence[str] | None = None,
) -> str:
    """Return one trustworthy physical ``From`` identity, or fail closed."""

    return canonical_singleton_header_address(
        sender,
        header_values=from_header_values,
    )


def is_public_email_domain(value: object) -> bool:
    """Return whether a canonical domain belongs to a public-mail provider."""

    domain = canonicalize_email_domain(value)
    if not domain:
        return False
    return any(
        domain == public_domain or domain.endswith(f".{public_domain}")
        for public_domain in _PUBLIC_EMAIL_DOMAINS
    )


def is_private_email_domain(value: object) -> bool:
    """Return whether a valid domain is safe as private-company evidence."""

    domain = canonicalize_email_domain(value)
    return bool(domain and not is_public_email_domain(domain))
