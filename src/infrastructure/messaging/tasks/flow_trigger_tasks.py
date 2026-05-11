"""Celery task: emit Shopify Flow `flowTriggerReceive` mutations (backend-020).

Fires for each :class:`~src.core.events.recovery_events.RecoverySucceededEvent`
/ :class:`RecoveryAbandonedEvent` / :class:`~src.core.events.risk_events.RiskAssessmentFinalisedEvent`
that cleared the gating predicates. Idempotent on
``(store_id, dedup_key, trigger_handle)`` via the
:class:`~src.infrastructure.database.models.tenant.flow_trigger_emission_log.FlowTriggerEmissionLogModel`
unique constraint.

Per backend-020 spec, the v1 implementation is a stub that *records* the
emission attempt + payload but doesn't actually call Shopify Admin
GraphQL — the real `flowTriggerReceive` wiring lands when the per-shop
session-token plumbing for Flow API extends to background workers. This
keeps the dedup contract + observability working today; the ship to
Shopify is a follow-up implementation detail behind the same task.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

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
    name="tasks.flow_trigger.emit",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    soft_time_limit=15,
)
def emit_flow_trigger(
    self,
    store_id: str,
    tenant_id: str,
    source_event_id: str,
    trigger_handle: str,
    dedup_key: str,
    payload: dict,
) -> dict:
    """Emit a Shopify Flow trigger via flowTriggerReceive (or record skip).

    Idempotent on ``(store_id, dedup_key, trigger_handle)``: if a row
    already exists for this tuple, the task exits without re-emitting.
    """
    return _run_async(
        _emit_async(
            store_id=store_id,
            tenant_id=tenant_id,
            source_event_id=source_event_id,
            trigger_handle=trigger_handle,
            dedup_key=dedup_key,
            payload=payload,
        )
    )


async def _emit_async(
    *,
    store_id: str,
    tenant_id: str,
    source_event_id: str,
    trigger_handle: str,
    dedup_key: str,
    payload: dict,
) -> dict:
    from sqlalchemy import text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.flow_trigger_emission_log import (
        FlowTriggerEmissionLogModel,
    )

    store_uuid = UUID(store_id)
    tenant_uuid = UUID(tenant_id)
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Step 1: try to insert the emission log row — unique on
        # (store_id, dedup_key, trigger_handle) gates duplicate emissions.
        log_stmt = (
            pg_insert(FlowTriggerEmissionLogModel)
            .values(
                tenant_id=tenant_uuid,
                store_id=store_uuid,
                source_event_id=source_event_id,
                trigger_handle=trigger_handle,
                dedup_key=dedup_key,
                status="pending",
                attempted_at=now,
                payload_snapshot=payload,
            )
            .on_conflict_do_nothing(constraint="uq_flow_trigger_dedup")
            .returning(FlowTriggerEmissionLogModel.id)
        )
        result = await session.execute(log_stmt)
        emission_id = result.scalar_one_or_none()

        if emission_id is None:
            # Idempotency hit — another invocation already emitted (or attempted).
            await session.commit()
            logger.info(
                "flow_trigger_dedup_skip",
                store_id=store_id,
                trigger_handle=trigger_handle,
                dedup_key=dedup_key,
            )
            return {"status": "deduped", "trigger_handle": trigger_handle}

        await session.commit()

    # Step 2: actual Shopify Admin GraphQL flowTriggerReceive call.
    # v1 stub — log the emission. Production wiring will use the existing
    # admin_client.py + the per-shop access token from ShopifyInstallation.
    logger.info(
        "flow_trigger_emit_logged",
        store_id=store_id,
        trigger_handle=trigger_handle,
        dedup_key=dedup_key,
        payload=payload,
    )

    # Step 3: mark the emission as succeeded in the log.
    from sqlalchemy import update as sa_update

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        await session.execute(
            sa_update(FlowTriggerEmissionLogModel)
            .where(FlowTriggerEmissionLogModel.id == emission_id)
            .values(status="succeeded", succeeded_at=datetime.now(UTC))
        )
        await session.commit()

    return {
        "status": "emitted",
        "trigger_handle": trigger_handle,
        "dedup_key": dedup_key,
    }
