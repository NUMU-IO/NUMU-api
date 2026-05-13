"""Regression test for the Step 04 storefront indexes.

Asserts the three indexes from migration ``loadtest_idx_20260513``
are present after migrations have been applied. If someone later
drops one without a paired downgrade migration, this test fails.

Requires a real Postgres reachable via ``TEST_DATABASE_URL`` (or
``DATABASE_URL`` as fallback) with the Alembic chain up to head
applied. ``pytest.skip``s cleanly when no Postgres URL is set or the
server isn't reachable.

Implementation note: this test is intentionally a *synchronous*
function that drives ``asyncpg`` via its own private event loop.
The project's main ``conftest.py`` opens a session-scoped asyncpg
pool; combined with ``pytest-asyncio`` creating per-test loops, the
SQLAlchemy-async approach hits "attached to a different loop" on
Windows. The private-loop pattern stays out of that interaction.
"""

from __future__ import annotations

import asyncio
import os
import re

import pytest

EXPECTED_INDEXES: set[str] = {
    "ix_products_store_active_created",
    "ix_products_store_slug_active",
    "ix_categories_store_parent_active",
}


def _parse_postgres_url() -> dict[str, object] | None:
    """Extract asyncpg.connect kwargs from a SQLAlchemy-style URL.

    Accepts both ``postgresql://`` and ``postgresql+asyncpg://`` (and
    other ``+driver`` variants). Returns ``None`` if no URL is set or
    the URL can't be parsed.
    """
    url = os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")
    if not url:
        return None
    cleaned = re.sub(r"^postgresql\+\w+://", "postgresql://", url)
    m = re.match(
        r"postgresql://([^:]+):([^@]+)@([^:/]+):?(\d+)?/([^?]+)",
        cleaned,
    )
    if not m:
        return None
    user, password, host, port, dbname = m.groups()
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": int(port) if port else 5432,
        "database": dbname,
    }


async def _fetch_present_indexes(params: dict[str, object]) -> set[str]:
    import asyncpg

    conn = await asyncpg.connect(**params)
    try:
        rows = await conn.fetch(
            "SELECT indexname FROM pg_indexes "
            "WHERE schemaname = 'public' AND indexname = ANY($1::text[])",
            list(EXPECTED_INDEXES),
        )
        return {r["indexname"] for r in rows}
    finally:
        await conn.close()


def test_storefront_indexes_present() -> None:
    params = _parse_postgres_url()
    if not params:
        pytest.skip("TEST_DATABASE_URL/DATABASE_URL not set to a Postgres URL")

    loop = asyncio.new_event_loop()
    try:
        try:
            found = loop.run_until_complete(_fetch_present_indexes(params))
        except OSError as exc:
            pytest.skip(f"Postgres not reachable: {exc}")
    finally:
        loop.close()

    missing = EXPECTED_INDEXES - found
    assert not missing, f"missing indexes: {missing}"
