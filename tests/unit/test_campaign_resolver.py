"""Unit tests for campaign_resolver — short_code extraction + tenant scoping.

The pure-helper ``_extract_short_code`` is tested directly. The
``resolve_campaign_id`` function is tested against a mock AsyncSession
to verify the query is correctly scoped by ``(store_id, short_code)``
— critical for SEC-001 / SEC-006 (cross-tenant isolation).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from src.application.services.campaign_resolver import (
    _extract_short_code,
    resolve_campaign_id,
)

# ── _extract_short_code ─────────────────────────────────────────────


def test_extract_returns_uppercase_code():
    assert _extract_short_code("eid-sale-2026-AB7K9X") == "AB7K9X"


def test_extract_uppercases_lowercase_codes():
    assert _extract_short_code("eid-sale-ab7k9x") == "AB7K9X"


def test_extract_handles_short_slug():
    assert _extract_short_code("x-AB7K9X") == "AB7K9X"


def test_extract_returns_none_when_no_suffix():
    assert _extract_short_code("eid-sale-2026") is None
    assert _extract_short_code("no-code-here") is None


def test_extract_returns_none_for_wrong_length_suffix():
    """7-char or 5-char tails don't match the strict 6-char pattern."""
    assert _extract_short_code("eid-sale-AB7K9XY") is None
    assert _extract_short_code("eid-sale-AB7K9") is None


def test_extract_rejects_excluded_alphabet_chars():
    """Crockford alphabet excludes I, L, O, U — a code containing one
    of these is not a real short_code (would never be generated)."""
    assert _extract_short_code("camp-ILOUXY") is None
    assert _extract_short_code("camp-AAAOAA") is None
    assert _extract_short_code("camp-AAAAUU") is None


def test_extract_rejects_special_chars():
    assert _extract_short_code("camp-AB7K9!") is None
    assert _extract_short_code("camp-AB7K9.") is None


def test_extract_handles_empty_input():
    assert _extract_short_code("") is None


# ── resolve_campaign_id — DB-aware path ─────────────────────────────


def _mock_session(returns: UUID | None) -> AsyncMock:
    """Return an AsyncMock session whose execute() yields scalar_one_or_none()=returns."""
    session = AsyncMock()

    async def execute(_stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = returns
        return result

    session.execute = execute
    return session


@pytest.mark.asyncio
async def test_resolve_returns_uuid_on_match():
    expected = uuid4()
    session = _mock_session(returns=expected)
    out = await resolve_campaign_id(
        session=session,
        store_id=uuid4(),
        utm_campaign="eid-sale-2026-AB7K9X",
    )
    assert out == expected


@pytest.mark.asyncio
async def test_resolve_returns_none_for_unknown_short_code():
    """No row matches → None, but the resolver still doesn't error."""
    session = _mock_session(returns=None)
    out = await resolve_campaign_id(
        session=session,
        store_id=uuid4(),
        utm_campaign="eid-sale-2026-AB7K9X",
    )
    assert out is None


@pytest.mark.asyncio
async def test_resolve_returns_none_for_utm_without_suffix():
    """No short_code suffix → no DB query at all → None."""
    session = AsyncMock()
    out = await resolve_campaign_id(
        session=session,
        store_id=uuid4(),
        utm_campaign="organic_share",
    )
    assert out is None
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_returns_none_for_empty_utm():
    session = AsyncMock()
    out = await resolve_campaign_id(
        session=session,
        store_id=uuid4(),
        utm_campaign="",
    )
    assert out is None
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_returns_none_for_none_utm():
    session = AsyncMock()
    out = await resolve_campaign_id(
        session=session,
        store_id=uuid4(),
        utm_campaign=None,
    )
    assert out is None
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_query_includes_store_id_filter():
    """Critical SEC-001 / SEC-006 check: the SQL must filter by BOTH
    store_id AND short_code, otherwise a cross-tenant probe with a
    known short_code from another store could leak campaign_id."""
    store_id = uuid4()
    expected = uuid4()
    captured_stmt = []

    async def execute(stmt):
        captured_stmt.append(stmt)
        result = MagicMock()
        result.scalar_one_or_none.return_value = expected
        return result

    session = AsyncMock()
    session.execute = execute

    await resolve_campaign_id(
        session=session,
        store_id=store_id,
        utm_campaign="eid-sale-AB7K9X",
    )

    # Inspect the compiled SQL — must reference both store_id and short_code.
    assert len(captured_stmt) == 1
    compiled = str(captured_stmt[0].compile(compile_kwargs={"literal_binds": False}))
    assert "store_id" in compiled, (
        "resolver must filter by store_id — cross-tenant safety!"
    )
    assert "short_code" in compiled, (
        "resolver must filter by short_code — otherwise lookup is meaningless"
    )
