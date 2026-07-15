"""Merchant signals (public schema with tenant_id discriminator).

The write model behind the AI Commerce Intelligence layer: every piece of
advice, opportunity, or alert the rule engine produces is one row here.
One write model → several read surfaces (advisor feed, alerts, executive
dashboard) stay consistent by construction.

Rows carry the rule id + a metrics snapshot, NOT rendered copy — the
bilingual text is rendered at read time from the rule's templates, so
copy fixes never need a data migration. ``expected_impact_cents`` is the
computed (never guessed) EGP effect used to rank signals.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    UUIDMixin,
)

SIGNAL_KINDS = ("advice", "opportunity", "alert")
SIGNAL_SEVERITIES = ("critical", "warning", "opportunity", "info")
SIGNAL_STATUSES = ("active", "dismissed", "resolved", "expired")


class MerchantSignalModel(Base, UUIDMixin, TenantMixin):
    """One actionable signal for a store (advice / opportunity / alert)."""

    __tablename__ = "merchant_signals"
    __table_args__ = (
        Index("ix_merchant_signals_store_status", "store_id", "status"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plain strings validated at the application layer — no PG enums
    # (the orderstatus lowercase-value lesson).
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="advice")
    rule_id: Mapped[str] = mapped_column(String(50), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="info")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", index=True
    )
    # Values the rule fired on — interpolated into the bilingual
    # templates at read time and kept for the audit trail.
    metrics_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    expected_impact_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # While now < cooldown_until, a re-fire refreshes the snapshot but
    # must not notify again (anti-noise).
    cooldown_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<MerchantSignalModel(store={self.store_id}, {self.rule_id} "
            f"[{self.severity}/{self.status}])>"
        )
