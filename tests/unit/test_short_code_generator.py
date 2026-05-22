"""Unit tests for short_code_generator (feature 001).

The DB-touching ``generate()`` is exercised against a mock session
returning hits/misses on demand. The pure ``generate_unchecked()`` is
exercised for alphabet, length, and uniformity properties.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.services.short_code_generator import (
    ShortCodeGenerationError,
    generate,
    generate_unchecked,
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


# ── Pure properties ─────────────────────────────────────────────────


def test_generate_unchecked_is_six_chars():
    for _ in range(50):
        code = generate_unchecked()
        assert len(code) == 6


def test_generate_unchecked_uses_only_crockford_alphabet():
    for _ in range(200):
        code = generate_unchecked()
        for ch in code:
            assert ch in _CROCKFORD, f"unexpected char {ch!r} in {code}"


def test_generate_unchecked_excludes_lookalike_chars():
    """Crockford excludes I, L, O, U — confirm none ever appear."""
    for _ in range(500):
        code = generate_unchecked()
        for forbidden in "ILOU":
            assert forbidden not in code, (
                f"forbidden char {forbidden} appeared in {code}"
            )


def test_generate_unchecked_is_random():
    """Two independent calls should overwhelmingly disagree.

    Probabilistically, the chance of two consecutive 6-char draws
    being equal is 1 in 32^6 ≈ 10⁻⁹. Generating 100 codes and
    checking they're not all identical is a sanity check that the
    RNG isn't seeded with a constant.
    """
    codes = {generate_unchecked() for _ in range(100)}
    assert len(codes) > 90, "RNG appears degenerate — generated only "
    f"{len(codes)} distinct codes in 100 draws"


# ── DB-aware path ───────────────────────────────────────────────────


def _mock_session_with_existence_results(*results: bool) -> AsyncMock:
    """Return an AsyncMock session whose execute() yields each result in turn.

    Each result is the boolean an ``exists()`` query would return — True
    when the candidate code is already taken, False when it's free.
    """
    session = AsyncMock()
    response_queue = list(results)

    async def execute(_stmt):
        out = MagicMock()
        next_val = response_queue.pop(0)
        out.scalar.return_value = next_val
        return out

    session.execute = execute
    return session


@pytest.mark.asyncio
async def test_generate_returns_code_on_first_miss():
    """First candidate doesn't exist → return immediately."""
    session = _mock_session_with_existence_results(False)
    code = await generate(uuid4(), session)
    assert len(code) == 6
    for ch in code:
        assert ch in _CROCKFORD


@pytest.mark.asyncio
async def test_generate_retries_on_collision():
    """First two candidates exist, third is free → returns the third."""
    session = _mock_session_with_existence_results(True, True, False)
    code = await generate(uuid4(), session)
    assert len(code) == 6


@pytest.mark.asyncio
async def test_generate_raises_after_max_retries():
    """If every candidate collides for 5 attempts → ShortCodeGenerationError."""
    session = _mock_session_with_existence_results(True, True, True, True, True)
    with pytest.raises(ShortCodeGenerationError):
        await generate(uuid4(), session)
