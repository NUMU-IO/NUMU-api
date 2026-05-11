"""Celery tasks for the recovery-flow aggregate (backend-021).

Two tasks:

- :func:`recovery_send_step` — fires step N's WhatsApp template, persists
  the :class:`~src.core.entities.recovery_flow.RecoveryStep` row,
  transitions the flow's state, and (if non-terminal) schedules step N+1
  via Celery countdown. Idempotent on ``(flow_id, step_index)``.

- :func:`recovery_apply_shopify_tags` — the Shopify additive-mutation
  outbox worker (spec 009 CL-006 step 3). Decoupled from the rollup
  write so a Shopify 5xx retry never re-triggers the rollup mutation.
  For v1 this task is a stub that logs the intended mutation; the actual
  Shopify Admin GraphQL call lands when spec 004's automation client is
  factored out for reuse.

The actual WhatsApp message send is deferred to spec 009's full
implementation — for v1 the send-step task records the ``RecoveryStep``
row + transitions the state but logs the message body instead of
calling Meta. This unblocks downstream wiring (event emission,
state-machine progression, dashboard timeline) without requiring Meta
credentials in dev/test.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Persistent per-worker event loop, mirroring ``risk_scoring_tasks``."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


# Map cadence step to the next state (backend-021 US2).
# ``current_step_index`` 0 → completes step_1 → transitions PENDING_STEP_1 → PENDING_STEP_2.
_STATE_AFTER_SEND_STEP = {
    0: "pending_step_2",
    1: "pending_step_3",
    # step_index >= 2 is terminal-via-cadence (the deposit fallback or auto-cancel).
}


@celery_app.task(
    name="tasks.recovery.send_step",
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    soft_time_limit=30,
)
def recovery_send_step(self, flow_id: str, step_index: int) -> dict:
    """Fire step N of the cadence for a recovery flow.

    Idempotent on the unique constraint ``uq_recovery_step_per_flow_index``:
    if a step row already exists for ``(flow_id, step_index)``, the task
    exits silently. This is the F-019 race protection at the step level.

    Returns a small dict for Celery introspection: the new state + whether
    a next step was scheduled.
    """
    return _run_async(_send_step_async(flow_id, step_index))


async def _send_step_async(flow_id: str, step_index: int) -> dict:
    from sqlalchemy import select

    from src.core.entities.recovery_flow import (
        RECOVERY_FLOW_TERMINAL_STATES,
        RecoveryFlowState,
        RecoveryStep,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.recovery_flow import (
        RecoveryFlowModel,
    )
    from src.infrastructure.repositories.recovery_flow_repository import (
        RecoveryFlowRepository,
    )

    flow_uuid = UUID(flow_id)

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET search_path TO public"))

        # Load the flow without the tenant filter (Celery worker has no
        # request-scope tenant context). Production-side RLS bypass is
        # acceptable here because the worker only reads + writes its own
        # `recovery_*` tables, all keyed off `flow_id`.
        result = await session.execute(
            select(RecoveryFlowModel).where(RecoveryFlowModel.id == flow_uuid)
        )
        flow_model = result.scalar_one_or_none()
        if flow_model is None:
            logger.warning("recovery_send_step_no_flow", flow_id=flow_id)
            return {"status": "no_flow"}

        # Bail if already terminal (customer paid in the meantime, etc.).
        if flow_model.state in RECOVERY_FLOW_TERMINAL_STATES:
            logger.info(
                "recovery_send_step_skipped_terminal",
                flow_id=flow_id,
                state=flow_model.state.value
                if hasattr(flow_model.state, "value")
                else str(flow_model.state),
            )
            return {"status": "skipped_terminal"}

        # Bail if the step we're firing isn't the current one (out-of-order delivery).
        if flow_model.current_step_index != step_index:
            logger.warning(
                "recovery_send_step_index_mismatch",
                flow_id=flow_id,
                expected=flow_model.current_step_index,
                got=step_index,
            )
            return {"status": "step_mismatch"}

        # Resolve the cadence entry for this step.
        cadence = flow_model.cadence or []
        if step_index >= len(cadence):
            logger.warning(
                "recovery_send_step_out_of_bounds",
                flow_id=flow_id,
                step_index=step_index,
                cadence_length=len(cadence),
            )
            return {"status": "out_of_bounds"}

        step_config = cadence[step_index]
        template_key = step_config.get("template_key")

        # Terminal action (no template) — apply the cadence's fallback_action.
        if not template_key:
            return await _apply_terminal_action(session, flow_model, step_config)

        # Insert the step row — unique constraint on (flow_id, step_index)
        # gives us the per-step idempotency.
        repo = RecoveryFlowRepository(session)
        scheduled_for = datetime.now(UTC)
        try:
            await repo.insert_step(
                RecoveryStep(
                    flow_id=flow_uuid,
                    step_index=step_index,
                    template_key=template_key,
                    channel="whatsapp",
                    scheduled_for=scheduled_for,
                    sent_at=datetime.now(UTC),
                ),
                tenant_id=flow_model.tenant_id,
            )
        except IntegrityError:
            # Idempotency hit — another worker already sent this step.
            await session.rollback()
            logger.info(
                "recovery_send_step_already_sent",
                flow_id=flow_id,
                step_index=step_index,
            )
            return {"status": "already_sent"}

        # TODO(spec-009): wire the actual WhatsApp messaging service here.
        # For v1 of backend-021 we log the intended send; the production
        # WhatsApp call lands as part of spec 009's implementation.
        logger.info(
            "recovery_send_step_dispatched",
            flow_id=flow_id,
            step_index=step_index,
            template_key=template_key,
            channel="whatsapp",
        )

        # Transition to the next pending state if there is one.
        next_state_value = _STATE_AFTER_SEND_STEP.get(step_index)
        scheduled_next = False
        if next_state_value is not None:
            from sqlalchemy import update as sa_update

            await session.execute(
                sa_update(RecoveryFlowModel)
                .where(RecoveryFlowModel.id == flow_uuid)
                .values(
                    state=RecoveryFlowState(next_state_value),
                    current_step_index=step_index + 1,
                    updated_at=datetime.now(UTC),
                )
            )

            # Schedule step N+1 via Celery countdown.
            next_step_index = step_index + 1
            if next_step_index < len(cadence):
                next_delay = cadence[next_step_index].get("delay_seconds", 0)
                recovery_send_step.apply_async(
                    args=[flow_id, next_step_index],
                    countdown=int(next_delay),
                )
                scheduled_next = True

        await session.commit()

        return {
            "status": "sent",
            "step_index": step_index,
            "next_state": next_state_value,
            "next_step_scheduled": scheduled_next,
        }


async def _apply_terminal_action(session, flow_model, step_config) -> dict:
    """Handle the cadence's terminal step (auto-cancel-or-hold or deposit-only).

    For v1 we transition the flow to ``ABANDONED`` (customer no response)
    or ``BLOCKED_NO_TEMPLATE`` if the action is unrecognised. The actual
    Shopify cancel/hold mutation is the outbox worker's responsibility
    (a future spec follow-up).
    """
    from sqlalchemy import update as sa_update

    from src.core.entities.recovery_flow import RecoveryFlowState
    from src.core.events.recovery_events import (
        RecoveryAbandonedEvent,
        make_dedupe_key,
    )
    from src.infrastructure.database.models.tenant.recovery_flow import (
        RecoveryFlowModel,
    )
    from src.infrastructure.events.setup import get_event_bus

    fallback = step_config.get("fallback_action")
    if fallback == "auto_cancel_or_hold":
        target = RecoveryFlowState.ABANDONED
        reason = "customer_no_response"
    elif fallback == "deposit_only":
        # The deposit-only fallback is itself a send-step in the default
        # cadence (it has a template_key). If we landed here without a
        # template, treat it as an abandon.
        target = RecoveryFlowState.ABANDONED
        reason = "deposit_offer_expired"
    else:
        target = RecoveryFlowState.BLOCKED_NO_TEMPLATE
        reason = "unknown_terminal_action"

    await session.execute(
        sa_update(RecoveryFlowModel)
        .where(RecoveryFlowModel.id == flow_model.id)
        .values(
            state=target,
            updated_at=datetime.now(UTC),
        )
    )
    await session.commit()

    bus = get_event_bus()
    bus.publish(
        RecoveryAbandonedEvent(
            flow_id=flow_model.id,
            store_id=flow_model.store_id,
            shopify_order_id=flow_model.shopify_order_id,
            dedupe_key=make_dedupe_key(
                flow_model.store_id, flow_model.shopify_order_id
            ),
            terminal_state=target.value,
            reason=reason,
        )
    )

    return {"status": "terminal", "target_state": target.value, "reason": reason}


# ---------------------------------------------------------------------------
# Shopify additive-mutation outbox worker (spec 009 CL-006 step 3)
# ---------------------------------------------------------------------------


@celery_app.task(
    name="tasks.recovery.apply_shopify_tags",
    bind=True,
    max_retries=5,
    default_retry_delay=30,
    soft_time_limit=15,
)
def recovery_apply_shopify_tags(
    self,
    store_id: str,
    shopify_order_id: str,
    rail: str,
    recovered_amount_cents: int,
) -> dict:
    """Append the recovery tags + note to the Shopify order.

    Decoupled from the rollup write (spec 009 CL-006 step 3): a Shopify
    5xx retry never re-triggers the rollup mutation because the rollup
    is already committed by the time this task runs.

    For v1 this is a stub that logs the intended additive mutation —
    spec 009 implementation will wire the actual Shopify Admin GraphQL
    call through the existing additive-mutation client used by spec 000's
    automation engine.
    """
    logger.info(
        "recovery_shopify_outbox_apply",
        store_id=store_id,
        shopify_order_id=shopify_order_id,
        rail=rail,
        recovered_amount_cents=recovered_amount_cents,
        tags_to_append=["numu-recovered", f"numu-recovery-{rail}"],
        note_prefix=f"NUMU: Recovered to prepaid via {rail} on {datetime.now(UTC).isoformat()}",
    )
    return {
        "status": "logged",
        "store_id": store_id,
        "shopify_order_id": shopify_order_id,
        "tags": ["numu-recovered", f"numu-recovery-{rail}"],
    }
