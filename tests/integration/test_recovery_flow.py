"""Integration tests for the recovery-flow aggregate (backend-021).

Each test maps to an acceptance scenario in
``specs/backend-021-recovery-flow-aggregate/spec.md`` (or its sibling
``specs/009-cod-recovery-engine``). The traceability tag in each test
docstring (e.g., ``US1 AS-1``) is what spec-derived-tests-from-spec means
in practice — change the spec, change the test.

Same caveats as ``test_instapay_flow.py``: SQLite in-memory means
PostgreSQL-only features (RLS, advisory locks, ON CONFLICT semantics)
are partially exercised. The dedup ledger (CL-006) is asserted by
checking the row-count outcome, not by serialising under load.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.recovery_flow_service import (
    DEFAULT_RECOVERY_TRIGGER_THRESHOLD,
    RecoveryFlowService,
)
from src.core.entities.recovery_flow import (
    DEFAULT_RECOVERY_CADENCE,
    InvalidRecoveryStateTransition,
    RecoveryFlowState,
    assert_can_transition,
    validate_cadence,
)
from src.core.events.recovery_events import (
    RecoveryBlockedEvent,
    RecoveryStartedEvent,
    RecoverySucceededEvent,
)
from src.core.events.risk_events import RiskAssessmentFinalisedEvent
from src.infrastructure.repositories.recovery_flow_repository import (
    RecoveryFlowRepository,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _capturing_bus() -> MagicMock:
    """An EventBus stand-in that records every publish() call.

    The recovery-flow service publishes domain events through the bus;
    rather than wire up real handlers we capture the calls and assert
    on the event class + payload. Matches the pattern in
    ``test_instapay_flow.py``.
    """
    bus = MagicMock()
    bus.publish = MagicMock()
    return bus


def _risk_event(
    *,
    store_id: UUID,
    tenant_id: UUID | None = None,
    shopify_order_id: str = "shop-order-001",
    risk_score: int = 75,
    score_type: str = "final",
    recovery_enabled: bool = True,
    has_payment_gateway: bool = True,
    subscription_active: bool = True,
) -> RiskAssessmentFinalisedEvent:
    return RiskAssessmentFinalisedEvent(
        assessment_id=uuid4(),
        tenant_id=tenant_id or uuid4(),
        store_id=store_id,
        shopify_order_id=shopify_order_id,
        order_id=uuid4(),
        customer_phone=None,
        risk_score=risk_score,
        risk_level="high",
        score_type=score_type,
        recovery_enabled=recovery_enabled,
        has_payment_gateway=has_payment_gateway,
        subscription_active=subscription_active,
    )


@pytest_asyncio.fixture
async def recovery_service(test_session: AsyncSession):
    """RecoveryFlowService wired against the SQLite test session."""
    repo = RecoveryFlowRepository(test_session)
    bus = _capturing_bus()
    service = RecoveryFlowService(repo=repo, event_bus=bus)
    return {"service": service, "repo": repo, "bus": bus, "session": test_session}


# ---------------------------------------------------------------------------
# State machine — pure-Python tests (no DB required)
# ---------------------------------------------------------------------------


class TestStateMachine:
    """Backend-021 FR-013: state-machine integrity at the application level.

    The DB-side CHECK constraint isn't testable on SQLite, but the
    application transition table catches the same invalid transitions.
    """

    def test_valid_transition_pending1_to_pending2(self):
        # Should not raise
        assert_can_transition(
            RecoveryFlowState.PENDING_STEP_1, RecoveryFlowState.PENDING_STEP_2
        )

    def test_valid_transition_pending3_to_succeeded(self):
        assert_can_transition(
            RecoveryFlowState.PENDING_STEP_3, RecoveryFlowState.SUCCEEDED
        )

    def test_invalid_transition_pending1_to_pending3_skips_step(self):
        # Spec: cadence steps fire in order; cannot leap-frog.
        with pytest.raises(InvalidRecoveryStateTransition):
            assert_can_transition(
                RecoveryFlowState.PENDING_STEP_1,
                RecoveryFlowState.PENDING_STEP_3,
            )

    def test_invalid_transition_from_terminal(self):
        # Terminal states have no outgoing transitions.
        with pytest.raises(InvalidRecoveryStateTransition):
            assert_can_transition(
                RecoveryFlowState.SUCCEEDED, RecoveryFlowState.PENDING_STEP_1
            )

    def test_payment_success_from_any_pending_state(self):
        # Customer can pay at any point in the cadence.
        for src in (
            RecoveryFlowState.PENDING_STEP_1,
            RecoveryFlowState.PENDING_STEP_2,
            RecoveryFlowState.PENDING_STEP_3,
        ):
            assert_can_transition(src, RecoveryFlowState.SUCCEEDED)
            assert_can_transition(src, RecoveryFlowState.SUCCEEDED_DEPOSIT)


# ---------------------------------------------------------------------------
# Cadence validation — spec 009 CL-001
# ---------------------------------------------------------------------------


class TestCadenceValidation:
    """Spec 009 CL-001: bounds enforced at the merchant settings save path."""

    def test_default_cadence_passes(self):
        validate_cadence(list(DEFAULT_RECOVERY_CADENCE))

    def test_empty_cadence_rejected(self):
        with pytest.raises(ValueError, match="empty"):
            validate_cadence([])

    def test_too_many_steps_rejected(self):
        cadence = [
            {"delay_seconds": 3600, "template_key": f"step_{i}"} for i in range(6)
        ]
        with pytest.raises(ValueError, match="too_many_steps"):
            validate_cadence(cadence)

    def test_inter_step_too_short_rejected(self):
        # 30 minutes < 1 hour minimum per CL-001.
        cadence = [
            {"delay_seconds": 0, "template_key": "step_1"},
            {"delay_seconds": 1800, "template_key": "step_2"},
        ]
        with pytest.raises(ValueError, match="inter_step_too_short"):
            validate_cadence(cadence)

    def test_total_window_exceeded_rejected(self):
        # 8 days exceeds the 7-day ceiling.
        cadence = [
            {"delay_seconds": 0, "template_key": "step_1"},
            {"delay_seconds": 8 * 24 * 3600, "template_key": "step_2"},
        ]
        with pytest.raises(ValueError, match="exceeds_total_window"):
            validate_cadence(cadence)


# ---------------------------------------------------------------------------
# US1 — RiskAssessmentFinalisedEvent → flow creation
# ---------------------------------------------------------------------------


class TestRiskEventConsumption:
    @pytest.mark.asyncio
    async def test_finalised_event_above_threshold_creates_flow_and_emits_started(
        self, recovery_service
    ):
        """US1 AS-1: gating predicate passes → flow + RecoveryStartedEvent."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]
        store_id = uuid4()
        tenant_id = uuid4()

        event = _risk_event(store_id=store_id, risk_score=75)
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=tenant_id)

        assert flow is not None
        assert flow.state == RecoveryFlowState.PENDING_STEP_1
        assert flow.shopify_order_id == event.shopify_order_id
        # Exactly one RecoveryStartedEvent published with the right shape.
        published = [c.args[0] for c in bus.publish.call_args_list]
        started_events = [e for e in published if isinstance(e, RecoveryStartedEvent)]
        assert len(started_events) == 1
        assert started_events[0].risk_score == 75
        assert started_events[0].dedupe_key == f"{store_id}:{event.shopify_order_id}"

    @pytest.mark.asyncio
    async def test_below_threshold_does_not_create_flow(self, recovery_service):
        """Gate: risk_score < threshold → no flow, no event."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        event = _risk_event(
            store_id=uuid4(),
            risk_score=DEFAULT_RECOVERY_TRIGGER_THRESHOLD - 1,
        )
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=uuid4())

        assert flow is None
        assert bus.publish.call_count == 0

    @pytest.mark.asyncio
    async def test_preliminary_score_does_not_create_flow(self, recovery_service):
        """US1 AS-5: preliminary scores never spawn flows."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        event = _risk_event(store_id=uuid4(), score_type="preliminary", risk_score=85)
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=uuid4())

        assert flow is None
        assert bus.publish.call_count == 0

    @pytest.mark.asyncio
    async def test_subscription_inactive_silently_suppresses(self, recovery_service):
        """US1 AS-3: no subscription → no flow, no event (just an INFO log)."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        event = _risk_event(store_id=uuid4(), subscription_active=False)
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=uuid4())

        assert flow is None
        assert bus.publish.call_count == 0

    @pytest.mark.asyncio
    async def test_recovery_disabled_emits_blocked_event_with_no_flow(
        self, recovery_service
    ):
        """Gate 3: feature disabled → no flow, RecoveryBlockedEvent for analytics."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        event = _risk_event(store_id=uuid4(), recovery_enabled=False)
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=uuid4())

        assert flow is None
        published = [c.args[0] for c in bus.publish.call_args_list]
        blocked = [e for e in published if isinstance(e, RecoveryBlockedEvent)]
        assert len(blocked) == 1
        assert blocked[0].reason == "feature_disabled"
        assert blocked[0].flow_id is None  # Blocked before flow creation

    @pytest.mark.asyncio
    async def test_no_gateway_creates_blocked_flow(self, recovery_service):
        """US1 AS-4: no gateway → flow created in BLOCKED_NO_GATEWAY for dashboard."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        event = _risk_event(store_id=uuid4(), has_payment_gateway=False)
        flow = await svc.maybe_start_flow_from_risk_event(event, tenant_id=uuid4())

        assert flow is not None
        assert flow.state == RecoveryFlowState.BLOCKED_NO_GATEWAY
        published = [c.args[0] for c in bus.publish.call_args_list]
        blocked = [e for e in published if isinstance(e, RecoveryBlockedEvent)]
        assert len(blocked) == 1
        assert blocked[0].reason == "no_gateway"
        assert blocked[0].flow_id == flow.id  # Flow row exists


# ---------------------------------------------------------------------------
# Spec 009 CL-006 + backend-021 SC-001 — idempotency triple
# ---------------------------------------------------------------------------


class TestIdempotency:
    @pytest.mark.asyncio
    async def test_duplicate_risk_event_creates_only_one_flow(self, recovery_service):
        """Resolves spec 009 red-team finding F-017 (flow uniqueness).

        Two RiskAssessmentFinalisedEvent deliveries for the same
        (store_id, shopify_order_id) → only one RecoveryFlow row exists.
        """
        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        bus = recovery_service["bus"]
        store_id = uuid4()
        tenant_id = uuid4()

        event1 = _risk_event(store_id=store_id, shopify_order_id="dup-order-1")
        event2 = _risk_event(store_id=store_id, shopify_order_id="dup-order-1")

        flow1 = await svc.maybe_start_flow_from_risk_event(event1, tenant_id=tenant_id)
        flow2 = await svc.maybe_start_flow_from_risk_event(event2, tenant_id=tenant_id)

        assert flow1 is not None and flow2 is not None
        # Same row returned both times.
        assert flow1.id == flow2.id
        # Only one flow exists for this store.
        flows = await repo.list_by_store(store_id)
        assert len(flows) == 1
        # Only one RecoveryStartedEvent published — second call is a no-op.
        started = [
            c.args[0]
            for c in bus.publish.call_args_list
            if isinstance(c.args[0], RecoveryStartedEvent)
        ]
        assert len(started) == 1

    @pytest.mark.asyncio
    async def test_duplicate_payment_success_increments_rollup_only_once(
        self, recovery_service
    ):
        """Resolves spec 009 red-team finding F-019 (rollup write race).

        Two RecoverySucceededEvent applications for the same
        (store_id, shopify_order_id) → one rollup increment, one event,
        not two.
        """
        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        bus = recovery_service["bus"]
        tenant_id = uuid4()
        store_id = uuid4()

        # Seed a flow first.
        event = _risk_event(store_id=store_id, shopify_order_id="ord-rollup-1")
        await svc.maybe_start_flow_from_risk_event(event, tenant_id=tenant_id)

        month = date(2026, 5, 1)
        # Apply payment success twice.
        await svc.apply_payment_success(
            tenant_id=tenant_id,
            store_id=store_id,
            shopify_order_id="ord-rollup-1",
            rail="paymob",
            paid_amount_cents=15_000,
            deposit=False,
            store_local_month=month,
        )
        await svc.apply_payment_success(
            tenant_id=tenant_id,
            store_id=store_id,
            shopify_order_id="ord-rollup-1",
            rail="paymob",
            paid_amount_cents=15_000,
            deposit=False,
            store_local_month=month,
        )

        # Rollup reflects exactly one increment.
        rollup = await repo.get_rollup(store_id, month)
        assert rollup is not None
        assert rollup.recovered_cents == 15_000
        assert rollup.recovered_count == 1

        # Exactly one RecoverySucceededEvent published.
        succeeded = [
            c.args[0]
            for c in bus.publish.call_args_list
            if isinstance(c.args[0], RecoverySucceededEvent)
        ]
        assert len(succeeded) == 1
        assert succeeded[0].rail == "paymob"
        assert succeeded[0].recovered_amount_cents == 15_000


# ---------------------------------------------------------------------------
# Read-side — list + detail + rollup
# ---------------------------------------------------------------------------


class TestReadSide:
    @pytest.mark.asyncio
    async def test_rollup_zero_when_no_flows(self, recovery_service):
        """The dashboard headline tile renders zero when no flows exist
        — backend supplies a missing rollup as `None`; the API route
        renders zeros explicitly per Principle VI empty-state quality bar.
        """
        repo = recovery_service["repo"]
        rollup = await repo.get_rollup(uuid4(), date(2026, 5, 1))
        assert rollup is None

    @pytest.mark.asyncio
    async def test_list_filters_by_state(self, recovery_service):
        """Listing by state surfaces only the matching rows."""
        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        store_id = uuid4()
        tenant_id = uuid4()

        # Create one PENDING and one BLOCKED_NO_GATEWAY in the same store.
        await svc.maybe_start_flow_from_risk_event(
            _risk_event(store_id=store_id, shopify_order_id="ord-pending"),
            tenant_id=tenant_id,
        )
        await svc.maybe_start_flow_from_risk_event(
            _risk_event(
                store_id=store_id,
                shopify_order_id="ord-blocked",
                has_payment_gateway=False,
            ),
            tenant_id=tenant_id,
        )

        pending = await repo.list_by_store(
            store_id, state=RecoveryFlowState.PENDING_STEP_1
        )
        blocked = await repo.list_by_store(
            store_id, state=RecoveryFlowState.BLOCKED_NO_GATEWAY
        )

        assert len(pending) == 1
        assert pending[0].shopify_order_id == "ord-pending"
        assert len(blocked) == 1
        assert blocked[0].shopify_order_id == "ord-blocked"


# ---------------------------------------------------------------------------
# Step row idempotency — the unique constraint on (flow_id, step_index)
# ---------------------------------------------------------------------------


class TestStepIdempotency:
    """Backend-021 US2 acceptance 1: step send is idempotent.

    The Celery task is a thin wrapper over the repository's
    ``insert_step`` + state transition; the underlying guarantee is the
    DB-level unique constraint ``uq_recovery_step_per_flow_index``. We
    exercise that directly to keep the test fast (no Celery harness
    setup needed).
    """

    @pytest.mark.asyncio
    async def test_inserting_duplicate_step_raises_integrity_error(
        self, recovery_service
    ):
        from sqlalchemy.exc import IntegrityError

        from src.core.entities.recovery_flow import RecoveryStep

        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        tenant_id = uuid4()
        store_id = uuid4()

        # Seed a flow.
        flow = await svc.maybe_start_flow_from_risk_event(
            _risk_event(store_id=store_id, shopify_order_id="ord-step-idem"),
            tenant_id=tenant_id,
        )
        assert flow is not None

        scheduled = datetime.now(UTC)
        step = RecoveryStep(
            flow_id=flow.id,
            step_index=0,
            template_key="recovery_step_1_offer",
            channel="whatsapp",
            scheduled_for=scheduled,
            sent_at=scheduled,
        )
        # First insert succeeds.
        await repo.insert_step(step, tenant_id=tenant_id)

        # Second insert at same (flow_id, step_index) raises.
        duplicate = RecoveryStep(
            flow_id=flow.id,
            step_index=0,
            template_key="recovery_step_1_offer",
            channel="whatsapp",
            scheduled_for=scheduled,
            sent_at=scheduled,
        )
        with pytest.raises(IntegrityError):
            await repo.insert_step(duplicate, tenant_id=tenant_id)

    @pytest.mark.asyncio
    async def test_steps_for_flow_returned_in_order(self, recovery_service):
        from src.core.entities.recovery_flow import RecoveryStep

        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        tenant_id = uuid4()
        store_id = uuid4()

        flow = await svc.maybe_start_flow_from_risk_event(
            _risk_event(store_id=store_id, shopify_order_id="ord-step-order"),
            tenant_id=tenant_id,
        )

        # Insert steps out of order.
        for idx in (2, 0, 1):
            await repo.insert_step(
                RecoveryStep(
                    flow_id=flow.id,
                    step_index=idx,
                    template_key=f"step_{idx}",
                    channel="whatsapp",
                    scheduled_for=datetime.now(UTC),
                ),
                tenant_id=tenant_id,
            )

        steps = await repo.list_steps_for_flow(flow.id)
        assert [s.step_index for s in steps] == [0, 1, 2]


# ---------------------------------------------------------------------------
# Service-level payment success state transitions
# ---------------------------------------------------------------------------


class TestPaymentSuccessTransitions:
    @pytest.mark.asyncio
    async def test_full_payment_transitions_to_succeeded(self, recovery_service):
        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        tenant_id = uuid4()
        store_id = uuid4()

        await svc.maybe_start_flow_from_risk_event(
            _risk_event(store_id=store_id, shopify_order_id="ord-succ-1"),
            tenant_id=tenant_id,
        )

        await svc.apply_payment_success(
            tenant_id=tenant_id,
            store_id=store_id,
            shopify_order_id="ord-succ-1",
            rail="paymob",
            paid_amount_cents=20_000,
            deposit=False,
            store_local_month=date(2026, 5, 1),
        )

        flow = await repo.get_by_store_and_order(store_id, "ord-succ-1")
        assert flow.state == RecoveryFlowState.SUCCEEDED
        assert flow.recovered_amount_cents == 20_000
        assert flow.recovered_via_rail == "paymob"

    @pytest.mark.asyncio
    async def test_deposit_payment_transitions_to_succeeded_deposit(
        self, recovery_service
    ):
        svc = recovery_service["service"]
        repo = recovery_service["repo"]
        tenant_id = uuid4()
        store_id = uuid4()

        await svc.maybe_start_flow_from_risk_event(
            _risk_event(store_id=store_id, shopify_order_id="ord-dep-1"),
            tenant_id=tenant_id,
        )

        await svc.apply_payment_success(
            tenant_id=tenant_id,
            store_id=store_id,
            shopify_order_id="ord-dep-1",
            rail="instapay",
            paid_amount_cents=5_000,
            deposit=True,
            store_local_month=date(2026, 5, 1),
        )

        flow = await repo.get_by_store_and_order(store_id, "ord-dep-1")
        assert flow.state == RecoveryFlowState.SUCCEEDED_DEPOSIT
        assert flow.recovered_amount_cents == 5_000
        assert flow.recovered_via_rail == "instapay"

    @pytest.mark.asyncio
    async def test_payment_for_unknown_order_is_noop(self, recovery_service):
        """Payment success for an order with no flow → returns None, no errors."""
        svc = recovery_service["service"]
        bus = recovery_service["bus"]

        result = await svc.apply_payment_success(
            tenant_id=uuid4(),
            store_id=uuid4(),
            shopify_order_id="ord-no-flow",
            rail="paymob",
            paid_amount_cents=10_000,
            deposit=False,
            store_local_month=date(2026, 5, 1),
        )

        assert result is None
        # No event published — there's nothing to publish about.
        published = [
            c.args[0]
            for c in bus.publish.call_args_list
            if isinstance(c.args[0], RecoverySucceededEvent)
        ]
        assert len(published) == 0


# ---------------------------------------------------------------------------
# Event bus subscription wiring smoke test
# ---------------------------------------------------------------------------


class TestEventBusWiring:
    """Verify the event-bus subscriptions registered in setup.py.

    Catches accidental removal of the recovery handlers — a regression
    that would silently break the entire flow-creation pipeline.
    """

    def test_recovery_handlers_are_subscribed(self):
        # Fresh import so the event bus is created from a clean state.
        # NB: setup.py guards against double-creation; we rely on that.
        from src.core.events.recovery_events import (
            RecoveryStartedEvent,
            RecoverySucceededEvent,
        )
        from src.core.events.risk_events import RiskAssessmentFinalisedEvent
        from src.infrastructure.events.handlers.recovery_event_handler import (
            handle_recovery_started_for_celery,
            handle_recovery_succeeded_outbox,
            handle_risk_finalised_for_recovery,
        )
        from src.infrastructure.events.setup import create_event_bus

        bus = create_event_bus()

        risk_handlers = bus._handlers.get(RiskAssessmentFinalisedEvent.__name__, [])
        started_handlers = bus._handlers.get(RecoveryStartedEvent.__name__, [])
        succeeded_handlers = bus._handlers.get(RecoverySucceededEvent.__name__, [])

        assert handle_risk_finalised_for_recovery in risk_handlers
        assert handle_recovery_started_for_celery in started_handlers
        assert handle_recovery_succeeded_outbox in succeeded_handlers
