"""The admin overview screen, in one request.

URL: /api/v1/admin/dashboard/overview — requires SUPER_ADMIN.

The overview is a triage screen: ten numbers, two monitoring charts, the
orders that need a human, and what staff and the system have been doing. It
is one endpoint rather than ten because it is read on a loop and every tile
shares the same demo/internal exclusion — split across ten calls, the tiles
would disagree with each other whenever a tenant flipped mid-refresh.

Everything here is computed from tables that already exist: orders,
risk_assessments, webhook_delivery_logs, audit_logs, support_cases, tenants
and stores. The one thing the design calls for that this platform still has
no source for is a named incident record, so the health bar reports derived
signals instead of pretending to have incidents.
"""

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.plan import PLAN_LIMITS

logger = logging.getLogger(__name__)

router = APIRouter()

# Every aggregate on this screen excludes demo tenants and NUMU's own
# internal ones, exactly as /admin/dashboard/stats does. Written once here
# and interpolated, because repeating it in nine queries is how two tiles end
# up counting different populations.
_LIVE_TENANTS = """
    NOT EXISTS (
        SELECT 1 FROM public.tenants x
        WHERE x.id = {alias}.tenant_id
          AND (x.lifecycle_state = 'demo' OR x.is_internal IS TRUE)
    )
"""


class Trend(BaseModel):
    """A number, how it moved, and the shape behind it."""

    value: float
    delta: float | None = None
    #: "count" compares absolute, "pct" compares percentage, "pp" is a
    #: percentage-point move on a metric that is itself a percentage.
    delta_unit: str = "count"
    spark: list[float] = []
    note: str | None = None


class OverviewMetrics(BaseModel):
    active_merchants: Trend
    active_stores: Trend
    orders_today: Trend
    order_value_today: Trend
    trial_to_paid_30d: Trend
    failed_payments_24h: Trend
    stores_requiring_review: Trend
    high_risk_cod_orders: Trend
    open_support_cases: Trend
    failed_jobs_webhooks: Trend


class Earnings(BaseModel):
    """What NUMU itself earns, as opposed to what merchants turn over.

    The distinction the tiles have to keep straight: a wallet top-up is the
    merchant's money sitting on the platform — a liability, not income. It
    becomes NUMU revenue only when it is consumed as commission. Both are
    reported, labelled for what they are, and only the earned half is summed
    into ``recognised_30d``.
    """

    #: Monthly recurring revenue, annual plans at their monthly equivalent.
    mrr_cents: int
    mrr_subscribers: int
    mrr_by_plan: dict[str, int]
    #: Paying tenants by plan key, so "paid plans" is a real count.
    plan_counts: dict[str, int]
    #: Subscription receipts approved in the window.
    subscriptions_collected_30d: int
    #: Net pay-as-you-go commission: charges less reversals.
    payg_commission_30d: int
    #: Cash the wallets took in. NOT revenue — see the class docstring.
    wallet_topups_collected_30d: int
    #: Merchant money currently held. A liability NUMU owes back in service.
    wallet_float_cents: int
    #: subscriptions_collected_30d + payg_commission_30d.
    recognised_30d: int


class Bucket(BaseModel):
    label: str
    value: float
    #: Out of tolerance for this series — the UI paints it Terracotta.
    highlight: bool = False


class AttentionOrder(BaseModel):
    id: str
    order_number: str
    store_name: str
    store_id: str
    tenant_id: str | None
    customer_name: str | None
    customer_phone: str | None
    payment_method: str | None
    status: str
    payment_status: str
    total_cents: int
    currency: str
    risk_score: int | None
    risk_level: str | None
    created_at: datetime


class ActivityEntry(BaseModel):
    id: str
    action: str
    actor: str | None
    actor_type: str
    entity: str | None
    note: str | None
    severity: str
    created_at: datetime


#: How far back the orders chart reaches. Two weeks: long enough that a bad
#: day is visibly a dip rather than the whole chart, short enough that a bar
#: is still one readable day.
ORDERS_CHART_DAYS = 14


class HealthSignal(BaseModel):
    """A degradation derived from measurements, not from an incident record."""

    id: str
    severity: str
    title: str
    detail: str


class OverviewResponse(BaseModel):
    generated_at: datetime
    health: list[HealthSignal]
    metrics: OverviewMetrics
    earnings: Earnings
    orders_per_hour: list[Bucket]
    webhook_failures_per_hour: list[Bucket]
    orders_needing_attention: list[AttentionOrder]
    activity: list[ActivityEntry]


