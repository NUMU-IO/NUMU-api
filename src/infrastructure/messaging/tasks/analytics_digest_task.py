"""Weekly analytics digest — scheduled send task.

Runs Sunday 06:00 UTC (≈ 08:00–09:00 Cairo, Sunday morning — start of the
Egyptian work week). For each active store that opted in
(``settings.analytics_digest.enabled``), it builds the same recap the
preview endpoint shows and dispatches it over the merchant's chosen
channels (email via Resend, WhatsApp via the messaging service).

Opt-in shape on ``store.settings``::

    "analytics_digest": {"enabled": true, "channels": ["email", "whatsapp"]}

Best-effort per store — one store's send failure is logged and skipped,
never aborting the sweep.
"""

import asyncio
import logging
from datetime import UTC, datetime, timedelta

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop = None


def _run(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(name="tasks.send_weekly_analytics_digests", bind=True, max_retries=1)
def send_weekly_analytics_digests_task(self):
    """Send weekly digests to all opted-in active stores."""
    try:
        return _run(_send_all())
    except Exception as exc:
        logger.exception("weekly_digest_sweep_failed")
        raise self.retry(exc=exc, countdown=600)


async def _send_all() -> dict:
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(
                    StoreModel.id,
                    StoreModel.tenant_id,
                    StoreModel.settings,
                    StoreModel.default_currency,
                    StoreModel.default_language,
                    StoreModel.owner_id,
                ).where(StoreModel.status == "ACTIVE")
            )
        ).all()

    sent = 0
    skipped = 0
    errors = 0
    for row in rows:
        cfg = ((row.settings or {}).get("analytics_digest")) or {}
        if not cfg.get("enabled"):
            skipped += 1
            continue
        channels = cfg.get("channels") or ["email"]
        try:
            await _send_one(row, channels)
            sent += 1
        except Exception:
            errors += 1
            logger.warning(
                "weekly_digest_store_failed",
                extra={"store_id": str(row.id)},
                exc_info=True,
            )

    result = {"stores": len(rows), "sent": sent, "skipped": skipped, "errors": errors}
    logger.info(f"weekly_digest_complete: {result}")
    return result


async def _week_metrics(session, store_id, tz_name: str) -> dict:
    """This-week + prior-week aggregates + top product.

    Revenue/orders use the same rollup+live merge as the analytics KPI
    cards and the digest preview endpoint, so the sent digest never says
    "no sales" for a week whose orders the nightly rollup hasn't written yet.
    """
    from src.application.services.analytics_series import window_totals
    from src.core.utils.store_timezone import safe_zone
    from src.infrastructure.repositories.analytics_rollup_repository import (
        AnalyticsRollupRepository,
    )
    from src.infrastructure.repositories.order_repository import OrderRepository

    rollup_repo = AnalyticsRollupRepository(session)
    order_repo = OrderRepository(session)
    today = datetime.now(UTC).astimezone(safe_zone(tz_name)).date()
    week_start = today - timedelta(days=6)
    prev_end = week_start - timedelta(days=1)
    prev_start = prev_end - timedelta(days=6)

    cur_revenue, cur_orders = await window_totals(
        store_id=store_id,
        tz_name=tz_name,
        rollup_repo=rollup_repo,
        order_repo=order_repo,
        start_d=week_start,
        end_d=today,
        today_local=today,
    )
    prev_revenue, prev_orders = await window_totals(
        store_id=store_id,
        tz_name=tz_name,
        rollup_repo=rollup_repo,
        order_repo=order_repo,
        start_d=prev_start,
        end_d=prev_end,
        today_local=today,
    )
    # new_customers has no live equivalent yet — still rollup-sourced.
    cur = await rollup_repo.get_aggregated(store_id, week_start, today)
    rollups = await rollup_repo.get_range(store_id, week_start, today)

    prod: dict[str, tuple[str, int]] = {}
    for r in rollups or []:
        for item in r.top_products_json or []:
            pid = str(item.get("product_id", ""))
            if not pid:
                continue
            name, units = prod.get(pid, (item.get("name", ""), 0))
            prod[pid] = (
                name or item.get("name", ""),
                units + int(item.get("quantity", 0) or 0),
            )
    top = max(prod.values(), key=lambda x: x[1], default=None)

    orders = int(cur_orders)
    revenue = int(cur_revenue)
    return {
        "revenue_cents": revenue,
        "prev_revenue_cents": int(prev_revenue),
        "orders": orders,
        "prev_orders": int(prev_orders),
        "new_customers": int((cur or {}).get("new_customers", 0) or 0),
        "aov_cents": revenue // orders if orders > 0 else 0,
        "top_product_name": top[0] if top else None,
        "top_product_units": top[1] if top else 0,
        "week_start": week_start,
        "today": today,
    }


async def _send_one(row, channels: list[str]) -> None:
    from src.application.services.analytics_digest_service import build_weekly_digest
    from src.core.utils.store_timezone import resolve_store_timezone_name
    from src.infrastructure.database.connection import AsyncSessionLocal

    tz_name = resolve_store_timezone_name(row.settings)
    lang = "ar" if (row.default_language or "en").startswith("ar") else "en"
    currency = row.default_currency or "EGP"

    def _fmt(cents: int) -> str:
        return f"{cents / 100:,.2f} {currency}"

    async with AsyncSessionLocal() as session:
        metrics = await _week_metrics(session, row.id, tz_name)

    digest = build_weekly_digest(metrics, lang, _fmt)
    subject = "ملخص متجرك الأسبوعي" if lang == "ar" else "Your weekly store summary"
    lines = [digest["headline"], "", *[f"• {h}" for h in digest["highlights"]]]
    body = "\n".join(lines)

    if "email" in channels:
        await _send_email(row.owner_id, subject, body, digest)
    if "whatsapp" in channels:
        await _send_whatsapp(row.id, row.tenant_id, body)


async def _send_email(owner_id, subject: str, body: str, digest: dict) -> None:
    """Email the store owner. Fail-open; the Resend service already no-ops
    without an API key (dev)."""
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.user import UserModel
    from src.infrastructure.external_services.resend.email_service import (
        EmailMessage,
        ResendEmailService,
    )

    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(UserModel).where(UserModel.id == owner_id))
        ).scalar_one_or_none()
    if not user or not user.email:
        return

    html = (
        f"<h2>{digest['headline']}</h2><ul>"
        + "".join(f"<li>{h}</li>" for h in digest["highlights"])
        + "</ul>"
    )
    service = ResendEmailService()
    await service.send_email(
        EmailMessage(
            to=user.email, subject=subject, html_content=html, text_content=body
        )
    )


async def _send_whatsapp(store_id, tenant_id, body: str) -> None:
    """WhatsApp digest — deferred.

    Merchant-facing WhatsApp requires an approved message TEMPLATE (Meta
    doesn't allow freeform business-initiated messages outside a 24h
    session) plus the merchant's own opted-in number. Wiring that is a
    follow-up; email is the working channel today. Logged (not silently
    dropped) so the opt-in isn't mistaken for a delivery.
    """
    logger.info(
        "weekly_digest_whatsapp_deferred",
        extra={"store_id": str(store_id)},
    )
