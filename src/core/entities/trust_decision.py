"""Canonical trust-decision state machine (Phase C).

The single, auditable decision lifecycle that both the Shopify-app risk path
and the native storefront checkout collapse onto. Today that decision is
fragmented across four authorities — the ``_suggested_action`` risk ladder,
the ``ShopifyAppSettings`` thresholds, the automation rule engine, and the
native ``cod_trust_service`` guard sequence — which produce two different
numbers for the same buyer. This entity is the unifying backbone: one
declarative state + transition table, every move logged, mirroring the
proven recovery-flow FSM (``core/entities/recovery_flow.py``).

Pure domain — no application/infra imports. The decision *computation* (which
``SCORED`` → outcome edge to take) lives in
``application/services/trust_decision_service.py`` so the deterministic
trust-formula gate can be reused without a layering violation.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class TrustDecisionState(StrEnum):
    """Lifecycle of the trust decision for a single order.

    Terminal states are documented in :data:`TRUST_DECISION_TERMINAL_STATES`;
    legal moves are enforced by :data:`VALID_TRUST_DECISION_TRANSITIONS` and
    :func:`assert_can_transition`.
    """

    NEW = "new"  # created, not yet scored
    SCORED = "scored"  # score computed, awaiting the decision edge
    AUTO_APPROVED = "auto_approved"  # cleared (trusted or low-risk) — terminal
    CONFIRM_PENDING = "confirm_pending"  # awaiting customer WhatsApp confirmation
    HELD = "held"  # awaiting merchant review
    BLOCKED = "blocked"  # rejected at checkout / hard block — terminal
    CONFIRMED = "confirmed"  # confirmed by customer tap or merchant — terminal
    CANCELLED = "cancelled"  # safety-gated auto-cancel or merchant/customer — terminal


TRUST_DECISION_TERMINAL_STATES: frozenset[TrustDecisionState] = frozenset({
    TrustDecisionState.AUTO_APPROVED,
    TrustDecisionState.BLOCKED,
    TrustDecisionState.CONFIRMED,
    TrustDecisionState.CANCELLED,
})


VALID_TRUST_DECISION_TRANSITIONS: dict[
    TrustDecisionState, tuple[TrustDecisionState, ...]
] = {
    TrustDecisionState.NEW: (TrustDecisionState.SCORED,),
    TrustDecisionState.SCORED: (
        TrustDecisionState.AUTO_APPROVED,
        TrustDecisionState.CONFIRM_PENDING,
        TrustDecisionState.HELD,
        TrustDecisionState.BLOCKED,
        TrustDecisionState.CANCELLED,
    ),
    # A pending customer confirmation resolves to confirmed or cancelled.
    TrustDecisionState.CONFIRM_PENDING: (
        TrustDecisionState.CONFIRMED,
        TrustDecisionState.CANCELLED,
    ),
    # A merchant review resolves a held order to confirmed or cancelled.
    TrustDecisionState.HELD: (
        TrustDecisionState.CONFIRMED,
        TrustDecisionState.CANCELLED,
    ),
    # Terminal states have no outgoing transitions.
    TrustDecisionState.AUTO_APPROVED: (),
    TrustDecisionState.BLOCKED: (),
    TrustDecisionState.CONFIRMED: (),
    TrustDecisionState.CANCELLED: (),
}


class InvalidTrustDecisionTransition(Exception):
    """Raised when an illegal :class:`TrustDecisionState` transition is attempted."""

    def __init__(self, current: TrustDecisionState, target: TrustDecisionState) -> None:
        super().__init__(
            f"Invalid trust decision transition: {current.value} → {target.value}"
        )
        self.current = current
        self.target = target


def assert_can_transition(
    current: TrustDecisionState, target: TrustDecisionState
) -> None:
    """Raise :class:`InvalidTrustDecisionTransition` if the move is not permitted."""
    if target not in VALID_TRUST_DECISION_TRANSITIONS.get(current, ()):
        raise InvalidTrustDecisionTransition(current, target)


def is_terminal(state: TrustDecisionState) -> bool:
    """True if ``state`` has no outgoing transitions."""
    return state in TRUST_DECISION_TERMINAL_STATES


class TrustDecisionTransition(BaseModel):
    """One recorded edge in a decision's history (the audit trail)."""

    model_config = ConfigDict(frozen=True)

    from_state: TrustDecisionState
    to_state: TrustDecisionState
    reason: str | None = None
    at: datetime


class TrustDecision(BaseModel):
    """Per-order trust-decision aggregate: current state + full transition log.

    Every move goes through :meth:`transition_to`, so ``history`` is the
    complete audit artifact a due-diligence reviewer can read to see exactly
    what the system decided for an order and why.
    """

    id: UUID = Field(default_factory=uuid4)
    order_ref: str | None = None
    store_id: UUID | None = None
    state: TrustDecisionState = TrustDecisionState.NEW
    history: list[TrustDecisionTransition] = Field(default_factory=list)

    def transition_to(
        self,
        target: TrustDecisionState,
        *,
        reason: str | None = None,
        at: datetime | None = None,
    ) -> None:
        """Guarded transition; raises on an illegal edge and logs the move."""
        assert_can_transition(self.state, target)
        moment = at or datetime.now(UTC)
        self.history.append(
            TrustDecisionTransition(
                from_state=self.state,
                to_state=target,
                reason=reason,
                at=moment,
            )
        )
        self.state = target

    @property
    def is_terminal(self) -> bool:
        return is_terminal(self.state)
