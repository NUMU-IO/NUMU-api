"""Daily trust auto-approve kill-switch evaluator (spec 010 CL-002).

For each merchant with ``auto_approve_on_trust_enabled = true``, count
the trust-auto-approves over the trailing 30 days and the subset that
turned RTO. If the cohort meets the minimum sample (≥20) AND the RTO
rate exceeds 5%, flip the toggle off, persist the trigger context, and
queue an in-app banner notification.

Per spec 010 CL-002 (maintainer-confirmed):
- Min sample: 20 auto-approves before evaluation
- Max RTO rate: 5%
- Re-enable: one-click with interstitial modal (UI side)
- Notification: after disable + email digest (the email digest is a
  follow-up; this task fires the in-app banner via persisted state)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.trust_kill_switch.evaluate_all_stores",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    soft_time_limit=600,
)
def evaluate_trust_kill_switch_all_stores(self) -> dict:
    """Beat-scheduled daily entry point — evaluate every enabled store."""
    return _run_async(_evaluate_all_async())


async def _evaluate_all_async() -> dict:
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.shopify_app_settings import (
        ShopifyAppSettingsModel,
    )

    enabled_stores: list[tuple[UUID, UUID]] = []
    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        result = await session.execute(
            select(
                ShopifyAppSettingsModel.store_id,
                ShopifyAppSettingsModel.tenant_id,
            ).where(ShopifyAppSettingsModel.auto_approve_on_trust_enabled.is_(True))
        )
        for sid, tid in result.all():
            if tid is not None:
                enabled_stores.append((sid, tid))

    fired = 0
    skipped = 0
    failed = 0
    for store_id, tenant_id in enabled_stores:
        try:
            outcome = await _evaluate_one_store_async(
                store_id=store_id, tenant_id=tenant_id
            )
            if outcome["fired"]:
                fired += 1
            else:
                skipped += 1
        except Exception as exc:
            logger.warning(
                "trust_kill_switch_failed_for_store",
                extra={"store_id": str(store_id), "error": str(exc)},
            )
            failed += 1

    return {
        "evaluated": len(enabled_stores),
        "fired": fired,
        "skipped": skipped,
        "failed": failed,
    }


async def _evaluate_one_store_async(*, store_id: UUID, tenant_id: UUID) -> dict:
    from sqlalchemy import and_, func, select, text, update

    from src.application.services.customer_trust_formula import (
        AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE,
        AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE,
        kill_switch_should_disable,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.risk_assessment import (
        RiskAssessmentModel,
    )
    from src.infrastructure.database.models.tenant.shipment import ShipmentModel
    from src.infrastructure.database.models.tenant.shopify_app_settings import (
        ShopifyAppSettingsModel,
    )

    window_start = datetime.now(UTC) - timedelta(days=30)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Count trust-auto-approved assessments in the window.
        count_q = await session.execute(
            select(func.count())
            .select_from(RiskAssessmentModel)
            .where(
                and_(
                    RiskAssessmentModel.store_id == store_id,
                    RiskAssessmentModel.action_taken_by == "system_trust_auto",
                    RiskAssessmentModel.action_taken_at >= window_start,
                )
            )
        )
        auto_approve_count = int(count_q.scalar() or 0)

        # Count the subset that ended up returned (RTO).
        # Join through order_id → shipment.status='returned'.
        rto_q = await session.execute(
            select(func.count())
            .select_from(RiskAssessmentModel)
            .join(
                ShipmentModel,
                ShipmentModel.order_id == RiskAssessmentModel.order_id,
            )
            .where(
                and_(
                    RiskAssessmentModel.store_id == store_id,
                    RiskAssessmentModel.action_taken_by == "system_trust_auto",
                    RiskAssessmentModel.action_taken_at >= window_start,
                    ShipmentModel.status.in_(("returned", "rto")),
                )
            )
        )
        rto_count = int(rto_q.scalar() or 0)

        if not kill_switch_should_disable(
            auto_approve_count=auto_approve_count,
            rto_count=rto_count,
        ):
            return {
                "fired": False,
                "auto_approve_count": auto_approve_count,
                "rto_count": rto_count,
            }

        # Fire — flip the toggle off + persist the trigger context.
        rate_pct = (rto_count / auto_approve_count * 100) if auto_approve_count else 0
        reason = (
            f"{rto_count} of the last {auto_approve_count} auto-approved orders "
            f"were returned ({rate_pct:.1f}%, above the {AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE * 100:.0f}% safety threshold). "
            f"Review your trust threshold and re-enable when ready."
        )

        await session.execute(
            update(ShopifyAppSettingsModel)
            .where(ShopifyAppSettingsModel.store_id == store_id)
            .values(
                auto_approve_on_trust_enabled=False,
                auto_disabled_at=datetime.now(UTC),
                auto_disabled_reason=reason,
            )
        )
        await session.commit()

    logger.info(
        "trust_kill_switch_fired",
        extra={
            "store_id": str(store_id),
            "auto_approve_count": auto_approve_count,
            "rto_count": rto_count,
            "rate_pct": rate_pct,
            "min_sample": AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE,
        },
    )
    return {
        "fired": True,
        "auto_approve_count": auto_approve_count,
        "rto_count": rto_count,
        "rate_pct": rate_pct,
    }
