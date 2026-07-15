"""Advisor rule registry — pure, deterministic, zero-API business rules.

Each rule is a predicate over a pre-built ``MetricsContext`` dict (see
``intelligence_service.build_context``). A firing rule returns
``{severity, metrics, impact_cents}``; the engine persists it as a
``merchant_signals`` row. Copy lives HERE as bilingual templates and is
rendered at read time by interpolating the stored metrics snapshot — so
copy edits never touch data.

Money placeholders are pre-formatted into the snapshot as ``*_money``
strings by the context builder's formatter; numeric metrics stay raw so
the impact math remains auditable.

The 15 launch rules deliberately avoid session-derived inputs (those
metrics only started accruing when the page-view tracker shipped);
they run on orders / inventory / COD / funnel-event data that has
existed for months.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Severity vocabulary (matches merchant_signals.severity).
CRITICAL = "critical"
WARNING = "warning"
OPPORTUNITY = "opportunity"
INFO = "info"


@dataclass(frozen=True)
class Rule:
    rule_id: str
    kind: str  # "advice" | "opportunity"
    check: Callable[[dict], dict | None]
    title: dict[str, str]  # lang -> template
    action: dict[str, str]  # lang -> template


def _weekly(ctx: dict, idx: int) -> dict:
    """Weekly aggregates, idx 0 = current (most recent, possibly partial)
    week, 1 = last full week, … Empty dict when history is shorter."""
    weeks = ctx.get("weekly", [])
    return weeks[idx] if idx < len(weeks) else {}


# ── RV: revenue rules ──────────────────────────────────────────────


def _rv1_revenue_drop(ctx: dict) -> dict | None:
    """Last full week's revenue z-scored against the prior 4 weeks."""
    weeks = ctx.get("weekly", [])
    if len(weeks) < 6:
        return None
    current = weeks[1]["revenue_cents"]  # last FULL week
    baseline = [w["revenue_cents"] for w in weeks[2:6]]
    mean = sum(baseline) / 4
    if mean < 50_00:  # < E£50/week — too small to alarm about
        return None
    var = sum((b - mean) ** 2 for b in baseline) / 4
    std = var**0.5
    if std == 0:
        return None
    z = (current - mean) / std
    if z > -2:
        return None
    return {
        "severity": CRITICAL,
        "metrics": {
            "current_cents": current,
            "baseline_cents": round(mean),
            "drop_pct": round((mean - current) / mean * 100),
        },
        "impact_cents": max(round(mean) - current, 0),
    }


def _rv2_aov_declining(ctx: dict) -> dict | None:
    """AOV down three consecutive full weeks."""
    weeks = ctx.get("weekly", [])
    if len(weeks) < 5:
        return None
    aovs = []
    for w in weeks[1:5]:  # last 4 full weeks
        if w["orders"] < 3:  # too thin to trend
            return None
        aovs.append(w["revenue_cents"] / w["orders"])
    if not (aovs[0] < aovs[1] < aovs[2] < aovs[3]):
        return None
    drop = aovs[3] - aovs[0]
    return {
        "severity": WARNING,
        "metrics": {
            "aov_now_cents": round(aovs[0]),
            "aov_then_cents": round(aovs[3]),
        },
        "impact_cents": round(drop * weeks[1]["orders"]),
    }


def _rv3_concentration(ctx: dict) -> dict | None:
    """One product ≥40% of 28d revenue AND slowing week-over-week."""
    products = ctx.get("products_28d", [])
    total = sum(p["revenue_cents"] for p in products)
    if total < 100_00 or not products:
        return None
    top = max(products, key=lambda p: p["revenue_cents"])
    share = top["revenue_cents"] / total
    if share < 0.40:
        return None
    cur = ctx.get("products_7d", {}).get(top["product_id"], {}).get("revenue_cents", 0)
    prev = (
        ctx.get("products_prev_7d", {})
        .get(top["product_id"], {})
        .get("revenue_cents", 0)
    )
    if prev == 0 or cur >= prev * 0.8:  # needs a ≥20% w/w slowdown
        return None
    return {
        "severity": CRITICAL,
        "metrics": {
            "product_name": ctx.get("product_names", {}).get(
                top["product_id"], "(unnamed)"
            ),
            "share_pct": round(share * 100),
            "prev_cents": prev,
            "cur_cents": cur,
        },
        "impact_cents": prev - cur,
    }