async def _scalar(db: AsyncSession, sql: str, **params: Any) -> Any:
    return (await db.execute(text(sql), params)).scalar()


@router.get(
    "/overview",
    response_model=SuccessResponse[OverviewResponse],
    summary="Everything the admin overview screen renders",
    operation_id="admin_dashboard_overview",
)
async def get_overview(
    _admin_id: Annotated[UUID, Depends(require_admin)],
    db: Annotated[AsyncSession, Depends(get_db)],
):
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    orders_live = _LIVE_TENANTS.format(alias="o")
    stores_live = _LIVE_TENANTS.format(alias="s")

    # ── Merchants and stores, with a 7-day signup shape ─────────────────────
    active_merchants = await _scalar(
        db,
        """
        SELECT count(*) FROM public.tenants t
        WHERE t.is_active IS TRUE
          AND t.is_internal IS FALSE
          AND t.lifecycle_state <> 'demo'
        """,
    )
    merchants_new_7d = await _scalar(
        db,
        """
        SELECT count(*) FROM public.tenants t
        WHERE t.is_active IS TRUE AND t.is_internal IS FALSE
          AND t.lifecycle_state <> 'demo'
          AND t.created_at >= :since
        """,
        since=now - timedelta(days=7),
    )
    merchants_spark = [
        float(r)
        for r in (
            await db.execute(
                text(
                    """
                    SELECT count(t.id)
                    FROM generate_series(0, 6) AS g(d)
                    LEFT JOIN public.tenants t
                      ON date_trunc('day', t.created_at) = date_trunc('day', CAST(:now AS timestamptz) - (g.d || ' days')::interval)
                     AND t.is_internal IS FALSE AND t.lifecycle_state <> 'demo'
                    GROUP BY g.d ORDER BY g.d DESC
                    """
                ),
                {"now": now},
            )
        ).scalars()
    ]

    active_stores = await _scalar(
        db,
        f"SELECT count(*) FROM public.stores s WHERE s.status = 'ACTIVE' AND {stores_live}",  # nosec B608 - interpolates module literals only; values are bound
    )
    stores_new_7d = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.stores s
        WHERE s.created_at >= :since AND {stores_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        since=now - timedelta(days=7),
    )

    # ── Today's orders and value, against the same window yesterday ─────────
    orders_today = await _scalar(
        db,
        f"SELECT count(*) FROM public.orders o WHERE o.created_at >= :d AND {orders_live}",  # nosec B608 - interpolates module literals only; values are bound
        d=day_start,
    )
    orders_yesterday = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.orders o
        WHERE o.created_at >= :prev AND o.created_at < :cut AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        prev=day_start - timedelta(days=1),
        cut=day_start - timedelta(days=1) + (now - day_start),
    )
    value_today = await _scalar(
        db,
        f"""
        SELECT coalesce(sum(o.total), 0) FROM public.orders o
        WHERE o.created_at >= :d AND o.payment_status = 'PAID' AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        d=day_start,
    )
    value_yesterday = await _scalar(
        db,
        f"""
        SELECT coalesce(sum(o.total), 0) FROM public.orders o
        WHERE o.created_at >= :prev AND o.created_at < :cut
          AND o.payment_status = 'PAID' AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        prev=day_start - timedelta(days=1),
        cut=day_start - timedelta(days=1) + (now - day_start),
    )

    # ── Orders per day, zero-filled so the axis is a real fortnight ────────
    #
    # Per DAY, not per hour. A platform this size takes single-digit orders in
    # an hour, so an hourly axis was 24 buckets of zero with one bar somewhere
    # — it read as "nothing is happening" on a day that was ordinary. Days
    # carry enough volume per bucket to show a trend, which is the only
    # question this chart answers.
    chart_start = day_start - timedelta(days=ORDERS_CHART_DAYS - 1)
    daily = (
        await db.execute(
            text(
                f"""
                SELECT g.d AS day_offset,
                       count(o.id) AS orders,
                       coalesce(sum(o.total), 0) AS value
                FROM generate_series(0, :days - 1) AS g(d)
                LEFT JOIN public.orders o
                  ON o.created_at >= CAST(:start AS timestamptz)
                                     + (g.d || ' days')::interval
                 AND o.created_at <  CAST(:start AS timestamptz)
                                     + ((g.d + 1) || ' days')::interval
                 AND {orders_live}
                GROUP BY g.d ORDER BY g.d
                """  # nosec B608 - interpolates module literals only; values are bound
            ),
            {"start": chart_start, "days": ORDERS_CHART_DAYS},
        )
    ).all()
    orders_per_hour = [
        Bucket(
            label=(chart_start + timedelta(days=int(offset))).strftime("%d %b"),
            value=float(count),
        )
        for offset, count, _ in daily
    ]
    value_spark = [float(v) for _, _, v in daily]

    # ── Trial → paid over the last 30 days ─────────────────────────────────
    trial_started = await _scalar(
        db,
        """
        SELECT count(*) FROM public.tenants t
        WHERE t.trial_started_at >= :since AND t.is_internal IS FALSE
        """,
        since=now - timedelta(days=30),
    )
    trial_converted = await _scalar(
        db,
        """
        SELECT count(*) FROM public.tenants t
        WHERE t.trial_started_at >= :since AND t.trial_converted_at IS NOT NULL
          AND t.is_internal IS FALSE
        """,
        since=now - timedelta(days=30),
    )
    trial_pct = (
        round((trial_converted / trial_started) * 100, 1) if trial_started else 0.0
    )

    # ── Failures and queues ────────────────────────────────────────────────
    failed_payments = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.orders o
        WHERE o.payment_status = 'FAILED' AND o.created_at >= :since AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        since=now - timedelta(hours=24),
    )
    failed_payments_prev = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.orders o
        WHERE o.payment_status = 'FAILED'
          AND o.created_at >= :start AND o.created_at < :since AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        start=now - timedelta(hours=48),
        since=now - timedelta(hours=24),
    )

    stores_review = await _scalar(
        db,
        f"SELECT count(*) FROM public.stores s WHERE s.status = 'PENDING_APPROVAL' AND {stores_live}",  # nosec B608 - interpolates module literals only; values are bound
    )
    stores_review_old = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.stores s
        WHERE s.status = 'PENDING_APPROVAL' AND s.created_at < :cut AND {stores_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        cut=now - timedelta(hours=2),
    )

    high_risk = await _scalar(
        db,
        """
        SELECT count(*) FROM public.risk_assessments r
        WHERE r.risk_level IN ('high', 'critical')
          AND lower(coalesce(r.payment_method, '')) IN ('cod', 'cash_on_delivery')
          AND r.action_taken IS NULL
        """,
    )
    high_risk_oldest = await _scalar(
        db,
        """
        SELECT min(r.created_at) FROM public.risk_assessments r
        WHERE r.risk_level IN ('high', 'critical')
          AND lower(coalesce(r.payment_method, '')) IN ('cod', 'cash_on_delivery')
          AND r.action_taken IS NULL
        """,
    )

    open_cases = await _scalar(
        db,
        """
        SELECT count(*) FROM public.support_cases c
        WHERE c.status IN ('open', 'pending_merchant')
        """,
    )
    urgent_cases = await _scalar(
        db,
        """
        SELECT count(*) FROM public.support_cases c
        WHERE c.status IN ('open', 'pending_merchant')
          AND c.priority IN ('high', 'urgent')
        """,
    )

    failed_hooks = await _scalar(
        db,
        "SELECT count(*) FROM public.webhook_delivery_logs w WHERE w.status IN ('failed', 'exhausted')",
    )
    failed_hooks_24h = await _scalar(
        db,
        """
        SELECT count(*) FROM public.webhook_delivery_logs w
        WHERE w.status IN ('failed', 'exhausted') AND w.created_at >= :since
        """,
        since=now - timedelta(hours=24),
    )

    hook_rows = (
        await db.execute(
            text(
                """
                SELECT g.h AS hour, count(w.id) AS failures
                FROM generate_series(23, 0, -1) AS g(h)
                LEFT JOIN public.webhook_delivery_logs w
                  ON w.status IN ('failed', 'exhausted')
                 AND w.created_at >= CAST(:now AS timestamptz) - ((g.h + 1) || ' hours')::interval
                 AND w.created_at <  CAST(:now AS timestamptz) - (g.h || ' hours')::interval
                GROUP BY g.h ORDER BY g.h DESC
                """
            ),
            {"now": now},
        )
    ).all()
    hook_counts = [float(c) for _, c in hook_rows]
    # A bar is worth an operator's attention when it stands well clear of the
    # day's own typical hour, rather than above a number hardcoded here.
    ordered = sorted(hook_counts)
    # The 75th percentile, not the median: half the hours in a normal day sit
    # above the median, so a median-based line paints most of the chart red and
    # stops meaning anything. This flags the spike, not the busy half.
    p75 = ordered[int(len(ordered) * 0.75)] if ordered else 0.0
    threshold = max(p75 * 1.5, 1.0)
    webhook_failures_per_hour = [
        Bucket(
            label=f"{(now - timedelta(hours=h)):%H}",
            value=count,
            highlight=count >= threshold and count > 0,
        )
        for (h, _), count in zip(
            [(r[0], r[1]) for r in hook_rows], hook_counts, strict=True
        )
    ]

    # ── Orders needing a human ─────────────────────────────────────────────
    attention_rows = (
        (
            await db.execute(
                text(
                    f"""
                SELECT o.id, o.order_number, s.name AS store_name, s.id AS store_id,
                       o.tenant_id,
                       coalesce(r.customer_name, c.first_name || ' ' || c.last_name) AS customer_name,
                       c.phone AS customer_phone,
                       o.payment_method, o.status::text AS status,
                       o.payment_status::text AS payment_status,
                       o.total, o.currency,
                       r.risk_score, r.risk_level, o.created_at
                FROM public.orders o
                JOIN public.stores s ON s.id = o.store_id
                LEFT JOIN public.customers c ON c.id = o.customer_id
                LEFT JOIN public.risk_assessments r ON r.order_id = o.id
                WHERE {orders_live}
                  AND (
                        o.status IN ('PENDING', 'CONFIRMED', 'PAYMENT_FAILED')
                     OR o.payment_status = 'FAILED'
                     OR r.risk_level IN ('high', 'critical')
                  )
                -- NEWEST FIRST, not highest risk first. Sorting by score put
                -- the same handful of old high-risk orders at the top every
                -- day, so an order that needed a human this morning never
                -- reached the screen. Risk is still shown per row and the
                -- Trust & risk queue exists for working it by score.
                ORDER BY o.created_at DESC
                LIMIT 8
                """  # nosec B608 - interpolates module literals only; values are bound
                ),
            )
        )
        .mappings()
        .all()
    )

    # ── What staff and the system have been doing ──────────────────────────
    activity_rows = (
        (
            await db.execute(
                text(
                    """
                SELECT a.id, a.action, a.event_type, a.severity, a.details, a.created_at,
                       u.email AS actor_email,
                       s.name AS store_name
                FROM public.audit_logs a
                LEFT JOIN public.users u ON u.id = a.user_id
                LEFT JOIN public.stores s ON s.id = a.store_id
                ORDER BY a.created_at DESC
                LIMIT 8
                """
                ),
            )
        )
        .mappings()
        .all()
    )

    # ── Derived health ─────────────────────────────────────────────────────
    # Not incident records — this platform has no incident table. These are
    # measurements, phrased so an operator can act.
    #
    # EVERY SIGNAL IS ALWAYS RETURNED, with its current reading, including
    # when it is fine. The card used to be empty until something broke, which
    # meant "healthy" and "the query silently returned nothing" looked
    # identical — and an operator could not tell a 2% payment-failure rate
    # from a 9% one on its way to breaching. `severity` carries the state;
    # `ok` means measured and within tolerance.
    health: list[HealthSignal] = []
    paid_or_failed_24h = await _scalar(
        db,
        f"""
        SELECT count(*) FROM public.orders o
        WHERE o.created_at >= :since AND o.payment_status IN ('PAID', 'FAILED') AND {orders_live}
        """,  # nosec B608 - interpolates module literals only; values are bound
        since=now - timedelta(hours=24),
    )

    if paid_or_failed_24h:
        failure_rate = (failed_payments or 0) / paid_or_failed_24h * 100
        health.append(
            HealthSignal(
                id="payment_failure_rate",
                severity=(
                    "danger"
                    if failure_rate >= 25
                    else "warning"
                    if failure_rate >= 10
                    else "ok"
                ),
                title=f"Payment failure rate is {failure_rate:.0f}% over 24 hours",
                detail=(
                    f"{failed_payments or 0} of {paid_or_failed_24h} settled orders "
                    "failed. Cash on delivery is unaffected."
                ),
            )
        )
    else:
        # No settled orders at all in 24 hours. Saying so is the honest
        # reading; a 0% failure rate on zero orders would be a false all-clear.
        health.append(
            HealthSignal(
                id="payment_failure_rate",
                severity="ok",
                title="No card or wallet orders settled in the last 24 hours",
                detail="Nothing to measure a failure rate against.",
            )
        )

    awaiting_risk = await _scalar(
        db,
        """
        SELECT count(*) FROM public.risk_assessments r
        JOIN public.tenants t ON t.id = r.tenant_id
        WHERE r.action_taken IS NULL
          AND NOT (t.lifecycle_state = 'demo' OR t.is_internal IS TRUE)
        """,
    )

    hooks = failed_hooks_24h or 0
    health.append(
        HealthSignal(
            id="webhook_backlog",
            severity="danger" if hooks >= 100 else "warning" if hooks >= 20 else "ok",
            title=(
                f"{hooks} webhook deliveries failed in 24 hours"
                if hooks
                else "No webhook delivery failures in 24 hours"
            ),
            detail=(
                "Merchant integrations may be missing order events. "
                "Open the queue to retry."
                if hooks >= 20
                else "Merchant integrations are receiving order events."
            ),
        )
    )

    health.append(
        HealthSignal(
            id="risk_queue",
            severity="warning" if (awaiting_risk or 0) >= 25 else "ok",
            title=(
                f"{awaiting_risk} cash-on-delivery orders awaiting a decision"
                if awaiting_risk
                else "No cash-on-delivery orders awaiting a decision"
            ),
            detail=(
                "Every hour one waits is an hour the merchant cannot ship."
                if awaiting_risk
                else "The trust queue is clear."
            ),
        )
    )

    # ── What NUMU earns ────────────────────────────────────────────────────
    # Three separate things that are easy to conflate: recurring subscription
    # revenue, commission actually taken from pay-as-you-go wallets, and
    # merchant cash sitting in those wallets. Only the first two are income.
    window_30d = now - timedelta(days=30)

    plan_rows = (
        await db.execute(
            text(
                """
                SELECT t.plan, coalesce(t.billing_cycle, 'monthly') AS cycle, count(*) AS n
                FROM public.tenants t
                WHERE t.lifecycle_state = 'active'
                  AND t.is_internal IS FALSE
                GROUP BY t.plan, coalesce(t.billing_cycle, 'monthly')
                """
            )
        )
    ).all()

    plan_counts: dict[str, int] = {}
    mrr_by_plan: dict[str, int] = {}
    mrr_total = 0
    mrr_subscribers = 0
    for plan, cycle, count in plan_rows:
        plan_counts[plan] = plan_counts.get(plan, 0) + count
        features = PLAN_LIMITS.get(plan)
        if features is None:
            continue
        # An annual plan is counted at a twelfth of its (discounted) price, so
        # it can sit in the same total as a monthly one.
        per_month = (
            features.annual_price_piasters // 12
            if cycle == "annual"
            else features.monthly_price_piasters
        )
        if per_month <= 0:
            continue
        mrr_by_plan[plan] = mrr_by_plan.get(plan, 0) + per_month * count
        mrr_total += per_month * count
        mrr_subscribers += count

    subscriptions_collected = await _scalar(
        db,
        """
        SELECT coalesce(sum(i.amount_cents), 0)
        FROM public.subscription_payment_proofs p
        JOIN public.subscription_payment_intents i ON i.id = p.intent_id
        JOIN public.tenants t ON t.id = p.tenant_id
        WHERE p.status IN ('approved', 'auto_approved')
          AND p.review_decision_at >= :since
          AND t.is_internal IS FALSE
        """,
        since=window_30d,
    )

    # Commission rows are negative (they debit the merchant); reversals are
    # positive. NUMU's net take is the negation of their sum.
    payg_commission = await _scalar(
        db,
        """
        SELECT -coalesce(sum(w.amount_cents), 0)
        FROM public.wallet_transactions w
        JOIN public.tenants t ON t.id = w.tenant_id
        WHERE w.kind IN ('commission', 'commission_reversal')
          AND w.created_at >= :since
          AND t.is_internal IS FALSE
        """,
        since=window_30d,
    )

    topups_collected = await _scalar(
        db,
        """
        SELECT coalesce(sum(w.amount_cents), 0)
        FROM public.wallet_transactions w
        JOIN public.tenants t ON t.id = w.tenant_id
        WHERE w.kind = 'topup' AND w.created_at >= :since
          AND t.is_internal IS FALSE
        """,
        since=window_30d,
    )

    wallet_float = await _scalar(
        db,
        """
        SELECT coalesce(sum(m.balance_cents), 0)
        FROM public.merchant_wallets m
        JOIN public.tenants t ON t.id = m.tenant_id
        WHERE t.is_internal IS FALSE
        """,
    )

    earnings = Earnings(
        mrr_cents=mrr_total,
        mrr_subscribers=mrr_subscribers,
        mrr_by_plan=mrr_by_plan,
        plan_counts=plan_counts,
        subscriptions_collected_30d=int(subscriptions_collected or 0),
        payg_commission_30d=int(payg_commission or 0),
        wallet_topups_collected_30d=int(topups_collected or 0),
        wallet_float_cents=int(wallet_float or 0),
        recognised_30d=int(subscriptions_collected or 0) + int(payg_commission or 0),
    )

    data = OverviewResponse(
        generated_at=now,
        health=health,
        earnings=earnings,
        metrics=OverviewMetrics(
            active_merchants=Trend(
                value=active_merchants,
                delta=merchants_new_7d,
                spark=list(reversed(merchants_spark)),
                note="new this week",
            ),
            active_stores=Trend(
                value=active_stores, delta=stores_new_7d, note="new this week"
            ),
            orders_today=Trend(
                value=orders_today,
                delta=_pct(orders_today, orders_yesterday),
                delta_unit="pct",
                spark=[b.value for b in orders_per_hour],
                note="vs this time yesterday",
            ),
            order_value_today=Trend(
                value=value_today,
                delta=_pct(value_today, value_yesterday),
                delta_unit="pct",
                spark=value_spark,
                note="paid orders",
            ),
            trial_to_paid_30d=Trend(
                value=trial_pct,
                delta_unit="pp",
                note=f"{trial_converted} of {trial_started} trials",
            ),
            failed_payments_24h=Trend(
                value=failed_payments,
                delta=failed_payments - (failed_payments_prev or 0),
                note="vs the 24 hours before",
            ),
            stores_requiring_review=Trend(
                value=stores_review,
                note=(
                    f"{stores_review_old} waiting over 2h"  # nosec B608 - interpolates module literals only; values are bound
                    if stores_review_old
                    else "none waiting over 2h"
                ),
            ),
            high_risk_cod_orders=Trend(
                value=high_risk,
                note=_oldest_note(high_risk_oldest, now),
            ),
            open_support_cases=Trend(
                value=open_cases,
                note=(
                    f"{urgent_cases} high or urgent" if urgent_cases else "none urgent"  # nosec B608 - interpolates module literals only; values are bound
                ),
            ),
            failed_jobs_webhooks=Trend(
                value=failed_hooks,
                delta=failed_hooks_24h,
                note="failed in the last 24h",
            ),
        ),
        orders_per_hour=orders_per_hour,
        webhook_failures_per_hour=webhook_failures_per_hour,
        orders_needing_attention=[
            AttentionOrder(
                id=str(r["id"]),
                order_number=r["order_number"],
                store_name=r["store_name"],
                store_id=str(r["store_id"]),
                tenant_id=str(r["tenant_id"]) if r["tenant_id"] else None,
                customer_name=r["customer_name"],
                customer_phone=r["customer_phone"],
                payment_method=r["payment_method"],
                status=r["status"],
                payment_status=r["payment_status"],
                total_cents=r["total"],
                currency=r["currency"],
                risk_score=r["risk_score"],
                risk_level=r["risk_level"],
                created_at=r["created_at"],
            )
            for r in attention_rows
        ],
        activity=[
            ActivityEntry(
                id=str(r["id"]),
                action=r["action"] or r["event_type"],
                actor=r["actor_email"] or (r["details"] or {}).get("actor_type"),
                actor_type=(r["details"] or {}).get("actor_type", "system"),
                entity=r["store_name"],
                note=(r["details"] or {}).get("note"),
                severity=r["severity"],
                created_at=r["created_at"],
            )
            for r in activity_rows
        ],
    )

    return SuccessResponse(data=data, message="Overview retrieved successfully")


def _pct(current: float, previous: float | None) -> float | None:
    """Percentage move, or None when there is no baseline to move from."""
    if not previous:
        return None
    return round((current - previous) / previous * 100, 1)


def _oldest_note(oldest: datetime | None, now: datetime) -> str:
    if not oldest:
        return "queue is clear"
    minutes = int((now - oldest).total_seconds() // 60)
    if minutes < 60:
        return f"oldest waiting {minutes}m"
    return f"oldest waiting {minutes // 60}h {minutes % 60:02d}m"
