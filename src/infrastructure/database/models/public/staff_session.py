"""Staff session database model (public schema).

Tracks active staff sessions for security monitoring and revocation.
"""

from datetime import datetime
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin


class StaffSessionModel(Base, TimestampMixin):
    """Staff session model.

    Tracks active sessions for staff accounts, including IP, user agent,
    device info, and last seen timestamps. Used for session revocation.
    """

    __tablename__ = "staff_sessions"
    __table_args__ = (
        Index("ix_staff_sessions_jti", "jti", unique=True),
        Index("ix_staff_sessions_user_id", "user_id"),
        Index("ix_staff_sessions_membership_id", "membership_id"),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    jti: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    membership_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    ip: Mapped[str | None] = mapped_column(String(45), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(Text, nullable=True)
    device_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<StaffSessionModel(jti={self.jti}, user_id={self.user_id})>"