def _rv4_discount_creep(ctx: dict) -> dict | None:
    """Discount share of gross up ≥5pts vs the prior week."""
    cur, prev = _weekly(ctx, 1), _weekly(ctx, 2)
    if not cur or not prev or cur.get("gross_cents", 0) < 100_00:
        return None
    if prev.get("gross_cents", 0) <= 0:
        return None
    cur_ratio = cur["discounts_cents"] / cur["gross_cents"]
    prev_ratio = prev["discounts_cents"] / prev["gross_cents"]
    if cur_ratio - prev_ratio < 0.05:
        return None
    return {
        "severity": WARNING,
        "metrics": {
            "cur_pct": round(cur_ratio * 100, 1),
            "prev_pct": round(prev_ratio * 100, 1),
        },
        "impact_cents": round((cur_ratio - prev_ratio) * cur["gross_cents"]),
    }


# ── LP / FG / IV: product + inventory rules ────────────────────────


def _lp1_dead_listing(ctx: dict) -> dict | None:
    """In-stock products (≥20 units) with zero sales in 28 days."""
    sold = {p["product_id"] for p in ctx.get("products_28d", [])}
    dead = [
        p
        for p in ctx.get("stock", [])
        if p["quantity"] >= 20 and p["product_id"] not in sold
    ]
    if not dead:
        return None
    value = sum(p["quantity"] * p["unit_value_cents"] for p in dead)
    worst = max(dead, key=lambda p: p["quantity"] * p["unit_value_cents"])
    return {
        "severity": INFO,
        "metrics": {
            "count": len(dead),
            "value_money": ctx["fmt"](value),
            "example": worst["name"] or "(unnamed)",
        },
        "impact_cents": value,
    }


def _fg1_fast_seller_low_cover(ctx: dict) -> dict | None:
    """Units +50% w/w on a product whose stock covers <14 days."""
    cur7 = ctx.get("products_7d", {})
    prev7 = ctx.get("products_prev_7d", {})
    stock = {p["product_id"]: p for p in ctx.get("stock", [])}
    for pid, cur in cur7.items():
        prev_units = prev7.get(pid, {}).get("units_sold", 0)
        if prev_units < 2 or cur["units_sold"] < prev_units * 1.5:
            continue
        st = stock.get(pid)
        if not st:
            continue
        velocity = cur["units_sold"] / 7
        cover = st["quantity"] / velocity if velocity > 0 else 999
        if cover >= 14:
            continue
        unit_rev = cur["revenue_cents"] / cur["units_sold"]
        return {
            "severity": CRITICAL,
            "metrics": {
                "product_name": st["name"] or "(unnamed)",
                "growth_pct": round((cur["units_sold"] / prev_units - 1) * 100),
                "days_cover": round(cover),
            },
            "impact_cents": round(velocity * 14 * unit_rev),
        }
    return None


def _iv1_top_seller_low_cover(ctx: dict) -> dict | None:
    """A top-10 (28d revenue) seller has <7 days of stock cover."""
    products = sorted(
        ctx.get("products_28d", []), key=lambda p: p["revenue_cents"], reverse=True
    )[:10]
    stock = {p["product_id"]: p for p in ctx.get("stock", [])}
    for p in products:
        st = stock.get(p["product_id"])
        if not st or p["units_sold"] <= 0:
            continue
        velocity = p["units_sold"] / 28
        if velocity <= 0:
            continue
        cover = st["quantity"] / velocity
        if cover >= 7 or st["quantity"] == 0:  # 0 handled by IV-3
            continue
        unit_rev = p["revenue_cents"] / p["units_sold"]
        return {
            "severity": CRITICAL,
            "metrics": {
                "product_name": st["name"] or "(unnamed)",
                "days_cover": round(cover, 1),
                "suggested_qty": max(round(velocity * 21), 5),
            },
            "impact_cents": round(velocity * 7 * unit_rev),
        }
    return None


