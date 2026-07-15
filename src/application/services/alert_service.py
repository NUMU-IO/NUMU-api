"""Smart Alerts — the hourly, time-critical rule subset (AI-2).

Where the nightly Advisor reacts to trends, alerts catch things that
can't wait for tomorrow: a sales spike in progress, yesterday's revenue
collapsing vs its weekday norm, COD rejections or refunds spiking, an
abandoned-cart surge, a top seller about to stock out.

Anti-noise design:
- Redis cooldown key per (store, alert) — a re-fire inside the window
  refreshes the signal row but does NOT reset its freshness or notify.
- Same-weekday baselines (Monday compares to Mondays) built from the
  daily rollups, so Egyptian weekend rhythms don't false-positive.
- Minimum denominators everywhere.

Alerts persist as ``merchant_signals`` rows with ``kind='alert'`` — the
same feed/executive-dashboard surfaces read them; the one-active-per-
rule index applies. Copy templates live in advisor_rules-style form
below and render at read time via a shared registry hook.
"""

from __future__ import annotations

import statistics
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.utils.store_timezone import safe_zone

ALERT_COOLDOWNS_H = {
    "AL-SPIKE": 6,
    "AL-REV-DROP": 48,
    "AL-COD-SPIKE": 168,
    "AL-REFUND-SPIKE": 168,
    "AL-ABANDON-SURGE": 24,
    "AL-STOCKOUT": 72,
}

# Bilingual templates, advisor-style ({placeholders} from the snapshot).
ALERT_TEMPLATES: dict[str, dict[str, dict[str, str]]] = {
    "AL-SPIKE": {
        "title": {
            "en": "You're having an unusually strong day — {orders} orders so far (typical {baseline}).",
            "ar": "يومك أقوى من المعتاد — {orders} طلب لحد دلوقتي (المعتاد {baseline}).",
        },
        "action": {
            "en": "Check stock on your best sellers and make sure your courier can handle the volume.",
            "ar": "اتأكد من مخزون الأكثر مبيعاً وإن شركة الشحن تقدر تستوعب الكمية.",
        },
    },
    "AL-REV-DROP": {
        "title": {
            "en": "Yesterday's revenue was far below a normal {weekday} — {revenue_money} vs typical {baseline_money}.",
            "ar": "إيرادات إمبارح أقل بكتير من أي {weekday} عادي — {revenue_money} بدل {baseline_money}.",
        },
        "action": {
            "en": "Test your checkout end-to-end and check whether traffic or conversion dropped.",
            "ar": "جرب الدفع بنفسك وشوف الزيارات ولا التحويل اللي نزل.",
        },
    },
    "AL-COD-SPIKE": {
        "title": {
            "en": "COD rejections jumped to {rate_pct}% this week (usually {baseline_pct}%).",
            "ar": "رفض الكاش وصل {rate_pct}% الأسبوع ده (المعتاد {baseline_pct}%).",
        },
        "action": {
            "en": "Confirm orders on WhatsApp before shipping this week.",
            "ar": "أكد الطلبات على واتساب قبل الشحن الأسبوع ده.",
        },
    },
    "AL-REFUND-SPIKE": {
        "title": {
            "en": "{refunds} refunds this week — about {ratio}x your normal rate.",
            "ar": "{refunds} استرجاع الأسبوع ده — حوالي {ratio} ضعف المعتاد.",
        },
        "action": {
            "en": "Open the refunded orders and look for a shared product or courier.",
            "ar": "افتح الطلبات المسترجعة ودور على منتج أو شركة شحن مشتركة.",
        },
    },
    "AL-ABANDON-SURGE": {
        "title": {
            "en": "{count} carts were abandoned in the last 24h — well above your usual pace.",
            "ar": "{count} سلة اتساب في آخر 24 ساعة — أعلى بكتير من معدلك.",
        },
        "action": {
            "en": "Check checkout for errors, then send recovery messages to reachable carts.",
            "ar": "اتأكد إن الدفع شغال، وبعدين ابعت رسائل استرجاع للسلل اللي سابت بيانات.",
        },
    },
    "AL-STOCKOUT": {
        "title": {
            "en": "URGENT: {product_name} has ~{days_cover} days of stock at current pace.",
            "ar": "عاجل: {product_name} فاضل له ~{days_cover} يوم مخزون بالمعدل الحالي.",
        },
        "action": {
            "en": "Reorder today — a stockout mid-demand costs the most.",
            "ar": "اطلب مخزون النهارده — نفاده وقت الطلب العالي أغلى خسارة.",
        },
    },
}


