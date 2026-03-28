"""Social post database model."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class SocialPostModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Social post database model with tenant_id discriminator."""

    __tablename__ = "social_posts"
    __table_args__ = {"schema": "public"}

    social_connection_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.social_connections.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform_post_id: Mapped[str] = mapped_column(String(255), nullable=False)
    image_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    likes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    comments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    suggested_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suggested_name_ar: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suggested_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    imported_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    product_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="SET NULL"),
        nullable=True,
    )

    # Relationships
    connection = relationship(
        "SocialConnectionModel", back_populates="posts", lazy="selectin"
    )
    product = relationship("ProductModel", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"<SocialPostModel(id={self.id}, platform_post_id={self.platform_post_id})>"
        )
