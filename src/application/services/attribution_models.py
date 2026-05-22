"""Multi-touch attribution models — pure-function credit distributors.

Each calculator takes an **ordered** sequence of touches (oldest first)
and a conversion revenue amount in cents, and returns a list of
``(touch, credit_cents)`` pairs describing how much of the revenue
each touch should be credited with.

* The list of touches must be sorted by ``ts`` ASCENDING. The route
  layer is responsible for the ordering — the calculators trust it.
* Returned credits sum to **exactly** ``revenue_cents``. Rounding
  drift goes onto the LAST touch so the total never under- or
  over-counts (which would compound across thousands of orders).
* When there are no touches the result is an empty list — the caller
  decides whether that means "skip this order" or "credit to direct".

Five models:

* **last_touch** — 100% to the last touch (legacy, default for v1
  reporting).
* **first_touch** — 100% to the first touch.
* **linear** — equal split across all touches.
* **time_decay** — exponential decay over the time gap to conversion;
  the half-life is configurable but defaults to 7 days (matches
  Google Ads' 7-day default).
* **position_based** — Shopify-style U-shape: 40% first, 40% last, 20%
  split evenly across the middle. Degrades to first+last (50/50) when
  only two touches exist; degrades to 100% when one touch exists.

These are pure functions — no DB, no clock, no globals. Easy to test
exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import exp
from typing import Literal, TypeVar
from uuid import UUID

AttributionModel = Literal[
    "last_touch", "first_touch", "linear", "time_decay", "position_based"
]

VALID_MODELS: tuple[AttributionModel, ...] = (
    "last_touch",
    "first_touch",
    "linear",
    "time_decay",
    "position_based",
)


@dataclass(frozen=True)
class Touch:
    """The minimum shape an attribution model needs.

    Lightweight wrapper around the persisted ``customer_touches`` row
    so the calculators don't carry SQLAlchemy mappings. The
    orchestration service translates DB rows into ``Touch`` before
    calling.
    """

    id: UUID
    ts: datetime
    utm_source: str | None
    utm_medium: str | None
    utm_campaign: str | None
    campaign_id: UUID | None


# Generic type so calculators work against either ``Touch`` or
# duck-typed shims (tests / orchestration layer).
T = TypeVar("T")


def _distribute(
    touches: list[T],
    weights: list[float],
    revenue_cents: int,
) -> list[tuple[T, int]]:
    """Split ``revenue_cents`` across ``touches`` per ``weights``.

    Weights are normalized internally — caller passes raw weights and
    we divide by the sum. Last-touch gets the rounding remainder so
    the credits sum to exactly ``revenue_cents``.
    """
    if not touches or revenue_cents == 0:
        return [(t, 0) for t in touches]
    total_weight = sum(weights)
    if total_weight <= 0:
        # Degenerate input — split evenly so we don't return all-zero.
        return _distribute(touches, [1.0] * len(touches), revenue_cents)

    # round() not int(): truncation surfaces float-precision noise
    # when the normalized weights aren't exact (e.g. 1.0 - 0.4 - 0.4
    # yields 0.19999999...). Banker's rounding is fine here because
    # the drift-to-last step below absorbs any per-row drift.
    raw = [round(revenue_cents * (w / total_weight)) for w in weights]
    drift = revenue_cents - sum(raw)
    raw[-1] += drift
    return list(zip(touches, raw, strict=True))


def last_touch_credit(touches: list[T], revenue_cents: int) -> list[tuple[T, int]]:
    """100% credit to the last touch."""
    if not touches:
        return []
    weights = [0.0] * len(touches)
    weights[-1] = 1.0
    return _distribute(touches, weights, revenue_cents)


def first_touch_credit(touches: list[T], revenue_cents: int) -> list[tuple[T, int]]:
    """100% credit to the first touch."""
    if not touches:
        return []
    weights = [0.0] * len(touches)
    weights[0] = 1.0
    return _distribute(touches, weights, revenue_cents)


def linear_credit(touches: list[T], revenue_cents: int) -> list[tuple[T, int]]:
    """Equal split across every touch."""
    if not touches:
        return []
    return _distribute(touches, [1.0] * len(touches), revenue_cents)


def time_decay_credit(
    touches: list[Touch],
    revenue_cents: int,
    *,
    conversion_at: datetime,
    half_life_days: float = 7.0,
) -> list[tuple[Touch, int]]:
    """Exponential time decay — recent touches weigh more.

    Weight for a touch at time ``t`` against conversion time ``c``:

        w(t) = 0.5 ** ((c - t).days / half_life_days)

    Touches at the conversion moment get weight 1.0; touches at one
    half-life ago get 0.5; one week before that 0.25; etc. The
    half-life parameter matches Google Ads' 7-day default — calibrated
    for the typical e-commerce consideration window.

    Negative time deltas (a touch dated after the conversion — clock
    skew on the storefront) collapse to weight 1.0 to avoid silly
    large numbers.
    """
    if not touches:
        return []
    if half_life_days <= 0:
        # Defensive: a 0 half-life would divide by zero. Fall back to
        # linear so we don't fail the analytics call.
        return linear_credit(touches, revenue_cents)

    weights: list[float] = []
    for t in touches:
        delta_days = max((conversion_at - t.ts).total_seconds() / 86400.0, 0.0)
        weight = exp(-0.6931471805599453 * delta_days / half_life_days)
        weights.append(weight)
    return _distribute(touches, weights, revenue_cents)


def position_based_credit(
    touches: list[T],
    revenue_cents: int,
    *,
    first_weight: float = 0.4,
    last_weight: float = 0.4,
) -> list[tuple[T, int]]:
    """U-shaped: 40% first, 40% last, 20% spread across the middle.

    Defaults match Shopify's "position-based" model. The two
    boundary weights are configurable so we can later expose a 30/40/30
    variant or similar without rewriting the math.

    Degenerate cases:
    * 0 touches → empty list.
    * 1 touch → 100% to that touch.
    * 2 touches → split (first_weight + middle/0) and (last_weight + middle/0)
      — the middle contribution is 0 because there are no middle
      touches.
    """
    if not touches:
        return []
    n = len(touches)
    if n == 1:
        return _distribute(touches, [1.0], revenue_cents)

    middle_total = max(0.0, 1.0 - first_weight - last_weight)
    middle_n = max(0, n - 2)
    middle_each = middle_total / middle_n if middle_n > 0 else 0.0

    weights = [middle_each] * n
    weights[0] = first_weight
    weights[-1] = last_weight
    return _distribute(touches, weights, revenue_cents)


def attribute(
    *,
    model: AttributionModel,
    touches: list[Touch],
    revenue_cents: int,
    conversion_at: datetime | None = None,
) -> list[tuple[Touch, int]]:
    """Single-dispatch entry point for the orchestration service.

    The route layer calls this once per order; the orchestrator then
    aggregates credit per channel across orders. ``conversion_at`` is
    only consulted by ``time_decay`` — passing None there falls back
    to linear (the time-decay weights collapse to constants without
    a reference point).
    """
    if model == "last_touch":
        return last_touch_credit(touches, revenue_cents)
    if model == "first_touch":
        return first_touch_credit(touches, revenue_cents)
    if model == "linear":
        return linear_credit(touches, revenue_cents)
    if model == "time_decay":
        if conversion_at is None:
            return linear_credit(touches, revenue_cents)
        return time_decay_credit(touches, revenue_cents, conversion_at=conversion_at)
    if model == "position_based":
        return position_based_credit(touches, revenue_cents)
    raise ValueError(f"unknown attribution model: {model!r}")