async def build_alert_context(
    session: AsyncSession, store_id: UUID, tz_name: str, currency: str
) -> dict:
    """Cheap hourly context — rollups + a few narrow queries."""
    from src.infrastructure.cache.realtime_counters import get_snapshot
    from src.infrastructure.database.models.tenant.abandoned_checkout import (
        AbandonedCheckoutModel,
    )
    from src.infrastructure.repositories.analytics_repository import (
        AnalyticsRepository,
    )
    from src.infrastructure.repositories.analytics_rollup_repository import (
        AnalyticsRollupRepository,
    )

    analytics = AnalyticsRepository(session)
    rollups = AnalyticsRollupRepository(session)
    now = datetime.now(UTC)
    today_local = now.astimezone(safe_zone(tz_name)).date()

    def fmt(cents: int) -> str:
        return f"{cents / 100:,.2f} {currency}"

    daily = await rollups.get_range(
        store_id, today_local - timedelta(days=90), today_local - timedelta(days=1)
    )
    by_date = {r.rollup_date: r for r in daily or []}

    snapshot = await get_snapshot(store_id)

    abandoned_24h = (
        await session.execute(
            select(func.count()).where(
                AbandonedCheckoutModel.store_id == store_id,
                AbandonedCheckoutModel.last_activity_at >= now - timedelta(hours=24),
            )
        )
    ).scalar() or 0

    cod_7d = await analytics.cod_summary(store_id, now - timedelta(days=7), now)
    cod_90d = await analytics.cod_summary(store_id, now - timedelta(days=90), now)

    p7 = await analytics.product_sales_window(store_id, now - timedelta(days=7), now)
    stock = await analytics.product_stock_snapshot(store_id)

    return {
        "fmt": fmt,
        "today_local": today_local,
        "daily_by_date": by_date,
        "orders_today": int(snapshot.get("orders_today", 0) or 0),
        "abandoned_24h": int(abandoned_24h),
        "cod_7d": cod_7d,
        "cod_90d": cod_90d,
        "products_7d": p7,
        "stock": {p["product_id"]: p for p in stock},
    }


def _same_weekday_series(ctx: dict, day_offset: int, weeks: int = 4) -> list[int]:
    """Revenue/orders for the same weekday over prior ``weeks`` weeks."""
    base = ctx["today_local"] - timedelta(days=day_offset)
    out = []
    for w in range(1, weeks + 1):
        r = ctx["daily_by_date"].get(base - timedelta(days=7 * w))
        if r is not None:
            out.append((int(r.total_revenue_cents), int(r.total_orders)))
    return out


