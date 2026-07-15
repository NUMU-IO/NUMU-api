"""Weekly analytics digest — pure, bilingual summary builder.

Turns a week's metrics into a short merchant-facing recap: one headline
line + a few highlight bullets, in English or Egyptian Arabic. Kept pure
(no I/O) so it's unit-testable and reusable by the Celery send task, the
preview endpoint, and the WhatsApp/email formatters.

Money arrives in cents and is formatted to major units at the edge; the
caller passes a ``format_money`` callable so currency/locale stay
consistent with the rest of the platform.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

_T = {
    "en": {
        "headline_up": "Sales are up {pct}% this week — {revenue}.",
        "headline_down": "Sales are down {pct}% this week — {revenue}.",
        "headline_flat": "Steady week — {revenue} in sales.",
        "headline_first": "Your first week of sales — {revenue}.",
        "orders": "{n} orders ({delta} vs last week)",
        "new_customers": "{n} new customers",
        "top_product": "Best seller: {name} ({units} sold)",
        "aov": "Average order value: {value}",
        "conversion": "Conversion rate: {pct}%",
        "no_sales": "No sales this week — a good time to run a promotion.",
        "same": "same",
    },
    "ar": {
        "headline_up": "مبيعاتك زادت {pct}% الأسبوع ده — {revenue}.",
        "headline_down": "مبيعاتك قلّت {pct}% الأسبوع ده — {revenue}.",
        "headline_flat": "أسبوع ثابت — {revenue} مبيعات.",
        "headline_first": "أول أسبوع مبيعات ليك — {revenue}.",
        "orders": "{n} طلب ({delta} عن الأسبوع اللي فات)",
        "new_customers": "{n} عميل جديد",
        "top_product": "الأكتر مبيعاً: {name} ({units} قطعة)",
        "aov": "متوسط قيمة الطلب: {value}",
        "conversion": "معدل التحويل: {pct}%",
        "no_sales": "مفيش مبيعات الأسبوع ده — وقت كويس لعرض ترويجي.",
        "same": "زي ما هو",
    },
}


def _delta_label(current: int, previous: int, lang: str) -> str:
    """Signed delta like "+3" / "-2" / "same" for a count."""
    if previous == 0:
        return f"+{current}" if current else _T[lang]["same"]
    diff = current - previous
    if diff == 0:
        return _T[lang]["same"]
    return f"+{diff}" if diff > 0 else str(diff)


def build_weekly_digest(
    metrics: dict[str, Any],
    lang: str,
    format_money: Callable[[int], str],
) -> dict[str, Any]:
    """Build ``{headline, highlights[], has_sales}`` from a week's metrics.

    Expected ``metrics`` keys (all optional, default 0/None):
      revenue_cents, prev_revenue_cents, orders, prev_orders,
      new_customers, aov_cents, conversion_pct,
      top_product_name, top_product_units.
    """
    lang = lang if lang in _T else "en"
    t = _T[lang]

    revenue = int(metrics.get("revenue_cents", 0) or 0)
    prev_revenue = int(metrics.get("prev_revenue_cents", 0) or 0)
    orders = int(metrics.get("orders", 0) or 0)
    prev_orders = int(metrics.get("prev_orders", 0) or 0)

    revenue_str = format_money(revenue)

    # ── Headline ──
    if revenue == 0:
        headline = t["no_sales"]
    elif prev_revenue == 0:
        headline = t["headline_first"].format(revenue=revenue_str)
    else:
        change = (revenue - prev_revenue) / prev_revenue * 100
        pct = abs(round(change))
        if pct < 3:
            headline = t["headline_flat"].format(revenue=revenue_str)
        elif change > 0:
            headline = t["headline_up"].format(pct=pct, revenue=revenue_str)
        else:
            headline = t["headline_down"].format(pct=pct, revenue=revenue_str)

    # ── Highlights ──
    highlights: list[str] = []
    if orders:
        highlights.append(
            t["orders"].format(n=orders, delta=_delta_label(orders, prev_orders, lang))
        )
    if metrics.get("new_customers"):
        highlights.append(t["new_customers"].format(n=int(metrics["new_customers"])))
    if metrics.get("top_product_name"):
        highlights.append(
            t["top_product"].format(
                name=metrics["top_product_name"],
                units=int(metrics.get("top_product_units", 0) or 0),
            )
        )
    if metrics.get("aov_cents"):
        highlights.append(
            t["aov"].format(value=format_money(int(metrics["aov_cents"])))
        )
    if metrics.get("conversion_pct"):
        highlights.append(
            t["conversion"].format(pct=round(float(metrics["conversion_pct"]), 1))
        )

    return {
        "headline": headline,
        "highlights": highlights,
        "has_sales": revenue > 0,
    }