def _iv2_dead_stock_value(ctx: dict) -> dict | None:
    """≥ E£1,000 frozen in stock with no sale for 90+ days."""
    value = int(ctx.get("dead_stock_90_value_cents", 0) or 0)
    if value < 1_000_00:
        return None
    return {
        "severity": INFO,
        "metrics": {"value_money": ctx["fmt"](value)},
        "impact_cents": value,
    }


def _iv3_oos_while_selling(ctx: dict) -> dict | None:
    """A product at 0 stock that was selling ≥1/day over 28 days."""
    stock = ctx.get("stock", [])
    sales = {p["product_id"]: p for p in ctx.get("products_28d", [])}
    for st in stock:
        if st["quantity"] != 0:
            continue
        s = sales.get(st["product_id"])
        if not s:
            continue
        velocity = s["units_sold"] / 28
        if velocity < 1:
            continue
        unit_rev = s["revenue_cents"] / s["units_sold"] if s["units_sold"] else 0
        return {
            "severity": WARNING,
            "metrics": {
                "product_name": st["name"] or "(unnamed)",
                "daily_units": round(velocity, 1),
            },
            "impact_cents": round(velocity * 7 * unit_rev),
        }
    return None


# ── CD: COD rules ──────────────────────────────────────────────────


def _cd1_rejection_rate(ctx: dict) -> dict | None:
    """COD rejection ≥20% over 30 days (≥10 COD orders)."""
    cod = ctx.get("cod", {})
    total = cod.get("total", 0)
    if total < 10:
        return None
    rejected = cod.get("rejected", 0)
    rate = rejected / total
    if rate < 0.20:
        return None
    return {
        "severity": CRITICAL,
        "metrics": {
            "rate_pct": round(rate * 100, 1),
            "rejected": rejected,
            "total": total,
        },
        # Each rejection ≈ round-trip shipping lost; approximate with the
        # rejected order value share the merchant never collected.
        "impact_cents": int(cod.get("rejected_amount", 0) or 0),
    }


def _cd2_governorate_hotspot(ctx: dict) -> dict | None:
    """One governorate rejects ≥15pts above the store average."""
    cod = ctx.get("cod", {})
    total = cod.get("total", 0)
    if total < 10:
        return None
    avg = cod.get("rejected", 0) / total
    for loc in ctx.get("cod_by_gov", []):
        if loc.get("total", 0) < 10:
            continue
        if (loc.get("rate", 0) / 100) - avg < 0.15:
            continue
        return {
            "severity": WARNING,
            "metrics": {
                "governorate": loc["location"],
                "rate_pct": loc["rate"],
                "avg_pct": round(avg * 100, 1),
                "orders": loc["total"],
            },
            "impact_cents": None,
        }
    return None


# ── CA: cart / checkout rules ──────────────────────────────────────


def _ca1_cart_to_checkout(ctx: dict) -> dict | None:
    """Under 40% of carts reach checkout (7d, ≥20 carts)."""
    f = ctx.get("funnel_7d", {})
    carts = f.get("add_to_cart", 0)
    if carts < 20:
        return None
    checkouts = f.get("checkout_started", 0)
    rate = checkouts / carts
    if rate >= 0.40:
        return None
    aov = ctx.get("aov_cents", 0)
    lost = round((0.40 - rate) * carts)
    return {
        "severity": WARNING,
        "metrics": {"rate_pct": round(rate * 100, 1), "carts": carts},
        "impact_cents": lost * aov if aov else None,
    }


