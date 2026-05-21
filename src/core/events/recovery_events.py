"""Domain events for the COD-to-prepaid recovery flow.

Emitted by the recovery flow service + Celery handlers; consumed by
spec 010 (positive trust signal recorder), spec 002 (rev-share billing),
spec 011 (Anthropic narrative cache), the dashboard rollup updater, and
``backend-020-flow-trigger-emitter`` (which forwards
``RecoverySucceededEvent`` and ``RecoveryAbandonedEvent`` to Shopify Flow).

Every event carries ``dedupe_key = f"{store_id}:{shopify_order_id}"`` so
downstream consumers can apply their own idempotency without inventing a
key. This matches constitution v1.2.0 FR-010's canonical recovered-revenue
dedup key and spec 009 CL-006's idempotency triple.
"""

from __future__ import annotations

from uuid import UUID

from src.core.events.base import DomainEvent


def make_dedupe_key(store_id: UUID, shopify_order_id: str) -> str:
    """The canonical dedup key per constitution v1.2.0 FR-010.

    Centralised here so consumers don't reinvent the format string.
    """
    return f"{store_id}:{shopify_order_id}"


class RecoveryStartedEvent(DomainEvent):
    """Emitted when a :class:`~src.core.entities.recovery_flow.RecoveryFlow` is created.

    Fires from the ``RiskAssessmentFinalisedEvent`` consumer once all
    gating preconditions pass (recovery enabled for store, gateway
    connected, subscription active, recovery_trigger_threshold met).
    """

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    risk_score: int
    cadence_step_count: int  # Number of send-steps (terminal action excluded)


class RecoveryStepSentEvent(DomainEvent):
    """Emitted after the WhatsApp message for step N is acknowledged by Meta."""

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    step_index: int
    template_key: str
    channel: str = "whatsapp"


class RecoverySucceededEvent(DomainEvent):
    """Emitted on a payment that resolves the flow to SUCCEEDED or SUCCEEDED_DEPOSIT.

    The ``rail`` field is one of ``"paymob" | "fawry" | "kashier" |
    "instapay" | "deposit"`` — the same set Shopify additive-mutation
    tagging uses (``numu-recovery-{rail}``).

    Spec 010's positive-trust-signal recorder consumes this. Spec 002's
    rev-share billing consumes this. The dashboard rollup updater consumes
    this. All three apply their own idempotency on ``dedupe_key`` per the
    spec 009 CL-006 idempotency triple — this event is allowed to be
    delivered more than once on event-bus replay.
    """

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    rail: str
    recovered_amount_cents: int
    succeeded_as_deposit: bool = False  # True iff state == SUCCEEDED_DEPOSIT


class RecoveryBalanceCapturedEvent(DomainEvent):
    """Emitted when the *balance* of a SUCCEEDED_DEPOSIT order is later captured.

    Per spec 009 CL-006 step 2: the deposit recovery is itself a terminal
    state for billing/aggregate purposes; the eventual balance capture
    is a *separate* event so spec 002's rev-share consumer can bill the
    remainder against the same dedup key without double-counting the
    deposit portion.
    """

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    paid_amount_cents: int  # The balance — exclusive of the prior deposit
    prior_succeeded_deposit_amount_cents: int


class RecoveryAbandonedEvent(DomainEvent):
    """Emitted when the flow transitions to a non-success terminal state.

    Reason values: ``"customer_no_response" | "customer_explicit_stop" |
    "merchant_cancel" | "template_rejected" | "gateway_unavailable" |
    "uninstall"``. Reason maps loosely to the underlying terminal state
    but isn't 1:1 — multiple states can carry the same conceptual reason
    (e.g., ABANDONED + ABANDONED_PARTIAL both can be "customer_no_response").
    """

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    terminal_state: str  # The RecoveryFlowState.value reached
    reason: str


class RecoveryBlockedEvent(DomainEvent):
    """Emitted when the flow cannot proceed and ends in a BLOCKED_* state.

    Reason values: ``"no_gateway" | "no_template" | "feature_disabled"``.
    The dashboard surfaces these distinctly from ``RecoveryAbandonedEvent``
    so merchants can take action (connect a gateway, fix the template).
    """

    flow_id: (
        UUID | None
    )  # May be None if blocked at gating predicate before flow creation
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    reason: str


class RecoveryRefundedEvent(DomainEvent):
    """Emitted when a refund webhook fires for a previously-recovered order.

    ``bill_cycle_match`` distinguishes within-cycle (decrement rollup +
    rev-share base) from cross-cycle (issue billing credit against the
    next invoice, capped at that next cycle's gross recovered revenue per
    constitution v1.2.0 FR-011).
    """

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    refunded_amount_cents: int
    bill_cycle_match: bool


class RecoveryRefundReversedEvent(DomainEvent):
    """Emitted when a refund is itself reversed (chargeback won by merchant)."""

    flow_id: UUID
    store_id: UUID
    shopify_order_id: str
    dedupe_key: str
    reversed_amount_cents: int
