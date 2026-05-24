#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys
from urllib.parse import urlparse

from dotenv import load_dotenv


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _guard_debug_neon_database():
    load_dotenv()
    if not _env_bool("DEBUG", True):
        return
    database_url = os.environ.get("DATABASE_URL", "")
    host = (urlparse(database_url).hostname or "").lower()
    if not host:
        return
    production_hosts = {
        item.strip().lower()
        for item in os.environ.get("PRODUCTION_DATABASE_HOSTS", "").split(",")
        if item.strip()
    }
    is_configured_production_host = host in production_hosts
    is_neon_host = host.endswith(".neon.tech")
    if is_configured_production_host or (is_neon_host and not _env_bool("ALLOW_DEBUG_NEON_DATABASE", False)):
        raise RuntimeError(
            "Refusing to start with DEBUG=True while DATABASE_URL points to a Neon database host. "
            "Use SQLite, local PostgreSQL, or a separate Neon dev branch for local work. "
            "Do not point local backend/.env at production Neon."
        )


def main():
    """Run administrative tasks."""
    _guard_debug_neon_database()
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pharmacy_api.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
