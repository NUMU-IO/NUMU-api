"""Event handlers that bind the recovery-flow service to the event bus.

Three handlers are registered in ``setup.py``:

- :func:`handle_risk_finalised_for_recovery` — listens to
  :class:`~src.core.events.risk_events.RiskAssessmentFinalisedEvent` and
  spawns a :class:`~src.core.entities.recovery_flow.RecoveryFlow` per
  the gating predicate (backend-021 US1).

- :func:`handle_recovery_succeeded_outbox` — listens to
  :class:`~src.core.events.recovery_events.RecoverySucceededEvent` and
  schedules the Shopify additive-mutation outbox worker so the order is
  tagged ``numu-recovered`` + the rail-specific tag without coupling the
  rollup write to a Shopify round-trip (spec 009 CL-006 step 3).

- :func:`handle_recovery_started_for_celery` — listens to
  :class:`~src.core.events.recovery_events.RecoveryStartedEvent` and
  schedules the first ``recovery_send_step`` Celery task at the
  cadence's ``step[0].delay_seconds``.

Each handler opens its own AsyncSession and sets the tenant RLS context
from the event payload. Failures are caught and logged via the bus's
:class:`~src.core.events.base.EventBus._safe_invoke` wrapper — they
never propagate to the publisher.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.core.entities.recovery_flow import (
    DEFAULT_RECOVERY_CADENCE,
)
from src.core.events.recovery_events import (
    RecoveryStartedEvent,
    RecoverySucceededEvent,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent

logger = get_logger(__name__)


async def _set_rls(session: AsyncSession, tenant_id) -> None:
    """Pin the session's PostgreSQL ``app.current_tenant`` so RLS policies fire.

    No-op on SQLite (the test harness has no RLS); silently swallows the
    error so integration tests using SQLite don't blow up.
    """
    try:
        await session.execute(
            text("SET app.current_tenant = :tid"), {"tid": str(tenant_id)}
        )
    except Exception:  # pragma: no cover — SQLite + dev environments
        pass


async def handle_risk_finalised_for_recovery(
    event: RiskAssessmentFinalisedEvent,
) -> None:
    """Backend-021 US1 — spawn (or look up) a recovery flow per the gating predicate.

    Idempotent at three layers: this handler may fire twice on event-bus
    replay, but ``RecoveryFlowService.maybe_start_flow_from_risk_event``
    short-circuits on the second call via the ``(store_id,
    shopify_order_id)`` unique constraint per spec 009 CL-006.
    """
    if event.score_type != "final":
        # Preliminary scores never spawn flows per backend-021 US1 AS-5.
        return

    # Local imports avoid module-load circulars on the event-bus side.
    from src.application.services.recovery_flow_service import RecoveryFlowService
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.events.setup import get_event_bus
    from src.infrastructure.repositories.recovery_flow_repository import (
        RecoveryFlowRepository,
    )

    async with AsyncSessionLocal() as session:
        await _set_rls(session, event.tenant_id)
        repo = RecoveryFlowRepository(session)
        service = RecoveryFlowService(repo=repo, event_bus=get_event_bus())
        flow = await service.maybe_start_flow_from_risk_event(
            event,
            tenant_id=event.tenant_id,
            cadence=list(DEFAULT_RECOVERY_CADENCE),
        )
        await session.commit()

        if flow is not None:
            logger.info(
                "recovery_flow_handler_processed",
                flow_id=str(flow.id),
                state=flow.state.value,
                store_id=str(event.store_id),
                shopify_order_id=event.shopify_order_id,
            )


async def handle_recovery_started_for_celery(event: RecoveryStartedEvent) -> None:
    """Schedule the first ``recovery_send_step`` Celery task for the new flow.

    Only fires for flows that successfully reached ``PENDING_STEP_1`` —
    blocked flows (``BLOCKED_NO_GATEWAY``) emit
    :class:`~src.core.events.recovery_events.RecoveryBlockedEvent` instead
    and never get here.
    """
    # Local import avoids pulling Celery into the event-bus module graph
    # before workers boot in non-Celery contexts (e.g., the API process
    # publishing the event but not consuming it).
    from src.infrastructure.messaging.tasks.recovery_tasks import (
        recovery_send_step,
    )

    # Step 0 fires immediately (delay_seconds=0); subsequent steps are
    # scheduled by the send-step task itself once each step completes.
    recovery_send_step.apply_async(
        args=[str(event.flow_id), 0],
        countdown=0,
    )
    logger.info(
        "recovery_send_step_scheduled",
        flow_id=str(event.flow_id),
        step_index=0,
        countdown=0,
    )


async def handle_recovery_succeeded_outbox(event: RecoverySucceededEvent) -> None:
    """Schedule the Shopify additive-mutation outbox worker (spec 009 CL-006 step 3).

    The rollup write (in the recovery service) and the Shopify mutation
    (here) are deliberately decoupled: a Shopify 5xx retry never
    re-triggers the rollup write because the rollup is already committed
    by the time this handler runs.
    """
    from src.infrastructure.messaging.tasks.recovery_tasks import (
        recovery_apply_shopify_tags,
    )

    recovery_apply_shopify_tags.apply_async(
        args=[
            str(event.store_id),
            event.shopify_order_id,
            event.rail,
            event.recovered_amount_cents,
        ],
        countdown=0,
    )
    logger.info(
        "recovery_shopify_outbox_scheduled",
        flow_id=str(event.flow_id),
        rail=event.rail,
        store_id=str(event.store_id),
        shopify_order_id=event.shopify_order_id,
    )