def _ca4_reachable_abandoned(ctx: dict) -> dict | None:
    """≥10 abandoned carts with contact info this week — recoverable."""
    count = ctx.get("abandoned_reachable_7d", 0)
    if count < 10:
        return None
    value = int(ctx.get("abandoned_value_7d_cents", 0) or 0)
    return {
        "severity": OPPORTUNITY,
        "metrics": {"count": count, "value_money": ctx["fmt"](value)},
        # Industry recovery ~10% of reachable cart value.
        "impact_cents": round(value * 0.10),
    }


# ── RC / PR: retention + pricing rules ─────────────────────────────


def _rc1_low_repeat(ctx: dict) -> dict | None:
    """Repeat-purchase rate under 15% over 90 days (≥30 customers)."""
    r = ctx.get("repeat", {})
    customers = r.get("customers", 0)
    if customers < 30:
        return None
    rate = r.get("repeat_customers", 0) / customers
    if rate >= 0.15:
        return None
    aov = ctx.get("aov_cents", 0)
    return {
        "severity": WARNING,
        "metrics": {
            "rate_pct": round(rate * 100, 1),
            "customers": customers,
        },
        # Bringing 10% of one-time buyers back once ≈ impact.
        "impact_cents": round(customers * 0.10 * aov) if aov else None,
    }


def _pr3_coupon_dependence(ctx: dict) -> dict | None:
    """>50% of the last 30 days' orders used a coupon (≥10 orders)."""
    orders = ctx.get("orders_30d", 0)
    if orders < 10:
        return None
    couponed = ctx.get("coupon_orders_30d", 0)
    share = couponed / orders
    if share <= 0.50:
        return None
    return {
        "severity": WARNING,
        "metrics": {"share_pct": round(share * 100), "orders": orders},
        "impact_cents": int(ctx.get("discounts_30d_cents", 0) or 0),
    }


# ── Registry ────────────────────────────────────────────────────────

