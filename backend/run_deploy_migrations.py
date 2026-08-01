#!/usr/bin/env python
"""Validate and run Railway pre-deploy migrations on a direct PostgreSQL URL.

The web process may use a transaction-pooling URL. Schema migrations must use
the separately configured direct URL so PostgreSQL session settings and Django
migration transactions have stable connection semantics.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import parse_qs, unquote, urlsplit

import psycopg


BACKEND_ROOT = Path(__file__).resolve().parent
POSTGRESQL_SCHEMES = {"postgres", "postgresql"}
TARGET_OVERRIDE_QUERY_KEYS = {
    "database",
    "connect_timeout",
    "dbname",
    "host",
    "hostaddr",
    "options",
    "port",
    "service",
    "servicefile",
}
DEFAULT_LOCK_TIMEOUT_MS = 10_000
DEFAULT_STATEMENT_TIMEOUT_MS = 900_000
DEFAULT_CONNECT_TIMEOUT_SECONDS = 8
MAX_LOCK_TIMEOUT_MS = 300_000
MAX_STATEMENT_TIMEOUT_MS = 3_600_000
MAX_CONNECT_TIMEOUT_SECONDS = 60
MIGRATION_ADVISORY_LOCK_ID = 0x414C414D45454E


class MigrationConfigurationError(RuntimeError):
    """Raised when migration database safety checks fail."""


@dataclass(frozen=True)
class DatabaseTarget:
    host: str
    port: int
    database: str


@dataclass(frozen=True)
class MigrationConfiguration:
    database_url: str
    connect_timeout_seconds: int
    lock_timeout_ms: int
    statement_timeout_ms: int


def _looks_like_pooler(host: str, query: str) -> bool:
    labels = host.split(".")
    if any(
        label == "pooler"
        or label.startswith("pooler-")
        or label.endswith("-pooler")
        for label in labels
    ):
        return True

    query_keys = {key.lower() for key in parse_qs(query, keep_blank_values=True)}
    return bool(query_keys & {"pgbouncer", "pool_mode"})


def _host_lineage(host: str) -> str:
    """Return the direct-host identity for a supported pooled-host spelling."""

    labels = host.split(".")
    if labels:
        labels[0] = re.sub(r"-pooler$", "", labels[0])
    return ".".join(labels)


def _is_local_host(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _canonical_host(value: str | None, *, variable_name: str) -> str:
    raw_host = value or ""
    if re.search(r"%(?![0-9A-Fa-f]{2})", raw_host):
        raise MigrationConfigurationError(
            f"{variable_name} must identify one PostgreSQL TCP host."
        )

    host = unquote(raw_host).lower().rstrip(".")
    if (
        not host
        or any(ord(character) < 33 or ord(character) > 126 for character in host)
        or any(character in host for character in ("/", "\\", "@", ",", "\x00"))
    ):
        raise MigrationConfigurationError(
            f"{variable_name} must identify one PostgreSQL TCP host."
        )

    try:
        ipaddress.ip_address(host)
    except ValueError:
        labels = host.split(".")
        if (
            len(host) > 253
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not re.fullmatch(r"[a-z0-9-]+", label)
                for label in labels
            )
        ):
            raise MigrationConfigurationError(
                f"{variable_name} must identify one PostgreSQL TCP host."
            ) from None
    return host


def _parse_database_target(
    value: str | None,
    *,
    variable_name: str,
    require_direct: bool,
) -> DatabaseTarget:
    if not value:
        raise MigrationConfigurationError(f"{variable_name} is required.")

    try:
        parsed = urlsplit(value)
        # Accessing port performs urllib's range and integer validation.
        parsed.port
    except (TypeError, ValueError):
        raise MigrationConfigurationError(
            f"{variable_name} must be a valid PostgreSQL URL."
        ) from None

    if parsed.scheme.lower() not in POSTGRESQL_SCHEMES:
        raise MigrationConfigurationError(
            f"{variable_name} must use PostgreSQL."
        )

    host = _canonical_host(parsed.hostname, variable_name=variable_name)
    database = unquote(parsed.path.removeprefix("/"))
    if (
        not database
        or "/" in database
        or parsed.fragment
    ):
        raise MigrationConfigurationError(
            f"{variable_name} must identify one PostgreSQL host and database."
        )

    query_values = parse_qs(parsed.query, keep_blank_values=True)
    query_keys = {key.lower() for key in query_values}
    if query_keys & TARGET_OVERRIDE_QUERY_KEYS:
        raise MigrationConfigurationError(
            f"{variable_name} must not override guarded connection settings in query options."
        )

    if require_direct and _looks_like_pooler(host, parsed.query):
        raise MigrationConfigurationError(
            "MIGRATION_DATABASE_URL must use a direct, unpooled PostgreSQL host."
        )

    if require_direct and not _is_local_host(host):
        sslmode_values = [
            value.strip().lower()
            for key, values in query_values.items()
            if key.lower() == "sslmode"
            for value in values
        ]
        if len(sslmode_values) != 1 or sslmode_values[0] not in {
            "require",
            "verify-ca",
            "verify-full",
        }:
            raise MigrationConfigurationError(
                "MIGRATION_DATABASE_URL must require TLS for a non-local host."
            )

    return DatabaseTarget(host=host, port=parsed.port or 5432, database=database)


def _read_positive_integer(
    environment: dict[str, str],
    name: str,
    *,
    default: int,
    maximum: int,
    unit: str,
) -> int:
    raw_value = environment.get(name)
    if raw_value is None:
        return default

    value = raw_value.strip()
    if not re.fullmatch(r"[1-9][0-9]*", value):
        raise MigrationConfigurationError(
            f"{name} must be a positive integer number of {unit}."
        )

    parsed_value = int(value)
    if parsed_value > maximum:
        raise MigrationConfigurationError(
            f"{name} exceeds the allowed migration safety bound."
        )
    return parsed_value


def load_migration_configuration(
    environment: dict[str, str] | None = None,
) -> MigrationConfiguration:
    environment = os.environ if environment is None else environment
    application_url = environment.get("DATABASE_URL")
    migration_url = environment.get("MIGRATION_DATABASE_URL")

    application_target = _parse_database_target(
        application_url,
        variable_name="DATABASE_URL",
        require_direct=False,
    )
    migration_target = _parse_database_target(
        migration_url,
        variable_name="MIGRATION_DATABASE_URL",
        require_direct=True,
    )

    if application_target.database != migration_target.database:
        raise MigrationConfigurationError(
            "MIGRATION_DATABASE_URL must identify the same database as DATABASE_URL."
        )
    if application_target.port != migration_target.port:
        raise MigrationConfigurationError(
            "MIGRATION_DATABASE_URL must use the same PostgreSQL port as DATABASE_URL."
        )
    if _host_lineage(application_target.host) != _host_lineage(
        migration_target.host
    ):
        raise MigrationConfigurationError(
            "MIGRATION_DATABASE_URL must share the DATABASE_URL host lineage."
        )

    connect_timeout_seconds = _read_positive_integer(
        environment,
        "MIGRATION_CONNECT_TIMEOUT_SECONDS",
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        maximum=MAX_CONNECT_TIMEOUT_SECONDS,
        unit="seconds",
    )
    lock_timeout_ms = _read_positive_integer(
        environment,
        "MIGRATION_LOCK_TIMEOUT_MS",
        default=DEFAULT_LOCK_TIMEOUT_MS,
        maximum=MAX_LOCK_TIMEOUT_MS,
        unit="milliseconds",
    )
    statement_timeout_ms = _read_positive_integer(
        environment,
        "MIGRATION_STATEMENT_TIMEOUT_MS",
        default=DEFAULT_STATEMENT_TIMEOUT_MS,
        maximum=MAX_STATEMENT_TIMEOUT_MS,
        unit="milliseconds",
    )
    if statement_timeout_ms < lock_timeout_ms:
        raise MigrationConfigurationError(
            "MIGRATION_STATEMENT_TIMEOUT_MS must be at least the lock timeout."
        )

    return MigrationConfiguration(
        database_url=migration_url,
        connect_timeout_seconds=connect_timeout_seconds,
        lock_timeout_ms=lock_timeout_ms,
        statement_timeout_ms=statement_timeout_ms,
    )


@contextmanager
def _without_libpq_environment():
    saved = {
        name: value
        for name, value in os.environ.items()
        if name.upper().startswith("PG")
    }
    for name in saved:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name in tuple(os.environ):
            if name.upper().startswith("PG"):
                os.environ.pop(name, None)
        os.environ.update(saved)


@contextmanager
def migration_advisory_lock(configuration: MigrationConfiguration):
    """Serialize every guarded migration invocation targeting this cluster."""

    options = (
        f"-c lock_timeout={configuration.lock_timeout_ms}ms "
        f"-c statement_timeout={configuration.statement_timeout_ms}ms"
    )
    with _without_libpq_environment():
        with psycopg.connect(
            configuration.database_url,
            autocommit=True,
            connect_timeout=configuration.connect_timeout_seconds,
            options=options,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_lock(%s)",
                    (MIGRATION_ADVISORY_LOCK_ID,),
                )
            # The session owns the lock until the child finishes and this
            # connection closes, including when the child exits nonzero.
            yield


def run_migrations(
    configuration: MigrationConfiguration,
    environment: dict[str, str] | None = None,
) -> int:
    child_environment = dict(os.environ if environment is None else environment)
    # libpq environment variables can override a validated URI (for example,
    # PGHOSTADDR redirects the physical server while preserving the URI host).
    # Remove the entire namespace so the guarded URL is the sole target and
    # then add only the migration-scoped options controlled below.
    for name in tuple(child_environment):
        if name.upper().startswith("PG"):
            child_environment.pop(name, None)
    child_environment["DATABASE_URL"] = configuration.database_url
    child_environment.pop("MIGRATION_DATABASE_URL", None)
    child_environment["DJANGO_SETTINGS_MODULE"] = "pharmacy_api.settings"
    child_environment["DATABASE_CONN_MAX_AGE_SECONDS"] = "0"
    child_environment["DATABASE_CONNECT_TIMEOUT_SECONDS"] = str(
        configuration.connect_timeout_seconds
    )
    child_environment["PGOPTIONS"] = (
        f"-c lock_timeout={configuration.lock_timeout_ms}ms "
        f"-c statement_timeout={configuration.statement_timeout_ms}ms"
    )

    try:
        with migration_advisory_lock(configuration):
            completed = subprocess.run(
                [sys.executable, "manage.py", "migrate", "--noinput"],
                cwd=BACKEND_ROOT,
                env=child_environment,
                check=False,
            )
    except psycopg.Error:
        print(
            "Unable to establish or lock the guarded PostgreSQL migration connection.",
            file=sys.stderr,
        )
        return 1
    except OSError:
        print("Unable to start the Django migration command.", file=sys.stderr)
        return 1
    return completed.returncode


def main(environment: dict[str, str] | None = None) -> int:
    environment = dict(os.environ if environment is None else environment)
    try:
        configuration = load_migration_configuration(environment)
    except MigrationConfigurationError as exc:
        print(f"Migration configuration error: {exc}", file=sys.stderr)
        return 2

    print("Validated direct migration database; running Django migrations.")
    return_code = run_migrations(configuration, environment)
    if return_code:
        print("Guarded Django migration step failed.", file=sys.stderr)
        return return_code
    print("Django migrations completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
