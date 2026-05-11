"""Recovery flow application service (backend-021).

Coordinates the entity, repository, and event bus to deliver the use cases
declared in ``specs/backend-021-recovery-flow-aggregate/spec.md``:

- US1: spawn a flow on ``RiskAssessmentFinalisedEvent`` (with full gating).
- US3 (partial): apply a payment-success event to the flow's terminal
  state + rollup, with idempotency on ``(store_id, shopify_order_id)``.

The service intentionally does NOT own:

- Celery scheduling of the next step (that's the worker's job — see
  ``recovery_tasks.py`` follow-up).
- Shopify additive mutations (that's the outbox-pattern worker — separate
  task).
- WhatsApp message sending (that's the messaging service).

Keeping those at arm's length lets us unit-test the state machine + rollup
gating without mocking out half the project.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from src.config.logging_config import get_logger
from src.core.entities.recovery_flow import (
    DEFAULT_RECOVERY_CADENCE,
    RecoveryFlow,
    RecoveryFlowState,
    RecoveryRollupLedgerEventType,
    validate_cadence,
)
from src.core.events.base import EventBus
from src.core.events.recovery_events import (
    RecoveryAbandonedEvent,
    RecoveryBlockedEvent,
    RecoveryStartedEvent,
    RecoverySucceededEvent,
    make_dedupe_key,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent
from src.infrastructure.repositories.recovery_flow_repository import (
    RecoveryFlowRepository,
)

logger = get_logger(__name__)


# Default risk-score cutoff. Merchants override via
# ``ShopifyAppSettings.recovery_trigger_threshold`` (added by spec 010 / spec 002).
DEFAULT_RECOVERY_TRIGGER_THRESHOLD = 60


class RecoveryFlowService:
    """Use cases for the recovery flow aggregate."""

    def __init__(
        self,
        repo: RecoveryFlowRepository,
        event_bus: EventBus,
    ) -> None:
        self.repo = repo
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    # US1 — RiskAssessmentFinalisedEvent → flow creation
    # ------------------------------------------------------------------

    async def maybe_start_flow_from_risk_event(
        self,
        event: RiskAssessmentFinalisedEvent,
        *,
        tenant_id: UUID,
        recovery_trigger_threshold: int = DEFAULT_RECOVERY_TRIGGER_THRESHOLD,
        cadence: list[dict] | None = None,
    ) -> RecoveryFlow | None:
        """Run the spec 009 FR-001 gating predicate; create the flow if it passes.

        Returns the flow (newly created OR pre-existing per CL-006 idempotency)
        if a flow exists for this order after the call; ``None`` if gating
        suppressed flow creation.

        Pre-conditions for flow creation (spec 009 FR-001 + US1 acceptance):

        1. ``score_type == "final"`` (preliminary scores never spawn flows).
        2. ``risk_score >= recovery_trigger_threshold``.
        3. ``event.recovery_enabled`` (the merchant has the feature on).
        4. ``event.has_payment_gateway`` (else flow ends in ``BLOCKED_NO_GATEWAY``
           with a ``RecoveryBlockedEvent`` so the dashboard surfaces it).
        5. ``event.subscription_active`` (no flow at all if no subscription;
           silent INFO log per US1 AS-3 — billing is not a recovery concern).
        6. ``event.shopify_order_id`` is present (we can't dedupe without it).
        """
        # Gate 1: score type
        if event.score_type != "final":
            return None
        # Gate 2: threshold
        if event.risk_score < recovery_trigger_threshold:
            return None
        # Gate 6: order id (defensive — risk events for non-Shopify sources skip)
        if not event.shopify_order_id:
            return None
        # Gate 5: subscription
        if not event.subscription_active:
            logger.info(
                "recovery_flow_skipped_no_subscription",
                store_id=str(event.store_id),
                shopify_order_id=event.shopify_order_id,
            )
            return None
        # Gate 3: feature enabled
        if not event.recovery_enabled:
            dedupe_key = make_dedupe_key(event.store_id, event.shopify_order_id)
            self.event_bus.publish(
                RecoveryBlockedEvent(
                    flow_id=None,
                    store_id=event.store_id,
                    shopify_order_id=event.shopify_order_id,
                    dedupe_key=dedupe_key,
                    reason="feature_disabled",
                )
            )
            return None

        # Build the flow entity. Gate 4 (no gateway) is encoded as a
        # ``BLOCKED_NO_GATEWAY`` initial state — the row IS created so the
        # merchant dashboard can render the "connect a gateway" prompt.
        if event.has_payment_gateway:
            initial_state = RecoveryFlowState.PENDING_STEP_1
        else:
            initial_state = RecoveryFlowState.BLOCKED_NO_GATEWAY

        cadence_to_use = cadence or list(DEFAULT_RECOVERY_CADENCE)
        # Defensive: validate even the default + override paths so a bad merchant
        # config that bypassed the settings-save validator still falls back.
        try:
            validate_cadence(cadence_to_use)
        except ValueError as exc:
            logger.warning(
                "recovery_cadence_invalid_fallback_to_default",
                store_id=str(event.store_id),
                shopify_order_id=event.shopify_order_id,
                reason=str(exc),
            )
            cadence_to_use = list(DEFAULT_RECOVERY_CADENCE)

        flow = RecoveryFlow(
            tenant_id=tenant_id,
            store_id=event.store_id,
            shopify_order_id=event.shopify_order_id,
            state=initial_state,
            cadence=cadence_to_use,
        )

        flow, created = await self.repo.create_if_not_exists(flow)
        dedupe_key = make_dedupe_key(flow.store_id, flow.shopify_order_id)

        if not created:
            # Idempotency hit — a flow already exists for this order. No event,
            # no work. The original RecoveryStartedEvent fired on first creation.
            logger.info(
                "recovery_flow_already_exists",
                flow_id=str(flow.id),
                store_id=str(flow.store_id),
                shopify_order_id=flow.shopify_order_id,
            )
            return flow

        # Emit the appropriate started/blocked event.
        if initial_state == RecoveryFlowState.BLOCKED_NO_GATEWAY:
            self.event_bus.publish(
                RecoveryBlockedEvent(
                    flow_id=flow.id,
                    store_id=flow.store_id,
                    shopify_order_id=flow.shopify_order_id,
                    dedupe_key=dedupe_key,
                    reason="no_gateway",
                )
            )
        else:
            send_step_count = sum(1 for s in cadence_to_use if s.get("template_key"))
            self.event_bus.publish(
                RecoveryStartedEvent(
                    flow_id=flow.id,
                    store_id=flow.store_id,
                    shopify_order_id=flow.shopify_order_id,
                    dedupe_key=dedupe_key,
                    risk_score=event.risk_score,
                    cadence_step_count=send_step_count,
                )
            )

        return flow

    # ------------------------------------------------------------------
    # US3 — payment success → SUCCEEDED / SUCCEEDED_DEPOSIT + rollup
    # ------------------------------------------------------------------

    async def apply_payment_success(
        self,
        *,
        tenant_id: UUID,
        store_id: UUID,
        shopify_order_id: str,
        rail: str,
        paid_amount_cents: int,
        deposit: bool,
        store_local_month: date,
    ) -> RecoveryFlow | None:
        """Drive a flow to SUCCEEDED / SUCCEEDED_DEPOSIT and update the rollup atomically.

        Idempotent on ``(store_id, shopify_order_id, event_type)`` per the
        rollup ledger (spec 009 CL-006). Safe to invoke multiple times for
        the same payment event — only the first call mutates state.

        Returns the flow entity in its post-transition state, or ``None``
        if no flow exists for that order (the caller can choose to log
        and ignore — payments arriving without a flow are unusual but
        not catastrophic).
        """
        flow = await self.repo.get_by_store_and_order(store_id, shopify_order_id)
        if flow is None:
            logger.info(
                "recovery_apply_payment_no_flow",
                store_id=str(store_id),
                shopify_order_id=shopify_order_id,
            )
            return None

        # If the flow is already terminal at success, skip the state transition
        # but still attempt the rollup (the ledger is the actual idempotency gate).
        if not flow.is_terminal():
            try:
                flow.mark_succeeded(
                    rail=rail,
                    amount_cents=paid_amount_cents,
                    deposit=deposit,
                )
                await self.repo.update_state(flow)
            except Exception as exc:  # InvalidRecoveryStateTransition or others
                logger.warning(
                    "recovery_flow_transition_skipped",
                    flow_id=str(flow.id),
                    current_state=flow.state.value,
                    reason=str(exc),
                )

        # Rollup ledger — gates the increment against duplicate events.
        event_type = (
            RecoveryRollupLedgerEventType.SUCCEEDED_DEPOSIT
            if deposit
            else RecoveryRollupLedgerEventType.SUCCEEDED
        )
        applied = await self.repo.apply_to_rollup(
            tenant_id=tenant_id,
            store_id=store_id,
            shopify_order_id=shopify_order_id,
            event_type=event_type,
            amount_cents=paid_amount_cents,
            store_local_month=store_local_month,
        )

        if applied:
            self.event_bus.publish(
                RecoverySucceededEvent(
                    flow_id=flow.id,
                    store_id=store_id,
                    shopify_order_id=shopify_order_id,
                    dedupe_key=make_dedupe_key(store_id, shopify_order_id),
                    rail=rail,
                    recovered_amount_cents=paid_amount_cents,
                    succeeded_as_deposit=deposit,
                )
            )

        return flow

    # ------------------------------------------------------------------
    # Generic abandonment helper (used by Celery STOP-reply handler etc.)
    # ------------------------------------------------------------------

    async def abandon_flow(
        self,
        flow: RecoveryFlow,
        *,
        target_state: RecoveryFlowState,
        reason: str,
    ) -> RecoveryFlow:
        """Transition a flow to a non-success terminal state + emit event."""
        flow.transition_to(target_state)
        await self.repo.update_state(flow)
        self.event_bus.publish(
            RecoveryAbandonedEvent(
                flow_id=flow.id,
                store_id=flow.store_id,
                shopify_order_id=flow.shopify_order_id,
                dedupe_key=make_dedupe_key(flow.store_id, flow.shopify_order_id),
                terminal_state=target_state.value,
                reason=reason,
            )
        )
        return flow

    # ------------------------------------------------------------------
    # Read-side helpers (used by the API)
    # ------------------------------------------------------------------

    async def list_flows_for_store(
        self,
        store_id: UUID,
        *,
        state: RecoveryFlowState | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RecoveryFlow]:
        return await self.repo.list_by_store(
            store_id, state=state, limit=limit, offset=offset
        )

    async def get_flow_with_steps(
        self, flow_id: UUID
    ) -> tuple[RecoveryFlow, list] | None:
        flow = await self.repo.get_by_id(flow_id)
        if flow is None:
            return None
        steps = await self.repo.list_steps_for_flow(flow_id)
        return flow, steps

    async def get_current_month_rollup(self, store_id: UUID, store_local_month: date):
        return await self.repo.get_rollup(store_id, store_local_month)
