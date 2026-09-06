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
        "headline_low": "{orders} this week vs {prev_orders} last week — {revenue}.",
        "headline_flat": "Steady week — {revenue} in sales.",
        "headline_first": "Your first week of sales — {revenue}.",
        "orders": "{n} ({delta} vs last week)",
        "new_customers": "{n} new customers",
        "top_product": "Best seller: {name} ({units} sold)",
        "aov": "Average order value: {value}",
        "conversion": "Conversion rate: {pct}%",
        "no_sales": "No sales this week — a good time to run a promotion.",
        "same": "same",
        "nudge_down": (
            "Two things that usually work: message last week's buyers with a "
            "returning-customer code, and put your best seller back in front of "
            "cold traffic."
        ),
        "nudge_none": (
            "Start with the cheapest win: a 48-hour offer sent to everyone who "
            "already bought from you."
        ),
    },
    "ar": {
        "headline_up": "مبيعاتك زادت {pct}% الأسبوع ده — {revenue}.",
        "headline_down": "مبيعاتك قلّت {pct}% الأسبوع ده — {revenue}.",
        "headline_low": (
            "{orders} الأسبوع ده مقابل {prev_orders} الأسبوع اللي فات — {revenue}."
        ),
        "headline_flat": "أسبوع ثابت — {revenue} مبيعات.",
        "headline_first": "أول أسبوع مبيعات ليك — {revenue}.",
        "orders": "{n} ({delta} عن الأسبوع اللي فات)",
        "new_customers": "{n} عميل جديد",
        "top_product": "الأكتر مبيعاً: {name} ({units} قطعة)",
        "aov": "متوسط قيمة الطلب: {value}",
        "conversion": "معدل التحويل: {pct}%",
        "no_sales": "مفيش مبيعات الأسبوع ده — وقت كويس لعرض ترويجي.",
        "same": "زي ما هو",
        "nudge_down": (
            "حاجتين بيجيبوا نتيجة: ابعت كود خصم لعملاء الأسبوع اللي فات، "
            "وارجع تعلن على المنتج الأكتر مبيعاً."
        ),
        "nudge_none": ("أرخص بداية: عرض ٤٨ ساعة تبعته لكل اللي اشتروا منك قبل كده."),
    },
}

# Below this many orders on both sides, a percentage swing is noise (1 → 2
# orders reads as "+100%"), so the headline shows the counts instead.
_LOW_VOLUME_ORDERS = 5


def _orders_noun(n: int, lang: str) -> str:
    """Order count with the right plural: "1 order", "طلبين", "5 طلبات"."""
    if lang == "ar":
        if n == 1:
            return "طلب واحد"
        if n == 2:
            return "طلبين"
        return f"{n} طلبات" if 3 <= n <= 10 else f"{n} طلب"
    return "1 order" if n == 1 else f"{n} orders"


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
    """Build ``{headline, highlights[], has_sales, nudge}`` from a week's metrics.

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
    declined = False
    if revenue == 0:
        headline = t["no_sales"]
    elif prev_revenue == 0:
        headline = t["headline_first"].format(revenue=revenue_str)
    else:
        change = (revenue - prev_revenue) / prev_revenue * 100
        pct = abs(round(change))
        declined = change < 0 and pct >= 3
        if pct < 3:
            headline = t["headline_flat"].format(revenue=revenue_str)
        elif max(orders, prev_orders) < _LOW_VOLUME_ORDERS:
            headline = t["headline_low"].format(
                orders=_orders_noun(orders, lang),
                prev_orders=_orders_noun(prev_orders, lang),
                revenue=revenue_str,
            )
        elif change > 0:
            headline = t["headline_up"].format(pct=pct, revenue=revenue_str)
        else:
            headline = t["headline_down"].format(pct=pct, revenue=revenue_str)

    # ── Highlights ──
    highlights: list[str] = []
    if orders:
        highlights.append(
            t["orders"].format(
                n=_orders_noun(orders, lang),
                delta=_delta_label(orders, prev_orders, lang),
            )
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
    # With a single order the AOV just repeats the revenue already in the
    # headline, so it earns a line only from two orders up.
    if metrics.get("aov_cents") and orders > 1:
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
        "nudge": t["nudge_none"]
        if revenue == 0
        else (t["nudge_down"] if declined else None),
    }
