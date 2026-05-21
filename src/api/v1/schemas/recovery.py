"""Pydantic response schemas for the recovery API (backend-021).

Mirrors the shape spec 009's dashboard widget + recoveries list view
(`numu-payments-intelligence/specs/009-cod-recovery-engine` US4) consumes.
"""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, Field


class RecoveryStepResponse(BaseModel):
    """Single step within a flow's timeline (US4 AS-4)."""

    step_index: int
    template_key: str
    channel: str
    scheduled_for: datetime
    sent_at: datetime | None
    opened_at: datetime | None
    delivered_at: datetime | None
    failed_reason: str | None


class RecoveryFlowSummaryResponse(BaseModel):
    """Compact flow representation for the recoveries list (US4 AS-3)."""

    id: UUID = Field(description="The canonical flow_id")
    store_id: UUID
    shopify_order_id: str
    state: str
    current_step_index: int
    recovered_amount_cents: int | None
    recovered_via_rail: str | None
    refunded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RecoveryFlowDetailResponse(RecoveryFlowSummaryResponse):
    """Full flow detail including timeline (US4 AS-4)."""

    cadence: list[dict]
    payment_link_session_id: UUID | None
    steps: list[RecoveryStepResponse]


class RecoveryRollupResponse(BaseModel):
    """Per-store, per-month rollup for the dashboard headline tile (US4 AS-1)."""

    store_id: UUID
    month_key: date = Field(
        description="First day of store-local calendar month per constitution v1.2.0 FR-011"
    )
    recovered_cents: int
    recovered_count: int
    updated_at: datetime