def evaluate_alerts(ctx: dict) -> list[dict]:
    """Pure evaluation → [{rule_id, severity, metrics, impact_cents}]."""
    fired: list[dict] = []

    # AL-SPIKE — today's orders way above the same-weekday norm.
    baseline = [o for _, o in _same_weekday_series(ctx, 0)]
    if len(baseline) >= 3 and ctx["orders_today"] >= 5:
        mean = statistics.mean(baseline)
        std = statistics.pstdev(baseline) or 1.0
        if ctx["orders_today"] > mean + 3 * std and ctx["orders_today"] > mean * 1.5:
            fired.append({
                "rule_id": "AL-SPIKE",
                "severity": "opportunity",
                "metrics": {"orders": ctx["orders_today"], "baseline": round(mean)},
                "impact_cents": None,
            })

    # AL-REV-DROP — yesterday under its weekday norm by ≥2σ.
    yesterday = ctx["daily_by_date"].get(ctx["today_local"] - timedelta(days=1))
    ybase = _same_weekday_series(ctx, 1)
    if yesterday is not None and len(ybase) >= 3:
        revs = [r for r, _ in ybase]
        mean = statistics.mean(revs)
        std = statistics.pstdev(revs)
        yrev = int(yesterday.total_revenue_cents)
        if mean >= 50_00 and std > 0 and yrev < mean - 2 * std:
            wd = (ctx["today_local"] - timedelta(days=1)).strftime("%A")
            fired.append({
                "rule_id": "AL-REV-DROP",
                "severity": "critical",
                "metrics": {
                    "revenue_cents": yrev,
                    "baseline_cents": round(mean),
                    "weekday": wd,
                },
                "impact_cents": round(mean) - yrev,
            })

    # AL-COD-SPIKE — 7d rejection rate ≥ 90d rate + 10pts.
    c7, c90 = ctx["cod_7d"], ctx["cod_90d"]
    if c7.get("total", 0) >= 10 and c90.get("total", 0) >= 20:
        r7 = c7["rejected"] / c7["total"]
        r90 = c90["rejected"] / c90["total"]
        if r7 - r90 >= 0.10:
            fired.append({
                "rule_id": "AL-COD-SPIKE",
                "severity": "critical",
                "metrics": {
                    "rate_pct": round(r7 * 100, 1),
                    "baseline_pct": round(r90 * 100, 1),
                },
                "impact_cents": int(c7.get("rejected_amount", 0) or 0),
            })

    # AL-REFUND-SPIKE — 7d refunds ≥ 2× the 90d weekly average (≥5).
    daily = list(ctx["daily_by_date"].values())
    ref_7d = sum(
        int(r.refund_count or 0)
        for r in daily
        if r.rollup_date >= ctx["today_local"] - timedelta(days=7)
    )
    ref_90d = sum(int(r.refund_count or 0) for r in daily)
    weekly_avg = ref_90d / 13 if ref_90d else 0
    if ref_7d >= 5 and weekly_avg > 0 and ref_7d >= 2 * weekly_avg:
        fired.append({
            "rule_id": "AL-REFUND-SPIKE",
            "severity": "warning",
            "metrics": {"refunds": ref_7d, "ratio": round(ref_7d / weekly_avg, 1)},
            "impact_cents": None,
        })

    # AL-ABANDON-SURGE — rollups don't carry abandonment history, so the
    # v1 gate is relative to today's sales: ≥5 abandoned in 24h AND at
    # least 3× today's completed orders (a surge, not normal browsing).
    if ctx["abandoned_24h"] >= max(3 * max(ctx["orders_today"], 1), 5):
        fired.append({
            "rule_id": "AL-ABANDON-SURGE",
            "severity": "warning",
            "metrics": {"count": ctx["abandoned_24h"]},
            "impact_cents": None,
        })

    # AL-STOCKOUT — any 7d-selling product with <5 days of cover.
    for p in ctx["products_7d"]:
        st = ctx["stock"].get(p["product_id"])
        if not st or st["quantity"] <= 0 or p["units_sold"] < 3:
            continue
        velocity = p["units_sold"] / 7
        cover = st["quantity"] / velocity
        if cover < 5:
            unit_rev = p["revenue_cents"] / p["units_sold"]
            fired.append({
                "rule_id": "AL-STOCKOUT",
                "severity": "critical",
                "metrics": {
                    "product_name": st["name"] or "(unnamed)",
                    "days_cover": round(cover, 1),
                },
                "impact_cents": round(velocity * 7 * unit_rev),
            })
            break  # one stockout alert per sweep is enough

    return fired


