"""Intelligence engine — builds the per-store MetricsContext and runs
the advisor rules against it, persisting results as merchant_signals.

One data pass per store per night: the context gathers everything the
rule registry needs (weekly aggregates, product sales windows, stock,
COD outcomes, funnel counts, abandoned carts, repeat rate, coupon
share), then ``run_store`` evaluates the rules and reconciles the
signals table:

- new firing rule            → insert active signal
- still-firing active signal → refresh snapshot/impact (no re-notify)
- active signal not firing   → auto-resolve

All zero-API: SQL + arithmetic. Fail-open per store.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.advisor_rules import run_rules
from src.core.utils.store_timezone import safe_zone
from src.infrastructure.database.models.tenant.abandoned_checkout import (
    AbandonedCheckoutModel,
)
from src.infrastructure.database.models.tenant.merchant_signal import (
    MerchantSignalModel,
)
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.repositories.analytics_repository import AnalyticsRepository
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)

# Re-fires within this window refresh the row silently.
_DEFAULT_COOLDOWN_DAYS = 7
# Anything the merchant hasn't acted on in this long stops cluttering
# the feed.
_EXPIRE_AFTER_DAYS = 30


async def build_context(
    session: AsyncSession,
    store_id: UUID,
    tz_name: str,
    currency: str,
) -> dict:
    """Gather every metric the rule registry reads. One pass, ~10 queries."""
    analytics = AnalyticsRepository(session)
    funnel = FunnelEventRepository(session)

    now = datetime.now(UTC)
    now_local = now.astimezone(safe_zone(tz_name))

    def fmt(cents: int) -> str:
        return f"{cents / 100:,.2f} {currency}"

    # ── Weekly aggregates (8 weeks) ──
    weekly_rows = await analytics.weekly_order_aggregates(
        store_id, now - timedelta(days=56), now, tz=tz_name
    )
    # newest first; index 0 = current (partial) week
    weekly = list(reversed(weekly_rows))

    # ── Product sales windows ──
    p28 = await analytics.product_sales_window(store_id, now - timedelta(days=28), now)
    p7 = await analytics.product_sales_window(store_id, now - timedelta(days=7), now)
    p_prev7 = await analytics.product_sales_window(
        store_id, now - timedelta(days=14), now - timedelta(days=7)
    )

    stock = await analytics.product_stock_snapshot(store_id)
    product_names = {p["product_id"]: p["name"] for p in stock}

    # Dead-stock (90d) value: in-stock products with no sale in 90 days.
    last_sold = await analytics.product_last_sold(store_id, now - timedelta(days=90))
    dead_value = sum(
        p["quantity"] * p["unit_value_cents"]
        for p in stock
        if p["quantity"] > 0 and p["product_id"] not in last_sold
    )

    # ── COD outcomes (30d) ──
    cod = await analytics.cod_summary(store_id, now - timedelta(days=30), now)
    cod_by_gov = await analytics.cod_rejections_by_location(
        store_id, now - timedelta(days=30), now
    )

    # ── Funnel counts (7d) ──
    funnel_7d = await funnel.get_funnel_counts(store_id, now - timedelta(days=7), now)

    # ── Abandoned carts with contact info (7d, not recovered) ──
    reachable_q = select(
        func.count().label("n"),
        func.coalesce(func.sum(AbandonedCheckoutModel.total), 0).label("value"),
    ).where(
        AbandonedCheckoutModel.store_id == store_id,
        AbandonedCheckoutModel.last_activity_at >= now - timedelta(days=7),
        AbandonedCheckoutModel.recovered_at.is_(None),
        (AbandonedCheckoutModel.email.isnot(None))
        | (AbandonedCheckoutModel.phone.isnot(None)),
    )
    reachable = (await session.execute(reachable_q)).one()

    # ── Repeat rate (90d) ──
    cust = await analytics.customer_period_aggregates(
        store_id, now - timedelta(days=90), now
    )
    repeat = {
        "customers": len(cust),
        "repeat_customers": sum(1 for c in cust if c["orders"] >= 2),
    }

    # ── Coupon share + AOV (30d) ──
    orders_q = select(
        func.count().label("orders"),
        func.count().filter(OrderModel.coupon_code.isnot(None)).label("couponed"),
        func.coalesce(func.sum(OrderModel.total), 0).label("revenue"),
        func.coalesce(func.sum(OrderModel.discount_amount), 0).label("discounts"),
    ).where(*analytics._store_window(store_id, now - timedelta(days=30), now))
    orow = (await session.execute(analytics._tenant_filter(orders_q))).one()
    orders_30d = int(orow.orders or 0)

    return {
        "now_local": now_local,
        "fmt": fmt,
        "weekly": weekly,
        "products_28d": p28,
        "products_7d": {p["product_id"]: p for p in p7},
        "products_prev_7d": {p["product_id"]: p for p in p_prev7},
        "product_names": product_names,
        "stock": stock,
        "dead_stock_90_value_cents": dead_value,
        "cod": cod,
        "cod_by_gov": cod_by_gov,
        "funnel_7d": funnel_7d,
        "abandoned_reachable_7d": int(reachable.n or 0),
        "abandoned_value_7d_cents": int(reachable.value or 0),
        "repeat": repeat,
        "orders_30d": orders_30d,
        "coupon_orders_30d": int(orow.couponed or 0),
        "discounts_30d_cents": int(orow.discounts or 0),
        "aov_cents": int(orow.revenue or 0) // orders_30d if orders_30d else 0,
    }


def _enrich_money(metrics: dict, fmt) -> dict:
    """Add a formatted ``*_money`` twin for every ``*_cents`` metric so
    templates can show money without storing locale-specific strings for
    the numeric audit trail."""
    out = dict(metrics)
    for key, value in list(metrics.items()):
        if key.endswith("_cents") and isinstance(value, int | float):
            out[key[: -len("_cents")] + "_money"] = fmt(int(value))
    return out


async def run_store(
    session: AsyncSession,
    store_id: UUID,
    tenant_id: UUID,
    tz_name: str,
    currency: str,
) -> dict:
    """Build context, evaluate rules, reconcile merchant_signals rows."""
    ctx = await build_context(session, store_id, tz_name, currency)
    fired = run_rules(ctx)
    now = datetime.now(UTC)

    rows = (
        (
            await session.execute(
                select(MerchantSignalModel).where(
                    MerchantSignalModel.store_id == store_id,
                    MerchantSignalModel.status.in_(["active", "dismissed"]),
                )
            )
        )
        .scalars()
        .all()
    )
    # Reconcile ONLY the advisor's own namespace — hourly alerts (AL-*)
    # run on a different cadence and must not be auto-resolved by a
    # nightly pass that never evaluates them.
    from src.application.services.advisor_rules import RULES_BY_ID

    active_by_rule = {
        r.rule_id: r for r in rows if r.rule_id in RULES_BY_ID and r.status == "active"
    }
    # A merchant's dismissal is respected while its cooldown holds — a
    # still-true condition doesn't resurrect the signal the next night.
    dismissed_cooling = {
        r.rule_id
        for r in rows
        if r.status == "dismissed"
        and r.cooldown_until is not None
        and r.cooldown_until > now
    }
    fired_ids = {f["rule_id"] for f in fired}

    created = refreshed = resolved = expired = 0

    for f in fired:
        if f["rule_id"] in dismissed_cooling and f["rule_id"] not in active_by_rule:
            continue
        metrics = _enrich_money(f["metrics"], ctx["fmt"])
        existing = active_by_rule.get(f["rule_id"])
        if existing is not None:
            existing.metrics_snapshot = metrics
            existing.severity = f["severity"]
            existing.expected_impact_cents = f["impact_cents"]
            refreshed += 1
        else:
            session.add(
                MerchantSignalModel(
                    tenant_id=tenant_id,
                    store_id=store_id,
                    kind=f["kind"],
                    rule_id=f["rule_id"],
                    severity=f["severity"],
                    status="active",
                    metrics_snapshot=metrics,
                    expected_impact_cents=f["impact_cents"],
                    cooldown_until=now + timedelta(days=_DEFAULT_COOLDOWN_DAYS),
                )
            )
            created += 1

    for rule_id, row in active_by_rule.items():
        if rule_id in fired_ids:
            continue
        if (now - row.created_at).days >= _EXPIRE_AFTER_DAYS:
            row.status = "expired"
            expired += 1
        else:
            row.status = "resolved"
            resolved += 1

    await session.flush()
    return {
        "fired": len(fired),
        "created": created,
        "refreshed": refreshed,
        "resolved": resolved,
        "expired": expired,
    }
