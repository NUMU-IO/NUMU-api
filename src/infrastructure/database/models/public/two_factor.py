"""Two-Factor Authentication database model (public schema)."""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class TwoFactorAuthModel(Base, UUIDMixin, TimestampMixin):
    """Stores TOTP secrets and backup codes for merchant 2FA."""

    __tablename__ = "two_factor_auth"
    __table_args__ = {"schema": "public"}

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )
    method: Mapped[str] = mapped_column(String(20), nullable=False, default="totp")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="disabled")
    # TOTP secret (base32-encoded) — stored in plaintext (required for HOTP/TOTP math)
    secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Hashed backup codes stored as a Postgres text array
    backup_codes: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    backup_codes_remaining: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<TwoFactorAuthModel(user_id={self.user_id}, status={self.status})>"