async def run_store_alerts(
    session: AsyncSession,
    store_id: UUID,
    tenant_id: UUID,
    tz_name: str,
    currency: str,
) -> dict:
    """Evaluate alerts and reconcile the AL-* signal rows for one store.

    Redis cooldown key ``alert:cd:{store}:{rule}`` (SETEX per-alert TTL)
    suppresses re-CREATION noise: while cooling, a still-true alert only
    refreshes its existing row. Redis being down degrades to
    row-existence dedup (the one-active-per-rule index), never a crash.
    """
    from src.infrastructure.database.models.tenant.merchant_signal import (
        MerchantSignalModel,
    )

    ctx = await build_alert_context(session, store_id, tz_name, currency)
    fired = evaluate_alerts(ctx)
    now = datetime.now(UTC)

    active_rows = (
        (
            await session.execute(
                select(MerchantSignalModel).where(
                    MerchantSignalModel.store_id == store_id,
                    MerchantSignalModel.status == "active",
                    MerchantSignalModel.rule_id.like("AL-%"),
                )
            )
        )
        .scalars()
        .all()
    )
    active_by_rule = {r.rule_id: r for r in active_rows}
    fired_ids = {f["rule_id"] for f in fired}

    async def _cooling(rule_id: str) -> bool:
        try:
            from src.infrastructure.cache.redis_cache import RedisCacheService

            client = await RedisCacheService()._get_client()
            return bool(await client.exists(f"alert:cd:{store_id}:{rule_id}"))
        except Exception:
            return False

    async def _set_cooldown(rule_id: str) -> None:
        try:
            from src.infrastructure.cache.redis_cache import RedisCacheService

            client = await RedisCacheService()._get_client()
            ttl_h = ALERT_COOLDOWNS_H.get(rule_id, 24)
            await client.setex(f"alert:cd:{store_id}:{rule_id}", ttl_h * 3600, "1")
        except Exception:
            pass

    created = refreshed = resolved = suppressed = 0
    for f in fired:
        metrics = dict(f["metrics"])
        for key, value in list(metrics.items()):
            if key.endswith("_cents") and isinstance(value, int | float):
                metrics[key[: -len("_cents")] + "_money"] = ctx["fmt"](int(value))
        existing = active_by_rule.get(f["rule_id"])
        if existing is not None:
            existing.metrics_snapshot = metrics
            existing.severity = f["severity"]
            existing.expected_impact_cents = f["impact_cents"]
            refreshed += 1
            continue
        if await _cooling(f["rule_id"]):
            suppressed += 1
            continue
        ttl_h = ALERT_COOLDOWNS_H.get(f["rule_id"], 24)
        session.add(
            MerchantSignalModel(
                tenant_id=tenant_id,
                store_id=store_id,
                kind="alert",
                rule_id=f["rule_id"],
                severity=f["severity"],
                status="active",
                metrics_snapshot=metrics,
                expected_impact_cents=f["impact_cents"],
                cooldown_until=now + timedelta(hours=ttl_h),
            )
        )
        await _set_cooldown(f["rule_id"])
        created += 1

    # Hysteresis-lite: an alert whose condition cleared resolves itself.
    for rule_id, row in active_by_rule.items():
        if rule_id not in fired_ids:
            row.status = "resolved"
            resolved += 1

    await session.flush()
    return {
        "fired": len(fired),
        "created": created,
        "refreshed": refreshed,
        "resolved": resolved,
        "suppressed": suppressed,
    }


def render_alert(rule_id: str, metrics: dict, lang: str) -> dict[str, str]:
    """Render an alert's bilingual copy (advisor-compatible signature)."""
    tpl = ALERT_TEMPLATES.get(rule_id)
    if not tpl:
        return {"title": rule_id, "action": ""}
    lang = lang if lang in tpl["title"] else "en"

    class _Safe(dict):
        def __missing__(self, key):  # noqa: D105
            return "{" + key + "}"

    safe = _Safe(**(metrics or {}))
    return {
        "title": tpl["title"][lang].format_map(safe),
        "action": tpl["action"][lang].format_map(safe),
    }
