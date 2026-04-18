"""Temporary access grant database model (public schema).

Provides time-bound permission grants that auto-expire via Celery beat.
"""

from datetime import datetime
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin


class TemporaryAccessGrantModel(Base, TimestampMixin):
    """Temporary access grant model.

    Provides time-bound access to roles. Celery beat expires grants
    when valid_until is reached and bumps permission_version.
    """

    __tablename__ = "temporary_access_grants"
    __table_args__ = (
        Index("ix_temporary_grants_membership", "membership_id"),
        Index("ix_temporary_grants_valid_until", "valid_until"),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    membership_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenant_memberships.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.roles.id", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    valid_until: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<TemporaryAccessGrantModel(membership={self.membership_id}, role={self.role_id})>"
