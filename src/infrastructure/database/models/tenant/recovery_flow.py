"""SQLAlchemy persistence for the recovery-flow aggregate (backend-021).

Four tables collaborate:

- ``recovery_flows`` — the aggregate root, one row per (store, shopify_order_id).
- ``recovery_steps`` — child of recovery_flows, one row per scheduled/sent step.
- ``recovery_monthly_rollups`` — per-store, per-store-local-month aggregate.
- ``recovery_rollup_ledger`` — append-only dedup ledger gating rollup mutations
  per spec 009 CL-006 (the F-019 race).
"""

from datetime import date, datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.recovery_flow import (
    RecoveryFlowState,
    RecoveryRollupLedgerEventType,
)
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class RecoveryFlowModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Aggregate root for one COD-to-prepaid recovery flow."""

    __tablename__ = "recovery_flows"

    __table_args__ = (
        UniqueConstraint(
            "store_id", "shopify_order_id", name="uq_recovery_flow_per_order"
        ),
        Index(
            "ix_recovery_flow_store_state_created",
            "store_id",
            "state",
            "created_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    shopify_order_id: Mapped[str] = mapped_column(
        String(64), nullable=False, index=True
    )
    state: Mapped[RecoveryFlowState] = mapped_column(
        Enum(
            RecoveryFlowState,
            name="recovery_flow_state_enum",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    cadence: Mapped[list[dict]] = mapped_column(
        JSONB, nullable=False, server_default="'[]'"
    )
    current_step_index: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    payment_link_session_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    recovered_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    recovered_via_rail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class RecoveryStepModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One step (send attempt) within a recovery flow."""

    __tablename__ = "recovery_steps"

    __table_args__ = (
        UniqueConstraint(
            "flow_id", "step_index", name="uq_recovery_step_per_flow_index"
        ),
        Index("ix_recovery_step_flow_id", "flow_id"),
        {"schema": "public"},
    )

    flow_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.recovery_flows.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_index: Mapped[int] = mapped_column(Integer, nullable=False)
    template_key: Mapped[str] = mapped_column(String(128), nullable=False)
    channel: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="'whatsapp'"
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failed_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class RecoveryMonthlyRollupModel(Base, TenantMixin, TimestampMixin):
    """Per-store, per-month write-through aggregate.

    Composite PK (store_id, month_key) means one row per store per
    store-local calendar month per constitution v1.2.0 FR-011. Updates
    are atomic via INSERT ... ON CONFLICT DO UPDATE, gated by the
    :class:`RecoveryRollupLedgerModel` dedup ledger.
    """

    __tablename__ = "recovery_monthly_rollups"
    __table_args__ = (
        Index("ix_recovery_rollup_updated_at", "updated_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    month_key: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        comment="First day of store-local calendar month per constitution v1.2.0 FR-011",
    )
    recovered_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    recovered_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )


class RecoveryRollupLedgerModel(Base, TenantMixin):
    """Append-only ledger gating per-event rollup mutations (spec 009 CL-006).

    The composite PK ``(store_id, shopify_order_id, event_type)`` prevents
    the same rollup increment from being applied twice on Celery retry.
    Deliberately omits :class:`TimestampMixin` ``updated_at`` — rows are
    immutable once written; only ``applied_at`` is meaningful.
    """

    __tablename__ = "recovery_rollup_ledger"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    shopify_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[RecoveryRollupLedgerEventType] = mapped_column(
        Enum(
            RecoveryRollupLedgerEventType,
            name="recovery_rollup_ledger_event_type_enum",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        primary_key=True,
    )
    applied_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    applied_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
