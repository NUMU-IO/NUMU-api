"""Theme update notification model (Phase 5.1).

Public schema, tenant_id discriminator (RLS), one row per (store, target
version). Additive — no changes to existing tables, safe for production
stores (they simply have no notification rows until a version bump is
detected).
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ThemeUpdateNotificationModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A theme-version update awaiting merchant review/apply for one store."""

    __tablename__ = "marketplace_theme_update_notifications"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "to_version_id", name="uq_theme_update_store_version"
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # marketplace_themes.id — not FK-constrained to keep the table additive
    # and decoupled from the marketplace tables' lifecycle.
    theme_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )
    from_version_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    to_version_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    from_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    to_version: Mapped[str] = mapped_column(String(50), nullable=False, default="")
    classification: Mapped[str] = mapped_column(
        String(16), nullable=False, default="automatic"
    )
    changes: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    release_notes: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", index=True
    )

    def __repr__(self) -> str:
        return (
            f"<ThemeUpdateNotificationModel(id={self.id}, "
            f"store_id={self.store_id}, to_version={self.to_version}, "
            f"status={self.status})>"
        )
