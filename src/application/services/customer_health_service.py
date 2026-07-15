"""Customer Health Score (AI-3) — pure scorer + state machine.

0–100 composite per customer, computed at read time from order history
(the same live-compute convention as /customer-segments). Components:

- Recency 30 — exp(-days_since_last / store_median_gap) × 30. The decay
  is STORE-RELATIVE (a perfume store's rhythm ≠ a grocery's); median
  inter-purchase gap falls back to 30 days when the store has too few
  repeat customers to measure.
- Frequency 25 — order-count quintile within the store's customers.
- Monetary 20 — lifetime-spend quintile (this finally puts the money
  dimension to work — the RFM segmenter historically ignored it, D14).
- Engagement 10 — funnel-event percentile over the last 30 days.
- Reliability 10 — 10 minus 5 per returned/rejected order (floor 0).
- Momentum 5 — last-90d spend vs the prior 90d (+5 / 0).

States are evaluated in order, first match wins (see ``classify``).
"""

from __future__ import annotations

import math
from datetime import datetime
from statistics import median

DEFAULT_GAP_DAYS = 30.0

STATES = (
    "vip",
    "loyal",
    "active",
    "growing",
    "high_value_prospect",
    "coupon_hunter",
    "at_risk",
    "churned",
)


def _quintile(value: float, sorted_values: list[float]) -> int:
    """1–5 rank of ``value`` within the store's distribution."""
    if not sorted_values:
        return 1
    below = sum(1 for v in sorted_values if v < value)
    return min(5, max(1, math.ceil((below + 1) / max(len(sorted_values), 1) * 5)))


def median_interpurchase_gap(rows: list[dict]) -> float:
    """Store-typical days between orders, from repeat customers'
    (last - first) / (orders - 1). Falls back to 30d below 3 samples."""
    gaps = []
    for r in rows:
        if r["orders"] >= 2 and r.get("first_at") and r.get("last_at"):
            span_days = (r["last_at"] - r["first_at"]).total_seconds() / 86400
            if span_days > 0:
                gaps.append(span_days / (r["orders"] - 1))
    return median(gaps) if len(gaps) >= 3 else DEFAULT_GAP_DAYS


def score_customer(
    row: dict,
    *,
    now: datetime,
    median_gap: float,
    freq_dist: list[float],
    money_dist: list[float],
    engagement_dist: list[float],
) -> dict:
    """Score one customer row → {score, components, state inputs}."""
    days_since = (
        (now - row["last_at"]).total_seconds() / 86400 if row.get("last_at") else 999
    )
    recency = math.exp(-days_since / max(median_gap, 1.0)) * 30
    f_q = _quintile(row["orders"], freq_dist)
    m_q = _quintile(row["total_spent_cents"], money_dist)
    frequency = f_q / 5 * 25
    monetary = m_q / 5 * 20
    engagement = (
        _quintile(row.get("events_30d", 0), engagement_dist) / 5 * 10
        if row.get("events_30d", 0) > 0
        else 0.0
    )
    reliability = max(0.0, 10.0 - 5.0 * row.get("returned_orders", 0))
    momentum = (
        5.0
        if row.get("spend_last_90", 0) > row.get("spend_prior_90", 0) > 0
        or (row.get("spend_prior_90", 0) == 0 and row.get("spend_last_90", 0) > 0)
        else 0.0
    )
    score = round(recency + frequency + monetary + engagement + reliability + momentum)
    return {
        "score": min(score, 100),
        "m_quintile": m_q,
        "days_since": days_since,
    }


def classify(row: dict, scored: dict, *, median_gap: float, store_aov: int) -> str:
    """First-match state per the §7.4 table."""
    score = scored["score"]
    days_since = scored["days_since"]
    orders = row["orders"]

    if orders >= 2 and days_since > 3 * median_gap and score < 25:
        return "churned"
    if orders >= 2 and days_since > 1.5 * median_gap:
        return "at_risk"
    if score >= 80 and scored["m_quintile"] == 5:
        return "vip"
    if score >= 70 and orders >= 3:
        return "loyal"
    if orders >= 2 and row.get("coupon_orders", 0) / orders >= 0.8:
        return "coupon_hunter"
    if (
        orders == 1
        and store_aov > 0
        and row["total_spent_cents"] >= 2 * store_aov
        and days_since <= 60
    ):
        return "high_value_prospect"
    if score >= 55:
        return "active"
    if orders == 1 and days_since > 3 * median_gap:
        return "churned"
    if days_since > 1.5 * median_gap:
        return "at_risk"
    return "growing"


def score_store_customers(
    rows: list[dict], now: datetime, store_aov: int
) -> list[dict]:
    """Score + classify every customer row. Pure."""
    median_gap = median_interpurchase_gap(rows)
    freq_dist = sorted(r["orders"] for r in rows)
    money_dist = sorted(r["total_spent_cents"] for r in rows)
    engagement_dist = sorted(
        r.get("events_30d", 0) for r in rows if r.get("events_30d")
    )

    out = []
    for r in rows:
        scored = score_customer(
            r,
            now=now,
            median_gap=median_gap,
            freq_dist=freq_dist,
            money_dist=money_dist,
            engagement_dist=engagement_dist,
        )
        state = classify(r, scored, median_gap=median_gap, store_aov=store_aov)
        out.append({**r, "score": scored["score"], "state": state})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out
