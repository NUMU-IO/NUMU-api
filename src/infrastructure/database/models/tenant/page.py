"""Page database model — merchant content pages.

Public schema with a tenant_id discriminator (RLS), one row per page.
Mirrors the menus table shape (Phase 2.1).
"""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class PageModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A merchant content page with a unique handle per store."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("store_id", "handle", name="uq_pages_store_handle"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Bilingual title / body: {"en": ..., "ar": ...}
    title: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # SEO overrides: {"title": {en, ar}, "description": {en, ar}}
    seo: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    template: Mapped[str] = mapped_column(String(64), nullable=False, default="page")
    # Alternate template variant key suffix (Shopify-style); null = base template.
    template_suffix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Reserved for full per-page section customization (Shopify parity).
    content_v3: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    def __repr__(self) -> str:
        return f"<PageModel(id={self.id}, handle={self.handle})>"
