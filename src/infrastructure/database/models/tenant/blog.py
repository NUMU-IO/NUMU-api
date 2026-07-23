"""Blog + Article database models — merchant content marketing.

Public schema with a tenant_id discriminator (RLS), mirroring the pages
table shape. Articles carry a denormalized store_id so storefront queries
and RLS never need a join through blogs.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class BlogModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A named article collection with a unique handle per store."""

    __tablename__ = "blogs"
    __table_args__ = (
        UniqueConstraint("store_id", "handle", name="uq_blogs_store_handle"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Bilingual title / description: {"en": ..., "ar": ...}
    title: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    description: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<BlogModel(id={self.id}, handle={self.handle})>"


class ArticleModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """An article inside a blog, with a draft→scheduled→published lifecycle."""

    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("blog_id", "handle", name="uq_articles_blog_handle"),
        Index("ix_articles_store_status", "store_id", "status"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    blog_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.blogs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Bilingual text: {"en": ..., "ar": ...}; body holds rich-text HTML,
    # sanitized at RENDER time (host sanitizeHtml / SDK <RichText>).
    title: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    excerpt: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    body: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    image_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    author: Mapped[str | None] = mapped_column(String(255), nullable=True)
    tags: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # SEO overrides: {"title": {en, ar}, "description": {en, ar}}
    seo: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # draft | scheduled | published | archived (ArticleStatus values).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="draft", index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Every handle this article ever had — storefront resolves old links
    # and redirects to the canonical URL (renames never 404).
    previous_handles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    def __repr__(self) -> str:
        return (
            f"<ArticleModel(id={self.id}, handle={self.handle}, status={self.status})>"
        )
