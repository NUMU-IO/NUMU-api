"""Social connection database model."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.social_connection import SocialConnectionStatus, SocialPlatform
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class SocialConnectionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Social connection database model with tenant_id discriminator."""

    __tablename__ = "social_connections"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    platform: Mapped[SocialPlatform] = mapped_column(
        Enum(SocialPlatform, name="socialplatform", schema="public"),
        nullable=False,
    )
    platform_account_id: Mapped[str] = mapped_column(String(255), nullable=False)
    handle: Mapped[str] = mapped_column(String(255), nullable=False)
    followers: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    posts_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    access_token_encrypted: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[SocialConnectionStatus] = mapped_column(
        Enum(SocialConnectionStatus, name="socialconnectionstatus", schema="public"),
        default=SocialConnectionStatus.ACTIVE,
        nullable=False,
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    store = relationship("StoreModel", lazy="selectin")
    posts = relationship(
        "SocialPostModel", back_populates="connection", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<SocialConnectionModel(id={self.id}, platform={self.platform}, handle={self.handle})>"
