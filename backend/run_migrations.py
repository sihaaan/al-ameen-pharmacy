#!/usr/bin/env python
"""
Smart migration runner for Railway deployment.
Handles the v1 -> v2 migration state properly.
"""
import os
import sys
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pharmacy_api.settings')
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
django.setup()

from django.db import connection
from django.core.management import call_command


def check_migration_state():
    """Check if we need to fix the migration state."""
    with connection.cursor() as cursor:
        # Check if django_migrations table exists
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'django_migrations'
            )
        """)
        has_migrations_table = cursor.fetchone()[0]

        if not has_migrations_table:
            print("Fresh database - running normal migrations")
            return "fresh"

        # Check what api migrations are recorded
        cursor.execute("SELECT name FROM django_migrations WHERE app = 'api' ORDER BY name")
        api_migrations = [m[0] for m in cursor.fetchall()]
        print(f"Recorded api migrations: {api_migrations}")

        # Check if api_category table exists (legacy table)
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'api_category'
            )
        """)
        has_legacy_tables = cursor.fetchone()[0]

        # Check if api_brand table exists (v2 table)
        cursor.execute("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables
                WHERE table_name = 'api_brand'
            )
        """)
        has_v2_tables = cursor.fetchone()[0]

        print(f"Has legacy tables (api_category): {has_legacy_tables}")
        print(f"Has v2 tables (api_brand): {has_v2_tables}")

        if '0001_initial_legacy' in api_migrations:
            print("Migration state looks correct - running normal migrations")
            return "normal"

        if has_legacy_tables and not has_v2_tables:
            print("Legacy tables exist but migrations not recorded - need to fix state")
            return "fix_state"

        return "normal"


def fix_migration_state():
    """Fix the migration state for v1 -> v2 upgrade."""
    with connection.cursor() as cursor:
        # Clear any old api migrations
        cursor.execute("DELETE FROM django_migrations WHERE app = 'api'")
        print("Cleared old api migrations")

        # Fake-apply 0001_initial_legacy since tables already exist
        cursor.execute(
            "INSERT INTO django_migrations (app, name, applied) VALUES (%s, %s, NOW())",
            ['api', '0001_initial_legacy']
        )
        print("Fake-applied 0001_initial_legacy")


def run_migrations():
    """Main migration runner."""
    print("=" * 50)
    print("Smart Migration Runner")
    print("=" * 50)

    state = check_migration_state()

    if state == "fix_state":
        fix_migration_state()

    # Run migrations
    print("\nRunning Django migrations...")
    call_command('migrate', '--noinput')
    print("\nMigrations complete!")


if __name__ == '__main__':
    run_migrations()
