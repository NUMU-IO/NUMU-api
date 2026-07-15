"""Predictions v1 (AI-5) — statistical, self-hosted, zero API cost.

Pure math over data the analytics repositories already fetch:

- **Stock depletion dates** — EWMA-blended velocity (7d weighted over
  28d) with a Poisson-derived early/late band: at daily rate λ the
  1-sigma band on consumption is λ ± √λ, so the run-out date bracket is
  ``stock / (λ + √λ)`` .. ``stock / (λ − √λ)``.
- **Monthly revenue / daily orders bands** — delegates to the existing
  Holt-Winters in :py:mod:`forecast_service` (weekly seasonality,
  residual-σ intervals) and sums the remaining-days horizon.
- **Repeat purchase probability** — empirical: the store's own
  inter-purchase gap distribution, no parametric model.
- **COD rejection risk** — empirical rate with a Wilson score interval
  and per-governorate empirical-Bayes shrinkage toward the store rate.

Every output carries a confidence tier derived from sample size —
a prediction without its uncertainty is a lie of omission.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta

from src.application.services.forecast_service import _run_holt_winters

# Below this many resolved samples we refuse to show a rate at all.
MIN_COD_RESOLVED = 5
# Pseudo-observations pulling a governorate rate toward the store rate.
COD_SHRINKAGE_M = 10.0


def ewma_velocity(units_7d: int, units_28d: int) -> float:
    """Daily sales velocity blending recent pace with the stable base.

    0.6 × (7-day rate) + 0.4 × (28-day rate): responsive to a spike or
    a stall this week without letting one odd week own the estimate.
    """
    return 0.6 * (units_7d / 7.0) + 0.4 * (units_28d / 28.0)


def stockout_prediction(
    product: dict,
    today: date,
    horizon_days: int = 45,
) -> dict | None:
    """Run-out window for one product, or None when not at risk.

    ``product`` needs: product_id, name, quantity, units_7d, units_28d.
    """
    quantity = product.get("quantity") or 0
    if quantity <= 0:
        return None  # already out — that's IV-1's job, not a prediction
    velocity = ewma_velocity(product.get("units_7d", 0), product.get("units_28d", 0))
    if velocity <= 0:
        return None
    days = quantity / velocity
    if days > horizon_days:
        return None

    sigma = math.sqrt(velocity)
    early_days = quantity / (velocity + sigma)
    late_days = quantity / (velocity - sigma) if velocity > sigma else None

    return {
        "product_id": product["product_id"],
        "name": product.get("name") or "(unnamed)",
        "quantity": int(quantity),
        "velocity_per_day": round(velocity, 2),
        "days_left": round(days, 1),
        "run_out_date": (today + timedelta(days=days)).isoformat(),
        "early_date": (today + timedelta(days=early_days)).isoformat(),
        "late_date": (
            (today + timedelta(days=late_days)).isoformat()
            if late_days is not None and late_days <= 365
            else None
        ),
        "urgent": days <= 7,
        # ≥14 units over the window ≈ enough history for the blend to
        # mean something; below that the band is decoration.
        "confidence": "high" if product.get("units_28d", 0) >= 14 else "low",
        "suggested_reorder_qty": max(int(math.ceil(velocity * 30)), 1),
    }


def predict_stockouts(
    products: list[dict],
    today: date,
    horizon_days: int = 45,
    limit: int = 10,
) -> list[dict]:
    """Products running out within the horizon, soonest first."""
    out = []
    for p in products:
        pred = stockout_prediction(p, today, horizon_days)
        if pred:
            out.append(pred)
    out.sort(key=lambda x: x["days_left"])
    return out[:limit]


def month_revenue_band(
    daily_revenue_cents: list[float],
    mtd_cents: int,
    remaining_days: int,
) -> dict | None:
    """Expected month-end revenue = actual MTD + forecast of the rest."""
    if len(daily_revenue_cents) < 14:
        return None
    if remaining_days <= 0:
        return {
            "expected_cents": mtd_cents,
            "lower_cents": mtd_cents,
            "upper_cents": mtd_cents,
            "mtd_cents": mtd_cents,
            "remaining_days": 0,
            "confidence": "high",
        }
    import numpy as np

    predicted, lower, upper = _run_holt_winters(
        np.array(daily_revenue_cents, dtype=float), remaining_days
    )
    expected = mtd_cents + float(predicted.sum())
    lo = mtd_cents + float(lower.sum())
    hi = mtd_cents + float(upper.sum())
    # Band width relative to the estimate → confidence tier.
    spread = (hi - lo) / expected if expected > 0 else 1.0
    return {
        "expected_cents": int(round(expected)),
        "lower_cents": int(round(lo)),
        "upper_cents": int(round(hi)),
        "mtd_cents": int(mtd_cents),
        "remaining_days": remaining_days,
        "confidence": "high" if spread < 0.4 else "medium" if spread < 0.8 else "low",
    }


def today_orders_band(daily_orders: list[float]) -> dict | None:
    """Expected order count for today with a 95% band."""
    if len(daily_orders) < 14:
        return None
    import numpy as np

    predicted, lower, upper = _run_holt_winters(np.array(daily_orders, dtype=float), 1)
    return {
        "predicted": int(round(float(predicted[0]))),
        "lower": int(round(float(lower[0]))),
        "upper": int(round(float(upper[0]))),
    }


def repeat_probability(rows: list[dict], now: datetime) -> dict:
    """Empirical repeat-purchase profile from the store's own history.

    ``rows`` are ``customer_period_aggregates`` dicts (orders,
    first_at, last_at). The per-customer average gap distribution
    yields P(next order within 30d | the customer repeats at all).
    """
    total = len(rows)
    repeaters = [r for r in rows if r["orders"] >= 2]
    repeat_rate = len(repeaters) / total if total else 0.0

    gaps: list[float] = []
    for r in repeaters:
        if r.get("first_at") and r.get("last_at"):
            span = (r["last_at"] - r["first_at"]).total_seconds() / 86400
            if span > 0:
                gaps.append(span / (r["orders"] - 1))

    within_30 = sum(1 for g in gaps if g <= 30) / len(gaps) if gaps else 0.0
    gaps.sort()
    median_gap = gaps[len(gaps) // 2] if gaps else None

    if len(repeaters) >= 200:
        confidence = "high"
    elif len(repeaters) >= 30:
        confidence = "medium"
    else:
        confidence = "low"

    return {
        "customers": total,
        "repeat_customers": len(repeaters),
        "repeat_rate_pct": round(repeat_rate * 100, 1),
        "p_next_30d_pct": round(repeat_rate * within_30 * 100, 1),
        "median_gap_days": round(median_gap, 1) if median_gap is not None else None,
        "confidence": confidence,
    }


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — sane on the small samples COD data has."""
    if n == 0:
        return (0.0, 0.0)
    p = successes / n
    denom = 1 + z * z / n
    center = (p + z * z / (2 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, center - margin), min(1.0, center + margin))


