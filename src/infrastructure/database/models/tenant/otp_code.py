"""WhatsApp OTP storage (backend-025 / spec 015).

One row per issuance attempt. Cleartext code is NEVER stored — only the
HMAC hash via the platform pepper. ``verified_at`` acts as the
idempotency gate: re-verifying an already-verified row is a no-op
(returns the same verdict without re-emitting events).
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class OtpCodeModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """One issuance of a 6-digit WhatsApp OTP."""

    __tablename__ = "otp_codes"
    __table_args__ = (
        Index("ix_otp_codes_phone_hash", "phone_hash"),
        Index("ix_otp_codes_store_phone", "store_id", "phone_hash"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    phone_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    language: Mapped[str] = mapped_column(
        String(2),
        nullable=False,
        server_default="'ar'",
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    attempts_left: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        server_default="3",
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    failed_send_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
