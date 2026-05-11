"""Risk-assessment lifecycle events.

Emitted by the risk-scoring path (``risk_scoring_tasks.py``) at each stage
of the constitution's hybrid sync/async scoring contract:

- :class:`RiskAssessmentPreliminaryEvent` — fires after the 200ms fast-score
  inside the orders/create webhook handler. Carries the preliminary 2-factor
  score so downstream "fire-and-forget" listeners (e.g., spec 004 Flow
  trigger emitter with ``score_type='preliminary'``) can act on it.

- :class:`RiskAssessmentFinalisedEvent` — fires after the async 5-factor
  computation completes (spec 000 SC-002: 95% within 10s). This is the
  trigger for ``backend-021-recovery-flow-aggregate`` US1 — the recovery
  flow only spawns on the *finalised* score, never on the preliminary,
  because preliminary scores are unstable and would create flows that
  immediately become orphaned.

These are new event classes added in support of backend-021 + backend-022;
the existing risk-scoring code emits no domain events today (it mutates
the ``RiskAssessment`` row directly), so adding these is part of those
specs' implementation work.
"""

from __future__ import annotations

from uuid import UUID

from src.core.events.base import DomainEvent


class RiskAssessmentPreliminaryEvent(DomainEvent):
    """Emitted after the synchronous 200ms preliminary risk score lands."""

    assessment_id: UUID
    store_id: UUID
    shopify_order_id: str | None
    order_id: UUID | None
    risk_score: int
    risk_level: str
    score_type: str = "preliminary"


class RiskAssessmentFinalisedEvent(DomainEvent):
    """Emitted after the async full 5-factor risk score is persisted.

    Carries the finalised score plus the gating preconditions needed by
    backend-021's flow-creation consumer (recovery_enabled, payment
    gateway connected, subscription active). Pre-computing these in the
    event payload avoids three round-trip queries in the hot
    flow-creation path.
    """

    assessment_id: UUID
    tenant_id: UUID  # Required for RLS context in async event handlers
    store_id: UUID
    shopify_order_id: str | None
    order_id: UUID | None
    customer_phone: str | None  # Used by spec 010 trust-signal lookup; never logged
    risk_score: int
    risk_level: str
    score_type: str = "final"
    # Gating predicates pre-resolved at the publish site so consumers don't
    # re-query them. Each consumer (recovery, automation, trust auto-approve)
    # may apply additional filters.
    recovery_enabled: bool = False
    has_payment_gateway: bool = False
    subscription_active: bool = False
