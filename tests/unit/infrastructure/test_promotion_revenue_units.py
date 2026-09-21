"""Promotion revenue is summed in CENTS, as the convert events store it.

`metadata.order_total` on a convert event is `OrderPaidEvent.total`, which is
`order.total` — integer cents. The aggregation used to read it as major units
and scale by 100, so a single 1,254 EGP order showed "125,400 EGP revenue" on
the merchant's offer page.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from src.infrastructure.repositories.promotion_event_repository import (
    PromotionEventRepository,
)


def _repo_returning(rows):
    result = MagicMock()
    result.all.return_value = rows
    return PromotionEventRepository(
        SimpleNamespace(execute=AsyncMock(return_value=result))
    )


async def test_revenue_is_the_order_total_in_cents_not_scaled_again():
    # One paid 1,254.00 EGP order that took 126.00 EGP off.
    repo = _repo_returning([("convert", 1, 12_600, Decimal("125400"))])
    counts = await repo.counts_for_promotion(uuid4())
    assert counts.revenue_cents == 125_400  # 1,254.00 EGP — not 125,400
    assert counts.discount_total_cents == 12_600
    assert counts.conversions == 1


async def test_revenue_sums_across_paid_orders():
    repo = _repo_returning([("convert", 2, 20_000, Decimal("300000"))])
    counts = await repo.counts_for_promotion(uuid4())
    assert counts.revenue_cents == 300_000


async def test_no_events_means_zeroes():
    counts = await _repo_returning([]).counts_for_promotion(uuid4())
    assert counts.revenue_cents == 0
    assert counts.discount_total_cents == 0
