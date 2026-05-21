"""Recovery flow domain entity — the per-order state machine for COD-to-prepaid recovery.

See ``specs/backend-021-recovery-flow-aggregate/spec.md`` for the spec this implements.
The state machine is derived from sibling spec ``009-cod-recovery-engine`` plus the
constitution v1.2.0 Principle VI canonical "recovered revenue" event definition.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class RecoveryFlowState(StrEnum):
    """Lifecycle of a single recovery flow per order.

    Terminal states are documented in :data:`RECOVERY_FLOW_TERMINAL_STATES`.
    Valid transitions are enforced by :data:`VALID_RECOVERY_TRANSITIONS` and
    by the application-level :func:`assert_can_transition` helper.
    """

    PENDING_STEP_1 = "pending_step_1"
    PENDING_STEP_2 = "pending_step_2"
    PENDING_STEP_3 = "pending_step_3"
    SUCCEEDED = "succeeded"
    SUCCEEDED_DEPOSIT = "succeeded_deposit"
    ABANDONED = "abandoned"
    ABANDONED_PARTIAL = "abandoned_partial"
    ABANDONED_BY_MERCHANT = "abandoned_by_merchant"
    TERMINATED_UNINSTALL = "terminated_uninstall"
    BLOCKED_NO_GATEWAY = "blocked_no_gateway"
    BLOCKED_NO_TEMPLATE = "blocked_no_template"


RECOVERY_FLOW_TERMINAL_STATES: frozenset[RecoveryFlowState] = frozenset({
    RecoveryFlowState.SUCCEEDED,
    RecoveryFlowState.SUCCEEDED_DEPOSIT,
    RecoveryFlowState.ABANDONED,
    RecoveryFlowState.ABANDONED_PARTIAL,
    RecoveryFlowState.ABANDONED_BY_MERCHANT,
    RecoveryFlowState.TERMINATED_UNINSTALL,
    RecoveryFlowState.BLOCKED_NO_GATEWAY,
    RecoveryFlowState.BLOCKED_NO_TEMPLATE,
})


# Valid transitions per the spec's state diagram. Any payment-success event from
# any non-terminal state may transition to SUCCEEDED / SUCCEEDED_DEPOSIT — that
# breadth is intentional because the customer can pay at any point in the cadence,
# not only when the next step fires.
_PAYMENT_SUCCESS_TARGETS = (
    RecoveryFlowState.SUCCEEDED,
    RecoveryFlowState.SUCCEEDED_DEPOSIT,
    RecoveryFlowState.ABANDONED_PARTIAL,
)
_GLOBAL_TERMINATION_TARGETS = (
    RecoveryFlowState.ABANDONED,
    RecoveryFlowState.ABANDONED_BY_MERCHANT,
    RecoveryFlowState.TERMINATED_UNINSTALL,
    RecoveryFlowState.BLOCKED_NO_TEMPLATE,
)

VALID_RECOVERY_TRANSITIONS: dict[RecoveryFlowState, tuple[RecoveryFlowState, ...]] = {
    RecoveryFlowState.PENDING_STEP_1: (
        RecoveryFlowState.PENDING_STEP_2,
        *_PAYMENT_SUCCESS_TARGETS,
        *_GLOBAL_TERMINATION_TARGETS,
    ),
    RecoveryFlowState.PENDING_STEP_2: (
        RecoveryFlowState.PENDING_STEP_3,
        *_PAYMENT_SUCCESS_TARGETS,
        *_GLOBAL_TERMINATION_TARGETS,
    ),
    RecoveryFlowState.PENDING_STEP_3: (
        *_PAYMENT_SUCCESS_TARGETS,
        *_GLOBAL_TERMINATION_TARGETS,
    ),
    # Terminal states have no outgoing transitions.
    RecoveryFlowState.SUCCEEDED: (),
    RecoveryFlowState.SUCCEEDED_DEPOSIT: (),
    RecoveryFlowState.ABANDONED: (),
    RecoveryFlowState.ABANDONED_PARTIAL: (),
    RecoveryFlowState.ABANDONED_BY_MERCHANT: (),
    RecoveryFlowState.TERMINATED_UNINSTALL: (),
    RecoveryFlowState.BLOCKED_NO_GATEWAY: (),
    RecoveryFlowState.BLOCKED_NO_TEMPLATE: (),
}


class InvalidRecoveryStateTransition(Exception):
    """Raised when an illegal :class:`RecoveryFlowState` transition is attempted."""

    def __init__(self, current: RecoveryFlowState, target: RecoveryFlowState) -> None:
        super().__init__(
            f"Invalid recovery flow state transition: {current.value} → {target.value}"
        )
        self.current = current
        self.target = target


def assert_can_transition(
    current: RecoveryFlowState, target: RecoveryFlowState
) -> None:
    """Raise :class:`InvalidRecoveryStateTransition` if the transition is not permitted."""
    if target not in VALID_RECOVERY_TRANSITIONS.get(current, ()):
        raise InvalidRecoveryStateTransition(current, target)


# Default recovery cadence per backend-021 spec + spec 009 CL-001 (1-hour minimum
# delay between steps; terminal action does NOT count toward 5-step send ceiling).
DEFAULT_RECOVERY_CADENCE: list[dict[str, Any]] = [
    {
        "delay_seconds": 0,
        "template_key": "recovery_step_1_offer",
        "fallback_action": None,
    },
    {
        "delay_seconds": 7200,
        "template_key": "recovery_step_2_reminder",
        "fallback_action": None,
    },
    {
        "delay_seconds": 86400,
        "template_key": "recovery_step_3_deposit",
        "fallback_action": "deposit_only",
    },
    # Terminal action — fired as auto_cancel or auto_hold per the constitution's
    # safe-defaults gate (first 30d post-install + 5 manual cancels per CL-005).
    {
        "delay_seconds": 172800,
        "template_key": None,
        "fallback_action": "auto_cancel_or_hold",
    },
]


# Cadence-override validation bounds per spec 009 CL-001.
CADENCE_MAX_SEND_STEPS = 5
CADENCE_MAX_TOTAL_SECONDS = 7 * 24 * 3600  # 7 days
CADENCE_MIN_INTER_STEP_SECONDS = 3600  # 1 hour


def validate_cadence(cadence: list[dict[str, Any]]) -> None:
    """Enforce the cadence-override bounds per spec 009 CL-001.

    Raises ``ValueError`` with a localized error code on the first violation
    encountered. Does not enforce template_key existence (that's checked at
    send time when the WhatsAppTemplate is resolved).
    """
    if not cadence:
        raise ValueError("recovery.cadence.empty")
    # Strip the terminal action for the send-step ceiling check.
    send_steps = [s for s in cadence if s.get("template_key")]
    if len(send_steps) > CADENCE_MAX_SEND_STEPS:
        raise ValueError("recovery.cadence.too_many_steps")
    total = sum(s.get("delay_seconds", 0) for s in cadence)
    if total > CADENCE_MAX_TOTAL_SECONDS:
        raise ValueError("recovery.cadence.exceeds_total_window")
    # Inter-step minimum applies to non-zero delays only (step 0 fires immediately).
    for step in cadence:
        d = step.get("delay_seconds", 0)
        if 0 < d < CADENCE_MIN_INTER_STEP_SECONDS:
            raise ValueError("recovery.cadence.inter_step_too_short")


# ---------------------------------------------------------------------------
# Aggregate root
# ---------------------------------------------------------------------------


class RecoveryFlow(BaseModel):
    """The per-order recovery flow aggregate.

    Owns its lifecycle state machine, cadence configuration, and the
    bookkeeping fields the rev-share billing pipeline reads from
    (``recovered_amount_cents``, ``recovered_via_rail``, ``refunded_at``).

    The ``id`` field is the canonical "flow_id" referenced in events,
    API responses, and the cross-merchant idempotency key
    ``(store_id, shopify_order_id)``.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID = Field(default_factory=uuid4)
    tenant_id: UUID
    store_id: UUID
    shopify_order_id: str
    state: RecoveryFlowState
    cadence: list[dict[str, Any]] = Field(
        default_factory=lambda: list(DEFAULT_RECOVERY_CADENCE)
    )
    current_step_index: int = 0
    payment_link_session_id: UUID | None = None
    recovered_amount_cents: int | None = None
    recovered_via_rail: str | None = None
    refunded_at: datetime | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def transition_to(self, target: RecoveryFlowState) -> None:
        """Validated state transition; raises if the move is illegal."""
        assert_can_transition(self.state, target)
        self.state = target
        self.updated_at = datetime.now(UTC)

    def is_terminal(self) -> bool:
        return self.state in RECOVERY_FLOW_TERMINAL_STATES

    def mark_succeeded(
        self, *, rail: str, amount_cents: int, deposit: bool = False
    ) -> None:
        """Capture a payment success — choose the right terminal state.

        ``deposit=True`` flags a partial payment that meets the deposit
        threshold (per spec 009 CL-002); the resulting state is
        ``SUCCEEDED_DEPOSIT`` and downstream billing computes rev-share on
        the partial amount. The eventual balance capture flows through
        :class:`~src.core.events.recovery_events.RecoveryBalanceCapturedEvent`,
        not a second SUCCEEDED transition (per spec 009 CL-006).
        """
        target = (
            RecoveryFlowState.SUCCEEDED_DEPOSIT
            if deposit
            else RecoveryFlowState.SUCCEEDED
        )
        self.transition_to(target)
        self.recovered_amount_cents = amount_cents
        self.recovered_via_rail = rail