RULES: list[Rule] = [
    Rule(
        "RV-1",
        "advice",
        _rv1_revenue_drop,
        {
            "en": "Sales last week were far below normal — {current_money} vs a typical {baseline_money} ({drop_pct}% drop).",
            "ar": "مبيعات الأسبوع اللي فات أقل من المعتاد بكتير — {current_money} بدل {baseline_money} (نزول {drop_pct}%).",
        },
        {
            "en": "Check what changed: traffic, checkout, stock of your best sellers.",
            "ar": "شوف إيه اللي اتغير: الزيارات، الدفع، أو مخزون الأكثر مبيعاً.",
        },
    ),
    Rule(
        "RV-2",
        "advice",
        _rv2_aov_declining,
        {
            "en": "Average order value has dropped 3 weeks in a row — now {aov_now_money}, was {aov_then_money}.",
            "ar": "متوسط قيمة الطلب بينزل 3 أسابيع ورا بعض — دلوقتي {aov_now_money} بدل {aov_then_money}.",
        },
        {
            "en": "Add a free-shipping threshold just above your AOV, or enable cart recommendations.",
            "ar": "حط حد شحن مجاني أعلى شوية من متوسط الطلب، أو فعّل اقتراحات السلة.",
        },
    ),
    Rule(
        "RV-3",
        "advice",
        _rv3_concentration,
        {
            "en": "{product_name} makes {share_pct}% of your revenue and it's slowing down.",
            "ar": "{product_name} بيعمل {share_pct}% من إيراداتك وبدأ يهدى.",
        },
        {
            "en": "Restock and promote it now, and start pushing your #2 and #3 products.",
            "ar": "اشحن مخزونه واعمله ترويج فوراً، وابدأ تدفع المنتجات التانية.",
        },
    ),
    Rule(
        "RV-4",
        "advice",
        _rv4_discount_creep,
        {
            "en": "Discounts are eating a bigger share of sales — {cur_pct}% this week vs {prev_pct}% last week.",
            "ar": "الخصومات بتاكل من مبيعاتك أكتر — {cur_pct}% الأسبوع ده بدل {prev_pct}%.",
        },
        {
            "en": "Review always-on coupons and cap stacking.",
            "ar": "راجع الكوبونات الشغالة على طول وحدد استخدامها.",
        },
    ),
    Rule(
        "LP-1",
        "advice",
        _lp1_dead_listing,
        {
            "en": "{count} stocked products haven't sold in a month — {value_money} sitting idle (e.g. {example}).",
            "ar": "{count} منتج متوفر ومباعش من شهر — {value_money} واقفة (مثال: {example}).",
        },
        {
            "en": "Discount them, bundle them with a best seller, or retire them.",
            "ar": "اعملهم خصم، أو اربطهم مع منتج بيبيع، أو شيلهم.",
        },
    ),
    Rule(
        "FG-1",
        "advice",
        _fg1_fast_seller_low_cover,
        {
            "en": "{product_name} is taking off (+{growth_pct}% this week) and you have ~{days_cover} days of stock left.",
            "ar": "{product_name} بيكسّر الدنيا (+{growth_pct}% الأسبوع ده) وفاضل عندك ~{days_cover} يوم مخزون.",
        },
        {
            "en": "Reorder now before you stock out mid-surge.",
            "ar": "اطلب مخزون دلوقتي قبل ما يخلص في عز البيع.",
        },
    ),
    Rule(
        "IV-1",
        "advice",
        _iv1_top_seller_low_cover,
        {
            "en": "Best seller {product_name} runs out in ~{days_cover} days.",
            "ar": "الأكثر مبيعاً {product_name} هيخلص خلال ~{days_cover} يوم.",
        },
        {
            "en": "Reorder about {suggested_qty} units to cover the next 3 weeks.",
            "ar": "اطلب حوالي {suggested_qty} قطعة تكفي 3 أسابيع جايين.",
        },
    ),
    Rule(
        "IV-2",
        "advice",
        _iv2_dead_stock_value,
        {
            "en": "{value_money} is frozen in products that haven't sold for 90+ days.",
            "ar": "{value_money} مجمدة في منتجات مباعتش من 90+ يوم.",
        },
        {
            "en": "Run a clearance collection to free up that capital.",
            "ar": "اعمل تصفية للمنتجات دي وحرر الفلوس.",
        },
    ),
    Rule(
        "IV-3",
        "advice",
        _iv3_oos_while_selling,
        {
            "en": "{product_name} is OUT OF STOCK while selling ~{daily_units}/day.",
            "ar": "{product_name} خلص من المخزون وهو بيبيع ~{daily_units} في اليوم.",
        },
        {
            "en": "Restock it — every day out costs you sales.",
            "ar": "اشحنه تاني — كل يوم من غيره بتخسر مبيعات.",
        },
    ),
    Rule(
        "CD-1",
        "advice",
        _cd1_rejection_rate,
        {
            "en": "{rate_pct}% of COD orders come back refused ({rejected} of {total}) — you pay shipping both ways.",
            "ar": "{rate_pct}% من طلبات الكاش بترجع مرفوضة ({rejected} من {total}) — وانت بتدفع الشحن رايح جاي.",
        },
        {
            "en": "Confirm orders on WhatsApp before shipping, or nudge customers to prepay.",
            "ar": "أكد الطلبات على واتساب قبل الشحن، أو شجع الدفع المسبق.",
        },
    ),
    Rule(
        "CD-2",
        "advice",
        _cd2_governorate_hotspot,
        {
            "en": "Deliveries to {governorate} fail at {rate_pct}% — far above your {avg_pct}% average.",
            "ar": "التوصيل لـ{governorate} بيفشل بنسبة {rate_pct}% — أعلى بكتير من متوسطك {avg_pct}%.",
        },
        {
            "en": "Require a deposit or prepayment for that governorate, or try a different courier there.",
            "ar": "اطلب عربون أو دفع مسبق للمحافظة دي، أو جرب شركة شحن تانية هناك.",
        },
    ),
    Rule(
        "CA-1",
        "advice",
        _ca1_cart_to_checkout,
        {
            "en": "Only {rate_pct}% of carts reach checkout this week ({carts} carts).",
            "ar": "{rate_pct}% بس من السلل بتوصل للدفع الأسبوع ده ({carts} سلة).",
        },
        {
            "en": "Show shipping costs earlier and simplify the path to checkout.",
            "ar": "اعرض تكلفة الشحن بدري وسهّل الوصول للدفع.",
        },
    ),
    Rule(
        "CA-4",
        "opportunity",
        _ca4_reachable_abandoned,
        {
            "en": "{count} abandoned carts this week left a phone or email — {value_money} recoverable.",
            "ar": "{count} سلة متروكة الأسبوع ده سابوا رقم أو إيميل — {value_money} ممكن ترجع.",
        },
        {
            "en": "Send a recovery message — stores typically win back ~10% of reachable carts.",
            "ar": "ابعت رسالة استرجاع — المتاجر عادة بترجّع ~10% من السلل دي.",
        },
    ),
    Rule(
        "RC-1",
        "advice",
        _rc1_low_repeat,
        {
            "en": "Only {rate_pct}% of your {customers} customers came back for a second order.",
            "ar": "{rate_pct}% بس من عملائك الـ{customers} رجعوا يشتروا تاني.",
        },
        {
            "en": "Set up a post-purchase follow-up with a second-order coupon.",
            "ar": "اعمل متابعة بعد الشراء مع كوبون للطلب التاني.",
        },
    ),
    Rule(
        "PR-3",
        "advice",
        _pr3_coupon_dependence,
        {
            "en": "{share_pct}% of orders used a coupon — customers may be learning to never pay full price.",
            "ar": "{share_pct}% من الطلبات استخدمت كوبون — عملاؤك ممكن يتعودوا مايدفعوش السعر الكامل.",
        },
        {
            "en": "Shorten coupon windows or switch to threshold offers (spend X, get Y).",
            "ar": "قصّر مدة الكوبونات أو حوّل لعروض حد أدنى (اشتري بـX وخد Y).",
        },
    ),
]

