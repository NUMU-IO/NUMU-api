"""Currency conversion service — Phase 6.

Display-only conversion for multi-currency presentment. The store
still **captures** payment in its base currency (EGP for MENA-first
v1) — these rates power the storefront's `<Money>` rendering when a
visitor toggles to USD/EUR/etc.

Read path:
    rate = CurrencyService.get_rate(base="EGP", target="USD")
    converted_cents = round(amount_cents * rate)

Write path:
    daily Celery beat → CurrencyService.upsert_rate(base, target, rate)

The rate table is small (a handful of currencies × N targets), so a
plain ``SELECT WHERE base=... AND target=...`` is fine — no caching
layer needed in v1. If we ever go global, we'd add a Redis cache.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.currency_rate import CurrencyRateModel


@dataclass(frozen=True)
class CurrencyRateRecord:
    base: str
    target: str
    rate: Decimal
    fetched_at: datetime


class CurrencyService:
    """Lookups + upserts on the global currency_rates table."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_rate(self, base: str, target: str) -> Decimal | None:
        """Fetch the most recent rate for base→target.

        Returns None when no row exists. Treat None as "fall back to
        the merchant's base currency display" — no implicit 1.0
        conversion (that would silently quote in the wrong currency).
        """

        if base == target:
            return Decimal("1.0")

        stmt = select(CurrencyRateModel).where(
            CurrencyRateModel.base == base,
            CurrencyRateModel.target == target,
        )
        result = await self._session.execute(stmt)
        row = result.scalar_one_or_none()
        return row.rate if row is not None else None

    async def list_rates(self, base: str) -> list[CurrencyRateRecord]:
        """All known target rates for a given base currency."""

        stmt = select(CurrencyRateModel).where(CurrencyRateModel.base == base)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            CurrencyRateRecord(
                base=r.base, target=r.target, rate=r.rate, fetched_at=r.fetched_at
            )
            for r in rows
        ]

    async def upsert_rates(
        self,
        base: str,
        rates: Iterable[tuple[str, Decimal]],
        fetched_at: datetime | None = None,
    ) -> int:
        """Upsert a batch of rates from a single feed pull.

        Daily Celery task pulls from ECB/openexchangerates and calls
        this with a list of (target, rate) pairs. We use ON CONFLICT
        rather than DELETE+INSERT so a partial pull (e.g. one
        unsupported currency 4xx'd at the source) doesn't blank out
        previously-good rates.
        """

        when = fetched_at or datetime.utcnow()
        n = 0
        for target, rate in rates:
            stmt = (
                pg_insert(CurrencyRateModel)
                .values(base=base, target=target, rate=rate, fetched_at=when)
                .on_conflict_do_update(
                    constraint="pk_currency_rates",
                    set_={"rate": rate, "fetched_at": when},
                )
            )
            await self._session.execute(stmt)
            n += 1
        return n

    @staticmethod
    def convert_cents(amount_cents: int, rate: Decimal) -> int:
        """Display-side conversion. Banker's rounding to whole cents."""

        if amount_cents == 0:
            return 0
        converted = (Decimal(amount_cents) * rate).quantize(Decimal("1"))
        return int(converted)