# ---------------------------------------------------------------------------
# Step (child of RecoveryFlow)
# ---------------------------------------------------------------------------


class RecoveryStep(BaseModel):
    """A single scheduled / sent / failed step within a flow."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: UUID = Field(default_factory=uuid4)
    flow_id: UUID
    step_index: int
    template_key: str
    channel: str = "whatsapp"
    scheduled_for: datetime
    sent_at: datetime | None = None
    opened_at: datetime | None = None
    delivered_at: datetime | None = None
    failed_reason: str | None = None


# ---------------------------------------------------------------------------
# Monthly rollup (per-merchant aggregate)
# ---------------------------------------------------------------------------


class RecoveryMonthlyRollup(BaseModel):
    """Per-store, per-month aggregate keyed by ``(store_id, month_key)``.

    ``month_key`` is the first day of the *store-local* calendar month
    per constitution v1.2.0 FR-011 (timezone alignment with the billing
    cycle). Updated atomically via INSERT ... ON CONFLICT UPDATE through
    the :class:`RecoveryRollupLedger` dedup gate.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store_id: UUID
    month_key: datetime  # Date-aligned to first-of-month at 00:00 store-local TZ
    recovered_cents: int = 0
    recovered_count: int = 0
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ---------------------------------------------------------------------------
# Rollup ledger (idempotency gate per spec 009 CL-006)
# ---------------------------------------------------------------------------


class RecoveryRollupLedgerEventType(StrEnum):
    """Event-type classifier on the rollup ledger PK."""

    SUCCEEDED = "succeeded"
    SUCCEEDED_DEPOSIT = "succeeded_deposit"
    BALANCE_CAPTURED = "balance_captured"
    REFUNDED = "refunded"
    REFUND_REVERSED = "refund_reversed"


class RecoveryRollupLedger(BaseModel):
    """Append-only dedup ledger for per-event rollup updates.

    The composite PK ``(store_id, shopify_order_id, event_type)`` prevents
    the same rollup increment from being applied twice on Celery retry.
    Without this ledger, the ``RecoveryMonthlyRollup`` primary key
    ``(store_id, month_key)`` would prevent row duplication but NOT
    increment duplication — which is the F-019 race the spec 009 red-team
    surfaced.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    store_id: UUID
    shopify_order_id: str
    event_type: RecoveryRollupLedgerEventType
    applied_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    applied_amount_cents: int
