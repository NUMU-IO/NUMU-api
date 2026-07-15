"""Executive dashboard composition (AI-6) — ranking + presentation only.

Everything here is a deterministic collapse of numbers other layers
already produce (signals, predictions, health score, stock snapshot,
channel mix) into six 0–100 gauges and a bilingual briefing line.
No new data collection, no models, no API calls.

Each gauge returns ``None`` when its inputs are too thin to judge —
an honest "—" beats a fabricated 50.
"""

from __future__ import annotations

from datetime import datetime


def _clamp(v: float) -> int:
    return int(round(min(100.0, max(0.0, v))))


def revenue_gauge(weekly: list[dict]) -> int | None:
    """Week-over-week revenue momentum, mapped so flat = 50.

    ``weekly`` is newest-first and index 0 is the current partial week,
    so the comparison is complete-week vs the one before it. +50% WoW
    saturates at 100; −50% bottoms at 0.
    """
    if len(weekly) < 3:
        return None
    last, prior = weekly[1], weekly[2]
    prior_rev = prior.get("revenue_cents", 0)
    if prior_rev <= 0:
        return None
    ratio = last.get("revenue_cents", 0) / prior_rev
    return _clamp(50 + 100 * (ratio - 1))


def profit_gauge(
    revenue_30d_cents: int,
    discounts_30d_cents: int,
    cod_rejected_amount_cents: int,
    cod_total_amount_cents: int,
) -> int | None:
    """Margin-erosion view: discount leakage + COD rejection losses."""
    if revenue_30d_cents <= 0:
        return None
    discount_leak = discounts_30d_cents / (revenue_30d_cents + discounts_30d_cents)
    cod_loss = (
        cod_rejected_amount_cents / cod_total_amount_cents
        if cod_total_amount_cents > 0
        else 0.0
    )
    return _clamp(100 - min(40.0, discount_leak * 200) - min(40.0, cod_loss * 100))


def inventory_gauge(stock: list[dict]) -> int | None:
    """Share of catalog healthily in stock; low stock counts half."""
    if not stock:
        return None
    total = len(stock)
    out = sum(1 for p in stock if (p.get("quantity") or 0) == 0)
    low = sum(1 for p in stock if 0 < (p.get("quantity") or 0) <= 5)
    return _clamp((total - out - 0.5 * low) / total * 100)


def marketing_gauge(
    tagged_pct: float | None,
    channel_sessions: dict[str, int],
) -> int | None:
    """Half tagging hygiene, half channel diversification.

    Concentration uses the Herfindahl index over session shares:
    HHI ≤ 0.4 (healthy mix) scores full marks; a single-channel store
    (HHI = 1) scores zero on that half.
    """
    total = sum(channel_sessions.values())
    if tagged_pct is None and total == 0:
        return None
    hygiene = tagged_pct if tagged_pct is not None else 0.0
    if total > 0:
        hhi = sum((n / total) ** 2 for n in channel_sessions.values())
        diversity = (1 - max(0.0, hhi - 0.4) / 0.6) * 100
    else:
        diversity = 0.0
    return _clamp(0.5 * hygiene + 0.5 * diversity)


# Lifecycle states → how much of "a healthy customer base" each is worth.
CUSTOMER_STATE_WEIGHTS = {
    "vip": 100,
    "loyal": 90,
    "active": 75,
    "growing": 70,
    "high_value_prospect": 60,
    "coupon_hunter": 45,
    "at_risk": 25,
    "churned": 5,
}


def customer_gauge(distribution: dict[str, int]) -> int | None:
    """The AI-3 state distribution collapsed to one weighted score."""
    total = sum(distribution.values())
    if total == 0:
        return None
    weighted = sum(
        CUSTOMER_STATE_WEIGHTS.get(state, 50) * n for state, n in distribution.items()
    )
    return _clamp(weighted / total)


def briefing(
    now_local: datetime,
    orders_band: dict | None,
    month_band: dict | None,
    problems: int,
    opportunities: int,
    fmt,
) -> dict:
    """One bilingual sentence answering "how is today going?"."""
    hour = now_local.hour
    if hour < 12:
        greet_en, greet_ar = "Good morning", "صباح الخير"
    elif hour < 18:
        greet_en, greet_ar = "Good afternoon", "مساء الخير"
    else:
        greet_en, greet_ar = "Good evening", "مساء الخير"

    if month_band:
        pace_en = (
            f"revenue is pacing to {fmt(month_band['lower_cents'])}–"
            f"{fmt(month_band['upper_cents'])} this month"
        )
        pace_ar = (
            f"الإيراد ماشي ناحية {fmt(month_band['lower_cents'])}–"
            f"{fmt(month_band['upper_cents'])} الشهر ده"
        )
    elif orders_band:
        pace_en = f"expect {orders_band['lower']}–{orders_band['upper']} orders today"
        pace_ar = f"متوقع {orders_band['lower']}–{orders_band['upper']} طلب النهارده"
    else:
        pace_en = "not enough history for a revenue forecast yet"
        pace_ar = "لسه مفيش بيانات كفاية لتوقع الإيراد"

    if problems == 0:
        prob_en, prob_ar = "no issues need attention", "مفيش مشاكل محتاجة نظرة"
    elif problems == 1:
        prob_en, prob_ar = "1 issue needs attention", "مشكلة واحدة محتاجة نظرة"
    else:
        prob_en = f"{problems} issues need attention"
        prob_ar = f"{problems} مشاكل محتاجة نظرة"

    if opportunities == 0:
        opp_en, opp_ar = "no opportunities are waiting", "مفيش فرص مستنية"
    elif opportunities == 1:
        opp_en, opp_ar = "1 opportunity is waiting", "فرصة واحدة مستنياك"
    else:
        opp_en = f"{opportunities} opportunities are waiting"
        opp_ar = f"{opportunities} فرص مستنياك"

    return {
        "en": (
            f"{greet_en}. {pace_en[0].upper()}{pace_en[1:]}; {prob_en} and {opp_en}."
        ),
        "ar": f"{greet_ar}. {pace_ar}؛ {prob_ar} و{opp_ar}.",
    }
