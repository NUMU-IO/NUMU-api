"""Verify Row-Level Security (RLS) policies on tenant-scoped tables.

Usage:
    python scripts/verify_rls.py

Checks that RLS is enabled and policies exist for all required tables.
Exit code 0 = all checks pass, 1 = one or more checks failed.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine, text

from src.config import settings


def _get_sync_url() -> str:
    """Build a sync database URL, trying psycopg2 then pg8000 as fallback."""
    base = settings.database_url_sync  # postgresql://...
    try:
        import psycopg2  # noqa: F401

        return base
    except ImportError:
        pass
    try:
        import pg8000  # noqa: F401

        return base.replace("postgresql://", "postgresql+pg8000://", 1)
    except ImportError:
        pass
    # Last resort: use asyncpg synchronously is not possible,
    # so just return the base URL and let SQLAlchemy raise a clear error
    return base


# Tables that must have RLS enabled (matches the migration)
REQUIRED_TABLES = [
    "stores",
    "products",
    "orders",
    "customers",
    "invoices",
    "coupons",
    "customer_addresses",
]


def verify_rls() -> bool:
    """Verify RLS is enabled and policies exist for all required tables."""
    engine = create_engine(_get_sync_url())
    all_passed = True

    print("=" * 72)
    print("  NUMU - Row-Level Security (RLS) Verification")
    print("=" * 72)
    print()

    # Header
    print(
        f"{'Table':<22} {'RLS Enabled':<14} {'Force RLS':<12} {'Policies':<10} {'Result'}"
    )
    print("-" * 72)

    with engine.connect() as conn:
        for table_name in REQUIRED_TABLES:
            # Check RLS flags
            result = conn.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class "
                    "WHERE relname = :tbl AND relnamespace = 'public'::regnamespace"
                ),
                {"tbl": table_name},
            ).fetchone()

            if result is None:
                print(
                    f"{table_name:<22} {'N/A':<14} {'N/A':<12} {'N/A':<10} FAIL (table not found)"
                )
                all_passed = False
                continue

            rls_enabled = result[0]
            force_rls = result[1]

            # Check policies
            policies = conn.execute(
                text(
                    "SELECT polname "
                    "FROM pg_policy "
                    "WHERE polrelid = ('public.' || :tbl)::regclass"
                ),
                {"tbl": table_name},
            ).fetchall()

            policy_count = len(policies)
            passed = rls_enabled and policy_count >= 1

            status = "PASS" if passed else "FAIL"
            if not passed:
                all_passed = False

            rls_str = "Yes" if rls_enabled else "No"
            force_str = "Yes" if force_rls else "No"

            print(
                f"{table_name:<22} {rls_str:<14} {force_str:<12} {policy_count:<10} {status}"
            )

    print("-" * 72)
    print()

    if all_passed:
        print("ALL CHECKS PASSED")
    else:
        print("SOME CHECKS FAILED - review output above")

    print()

    # Detailed policy listing
    print("=" * 72)
    print("  Detailed Policy Listing")
    print("=" * 72)
    print()

    with engine.connect() as conn:
        for table_name in REQUIRED_TABLES:
            try:
                policies = conn.execute(
                    text(
                        "SELECT polname, "
                        "CASE polcmd "
                        "  WHEN 'r' THEN 'SELECT' "
                        "  WHEN 'a' THEN 'INSERT' "
                        "  WHEN 'w' THEN 'UPDATE' "
                        "  WHEN 'd' THEN 'DELETE' "
                        "  WHEN '*' THEN 'ALL' "
                        "END as command "
                        "FROM pg_policy "
                        "WHERE polrelid = ('public.' || :tbl)::regclass "
                        "ORDER BY polname"
                    ),
                    {"tbl": table_name},
                ).fetchall()

                print(f"  {table_name}:")
                if policies:
                    for pol_name, pol_cmd in policies:
                        print(f"    - {pol_name} ({pol_cmd})")
                else:
                    print("    (no policies found)")
                print()
            except Exception as e:
                print(f"  {table_name}: ERROR - {e}")
                print()

    engine.dispose()
    return all_passed


if __name__ == "__main__":
    success = verify_rls()
    sys.exit(0 if success else 1)
