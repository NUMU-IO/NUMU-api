"""Page view database model (public schema with tenant_id discriminator)."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    UUIDMixin,
)


class PageViewModel(Base, UUIDMixin, TenantMixin):
    """Page view database model with tenant_id discriminator.

    Append-only — no updated_at needed, only created_at.
    """

    __tablename__ = "page_views"
    __table_args__ = (
        Index("ix_page_views_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    session_fingerprint: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=True
    )
    ip_address: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(500), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return f"<PageViewModel(id={self.id}, path={self.path})>"
