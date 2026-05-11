"""Flow trigger emission log (backend-020).

Records every ``flowTriggerReceive`` mutation NUMU-api sends to Shopify
on the merchant's behalf. Composite unique constraint on
``(store_id, dedup_key, trigger_handle)`` is the idempotency gate per
backend-020 FR-002.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class FlowTriggerEmissionLogModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One row per flowTriggerReceive emission attempt."""

    __tablename__ = "flow_trigger_emission_log"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "dedup_key",
            "trigger_handle",
            name="uq_flow_trigger_dedup",
        ),
        Index("ix_flow_trigger_status_attempted", "status", "attempted_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    source_event_id: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_handle: Mapped[str] = mapped_column(String(64), nullable=False)
    dedup_key: Mapped[str] = mapped_column(String(256), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        # 'pending' | 'succeeded' | 'failed_retryable' | 'failed_terminal'
        # | 'terminated_uninstall' | 'skipped_not_subscribed'
        server_default="'pending'",
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    succeeded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    error_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_snapshot: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default="'{}'",
    )
