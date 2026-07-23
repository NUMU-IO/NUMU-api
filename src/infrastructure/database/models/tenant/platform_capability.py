"""SQLAlchemy model for the platform capability registry (ADR-0 / ADR-6)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class PlatformCapabilityModel(Base, UUIDMixin):
    """One governed capability record.

    Platform-global, so it lives in `public` and carries no tenant_id — the
    registry describes what MAY exist, not what a given merchant has installed.
    Per-store activation stays with the existing install/enable/disable center.
    """

    __tablename__ = "platform_capabilities"
    __table_args__ = {"schema": "public"}

    slug: Mapped[str] = mapped_column(
        String(128), unique=True, nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    owner: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    lifecycle_state: Mapped[str] = mapped_column(
        String(20), server_default="draft", nullable=False, index=True
    )
    # What the capability exposes. Drives the floor tier: a
    # cross_merchant_aggregate capability can never be held below first_party,
    # regardless of what min_tier says (see PlatformCapability.effective_min_tier).
    data_classification: Mapped[str] = mapped_column(
        String(32), server_default="tenant_scoped", nullable=False
    )
    # May raise the classification floor, never lower it.
    min_tier: Mapped[str] = mapped_column(
        String(20), server_default="partner", nullable=False
    )
    # fail_open by default: a failing extension must not take a storefront or a
    # checkout down with it. fail_closed is the merchant's explicit choice.
    unavailable_behavior: Mapped[str] = mapped_column(
        String(20), server_default="fail_open", nullable=False
    )

    active_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supported_versions: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    placements: Mapped[list] = mapped_column(JSONB, server_default="[]", nullable=False)
    dependencies: Mapped[list] = mapped_column(
        JSONB, server_default="[]", nullable=False
    )
    eligibility: Mapped[dict] = mapped_column(
        JSONB, server_default="{}", nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=text("NOW()"),
        onupdate=text("NOW()"),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PlatformCapabilityModel(slug={self.slug}, kind={self.kind})>"
