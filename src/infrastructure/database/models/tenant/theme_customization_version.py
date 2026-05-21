"""SQLAlchemy model for theme customization version history."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class ThemeCustomizationVersionModel(Base, UUIDMixin):
    """Version history for V3 theme customizations."""

    __tablename__ = "theme_customization_versions"
    __table_args__ = (
        Index("idx_tcv_store_id", "store_id"),
        Index("idx_tcv_store_created", "store_id", text("created_at DESC")),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    theme_id: Mapped[str] = mapped_column(String(255), nullable=False)
    settings_blob: Mapped[dict] = mapped_column(JSONB, nullable=False)
    change_summary: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("NOW()"), nullable=False
    )
    created_by: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    is_autosave: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    version_label: Mapped[str | None] = mapped_column(String(255), nullable=True)

    def __repr__(self) -> str:
        return (
            f"<ThemeCustomizationVersionModel(id={self.id}, store_id={self.store_id})>"
        )