RULES_BY_ID: dict[str, Rule] = {r.rule_id: r for r in RULES}

# ── OP: opportunity detectors (AI-4) ───────────────────────────────


def _op_bundle(ctx: dict) -> dict | None:
    """Best product pair with lift ≥ 3 — a natural bundle."""
    basket = ctx.get("basket") or {}
    total = basket.get("total_orders", 0)
    if total < 10:
        return None
    names = ctx.get("product_names", {})
    per = basket.get("product_orders", {})
    best = None
    for pair in basket.get("pairs", []):
        a_o, b_o = per.get(pair["a_id"], 0), per.get(pair["b_id"], 0)
        if a_o == 0 or b_o == 0:
            continue
        lift = (pair["pair_orders"] * total) / (a_o * b_o)
        if lift >= 3 and (best is None or lift > best[0]):
            best = (lift, pair)
    if best is None:
        return None
    lift, pair = best
    return {
        "severity": OPPORTUNITY,
        "metrics": {
            "product_a": names.get(pair["a_id"], "(unnamed)"),
            "product_b": names.get(pair["b_id"], "(unnamed)"),
            "lift": round(lift, 1),
            "pair_orders": pair["pair_orders"],
        },
        "impact_cents": pair["pair_orders"] * ctx.get("aov_cents", 0),
    }


def _op_ads_ready(ctx: dict) -> dict | None:
    """A proven seller with margin headroom and stock to scale — a
    low-risk first paid-ads candidate."""
    stock = {p["product_id"]: p for p in ctx.get("stock", [])}
    for p in sorted(
        ctx.get("products_28d", []), key=lambda x: x["revenue_cents"], reverse=True
    ):
        if p["units_sold"] < 10:
            continue  # revenue order does not imply units order
        st = stock.get(p["product_id"])
        if not st or st.get("cost_cents") is None or st["price_cents"] <= 0:
            continue
        margin = (st["price_cents"] - st["cost_cents"]) / st["price_cents"]
        if margin < 0.40:
            continue
        velocity = p["units_sold"] / 28
        if velocity <= 0 or st["quantity"] / velocity < 21:
            continue
        return {
            "severity": OPPORTUNITY,
            "metrics": {
                "product_name": st["name"] or "(unnamed)",
                "margin_pct": round(margin * 100),
                "units": p["units_sold"],
            },
            "impact_cents": None,
        }
    return None


