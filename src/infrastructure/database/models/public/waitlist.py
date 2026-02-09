"""Waitlist database model (public schema — not tenant-scoped)."""

from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.waitlist import WaitlistStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class WaitlistModel(Base, UUIDMixin, TimestampMixin):
    """Waitlist database model.

    Lives in the public schema because signups happen before
    any tenant context exists.
    """

    __tablename__ = "waitlist"
    __table_args__ = {"schema": "public"}

    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    company_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    status: Mapped[WaitlistStatus] = mapped_column(
        Enum(WaitlistStatus, name="waitliststatus", schema="public"),
        default=WaitlistStatus.PENDING,
        nullable=False,
        index=True,
    )
    priority_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, index=True
    )

    # Referral
    referral_code: Mapped[str | None] = mapped_column(
        String(20), nullable=True, unique=True, index=True
    )
    referred_by: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    referral_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Invite
    invite_code: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True, index=True
    )
    invited_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    converted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Metadata
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<WaitlistModel(id={self.id}, email={self.email}, status={self.status})>"
        )