def cod_rejection_profile(
    governorate_rows: list[dict],
    pending: dict,
) -> dict | None:
    """Store + per-governorate COD rejection rates and expected loss.

    ``governorate_rows`` come from ``cod_outcomes_by_governorate``
    (resolved = delivered + returned only). Governorate rates are
    shrunk toward the store rate with ``COD_SHRINKAGE_M``
    pseudo-observations so a 1-of-2 governorate doesn't scream 50%.
    """
    resolved = sum(r["resolved"] for r in governorate_rows)
    returned = sum(r["returned"] for r in governorate_rows)
    if resolved < MIN_COD_RESOLVED:
        return None

    store_rate = returned / resolved
    lo, hi = wilson_interval(returned, resolved)

    by_gov = []
    for r in sorted(governorate_rows, key=lambda x: x["resolved"], reverse=True):
        raw = r["returned"] / r["resolved"] if r["resolved"] else 0.0
        shrunk = (r["returned"] + COD_SHRINKAGE_M * store_rate) / (
            r["resolved"] + COD_SHRINKAGE_M
        )
        by_gov.append({
            "governorate": r["governorate"],
            "resolved": r["resolved"],
            "returned": r["returned"],
            "rate_pct": round(raw * 100, 1),
            "shrunk_rate_pct": round(shrunk * 100, 1),
        })

    return {
        "store_rate_pct": round(store_rate * 100, 1),
        "wilson_low_pct": round(lo * 100, 1),
        "wilson_high_pct": round(hi * 100, 1),
        "resolved_orders": resolved,
        "pending_orders": pending.get("orders", 0),
        "pending_value_cents": pending.get("value_cents", 0),
        "expected_loss_cents": int(round(pending.get("value_cents", 0) * store_rate)),
        "by_governorate": by_gov[:10],
        "confidence": "high"
        if resolved >= 100
        else "medium"
        if resolved >= 30
        else "low",
    }