def _op_due_customers(ctx: dict) -> dict | None:
    """Customers inside their personal reorder window right now."""
    count = ctx.get("due_customers", 0)
    if count < 3:
        return None
    aov = ctx.get("aov_cents", 0)
    return {
        "severity": OPPORTUNITY,
        "metrics": {"count": count},
        # Conservative: ~20% of due customers reorder when nudged.
        "impact_cents": round(count * 0.2 * aov) if aov else None,
    }


OPPORTUNITY_RULES: list[Rule] = [
    Rule(
        "OP-BUNDLE",
        "opportunity",
        _op_bundle,
        {
            "en": "{product_a} + {product_b} are bought together {lift}x more than chance ({pair_orders} orders).",
            "ar": "{product_a} + {product_b} بيتشتروا مع بعض {lift} ضعف الصدفة ({pair_orders} طلب).",
        },
        {
            "en": "Create a bundle with a small discount — it sells itself.",
            "ar": "اعملهم باقة بخصم بسيط — هتبيع نفسها.",
        },
    ),
    Rule(
        "OP-ADS-READY",
        "opportunity",
        _op_ads_ready,
        {
            "en": "{product_name} is ad-ready: {units} sold organically at {margin_pct}% margin with stock to scale.",
            "ar": "{product_name} جاهز للإعلانات: {units} مبيعة أورجانيك بهامش {margin_pct}% ومخزون يكفي.",
        },
        {
            "en": "Test a small Meta or TikTok campaign on it this week.",
            "ar": "جرب عليه حملة صغيرة على ميتا أو تيك توك الأسبوع ده.",
        },
    ),
    Rule(
        "OP-DUE",
        "opportunity",
        _op_due_customers,
        {
            "en": "{count} customers are due for their next order right now (based on their own buying rhythm).",
            "ar": "{count} عميل معاد طلبهم الجاي دلوقتي (حسب إيقاع شراء كل واحد).",
        },
        {
            "en": "Message them today — a simple 'we miss you' with their favorites works.",
            "ar": "ابعتلهم النهارده — رسالة بسيطة بمنتجاتهم المفضلة بتجيب نتيجة.",
        },
    ),
]

RULES.extend(OPPORTUNITY_RULES)
RULES_BY_ID.update({r.rule_id: r for r in OPPORTUNITY_RULES})


def run_rules(ctx: dict) -> list[dict]:
    """Evaluate every rule; a rule that crashes is skipped (fail-open),
    never able to take down the sweep or block its siblings."""
    fired: list[dict] = []
    for rule in RULES:
        try:
            result = rule.check(ctx)
        except Exception:  # noqa: BLE001 — one bad rule must not stop the rest
            continue
        if result:
            fired.append({
                "rule_id": rule.rule_id,
                "kind": rule.kind,
                "severity": result["severity"],
                "metrics": result.get("metrics", {}),
                "impact_cents": result.get("impact_cents"),
            })
    return fired


def render_signal(rule_id: str, metrics: dict[str, Any], lang: str) -> dict[str, str]:
    """Render the bilingual title/action for a stored signal.

    Missing placeholders render as-is rather than raising — an old
    snapshot must never 500 the feed after a template gains a field.
    """
    rule = RULES_BY_ID.get(rule_id)
    if not rule:
        return {"title": rule_id, "action": ""}
    lang = lang if lang in rule.title else "en"

    class _Safe(dict):
        def __missing__(self, key):  # noqa: D105
            return "{" + key + "}"

    safe = _Safe(**(metrics or {}))
    return {
        "title": rule.title[lang].format_map(safe),
        "action": rule.action[lang].format_map(safe),
    }
