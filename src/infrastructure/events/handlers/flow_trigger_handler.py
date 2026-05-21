"""Event handlers that fire Shopify Flow triggers (backend-020).

Three handlers, one per supported trigger:

- :func:`handle_risk_finalised_for_flow_trigger` →
  ``risk_score_calculated`` trigger.
- :func:`handle_recovery_succeeded_for_flow_trigger` →
  ``recovery_succeeded`` trigger.
- :func:`handle_recovery_abandoned_for_flow_trigger` →
  ``recovery_abandoned`` trigger.

Each handler builds the trigger payload + dedup key, then schedules
:func:`~src.infrastructure.messaging.tasks.flow_trigger_tasks.emit_flow_trigger`
via Celery. The Celery task gates duplicate emissions on
``(store_id, dedup_key, trigger_handle)``.
"""

from __future__ import annotations

from src.application.services.flow_trigger_dedup_keys import (
    TRIGGER_RECOVERY_ABANDONED,
    TRIGGER_RECOVERY_SUCCEEDED,
    TRIGGER_RISK_SCORE_CALCULATED,
    dedup_key_recovery_abandoned,
    dedup_key_recovery_succeeded,
    dedup_key_risk_score_calculated,
)
from src.config.logging_config import get_logger
from src.core.events.recovery_events import (
    RecoveryAbandonedEvent,
    RecoverySucceededEvent,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent

logger = get_logger(__name__)


async def handle_risk_finalised_for_flow_trigger(
    event: RiskAssessmentFinalisedEvent,
) -> None:
    """Schedule a ``risk_score_calculated`` Flow trigger."""
    if not event.shopify_order_id:
        return

    from src.infrastructure.messaging.tasks.flow_trigger_tasks import (
        emit_flow_trigger,
    )

    payload = {
        "order": {"id": event.shopify_order_id},
        "score": event.risk_score,
        "level": event.risk_level,
        "score_type": event.score_type,
        # Backend-022 extension: spec 010 FR-008 adds these to the payload.
        # They're populated downstream from the RiskAssessment row by the
        # backend-020 emitter when calling Shopify; for v1 we leave them
        # out of the dedup key but include them in the payload snapshot.
    }

    emit_flow_trigger.apply_async(
        args=[
            str(event.store_id),
            str(event.tenant_id),
            str(event.event_id),
            TRIGGER_RISK_SCORE_CALCULATED,
            dedup_key_risk_score_calculated(event.shopify_order_id, event.score_type),
            payload,
        ],
        countdown=0,
    )
    logger.info(
        "flow_trigger_risk_scheduled",
        store_id=str(event.store_id),
        shopify_order_id=event.shopify_order_id,
        score_type=event.score_type,
    )


async def handle_recovery_succeeded_for_flow_trigger(
    event: RecoverySucceededEvent,
) -> None:
    """Schedule a ``recovery_succeeded`` Flow trigger."""
    from src.infrastructure.messaging.tasks.flow_trigger_tasks import (
        emit_flow_trigger,
    )

    # Resolve tenant_id from the recovery flow row — the event payload
    # doesn't carry it because the immediate consumers (rollup updater,
    # outbox worker) don't need it. Done as a small helper query rather
    # than bloating the event class.
    tenant_id = await _resolve_tenant_id_for_flow(event.flow_id)
    if tenant_id is None:
        return  # No tenant resolution → emission can't be scoped; skip.

    payload = {
        "order": {"id": event.shopify_order_id},
        "rail": event.rail,
        "recovered_amount_cents": event.recovered_amount_cents,
        "succeeded_as_deposit": event.succeeded_as_deposit,
        "dedupe_key": event.dedupe_key,
    }

    emit_flow_trigger.apply_async(
        args=[
            str(event.store_id),
            str(tenant_id),
            str(event.event_id),
            TRIGGER_RECOVERY_SUCCEEDED,
            dedup_key_recovery_succeeded(event.shopify_order_id),
            payload,
        ],
        countdown=0,
    )


async def handle_recovery_abandoned_for_flow_trigger(
    event: RecoveryAbandonedEvent,
) -> None:
    """Schedule a ``recovery_abandoned`` Flow trigger."""
    from src.infrastructure.messaging.tasks.flow_trigger_tasks import (
        emit_flow_trigger,
    )

    tenant_id = await _resolve_tenant_id_for_flow(event.flow_id)
    if tenant_id is None:
        return

    payload = {
        "order": {"id": event.shopify_order_id},
        "reason": event.reason,
        "terminal_state": event.terminal_state,
    }

    emit_flow_trigger.apply_async(
        args=[
            str(event.store_id),
            str(tenant_id),
            str(event.event_id),
            TRIGGER_RECOVERY_ABANDONED,
            dedup_key_recovery_abandoned(event.shopify_order_id),
            payload,
        ],
        countdown=0,
    )


async def _resolve_tenant_id_for_flow(flow_id):
    """Look up the tenant_id for a recovery flow id."""
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.recovery_flow import (
        RecoveryFlowModel,
    )

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))
        result = await session.execute(
            select(RecoveryFlowModel.tenant_id).where(RecoveryFlowModel.id == flow_id)
        )
        return result.scalar_one_or_none()
