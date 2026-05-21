"""Staff invitation database model (public schema).

Tracks invitation tokens for staff onboarding with expiry and resend limits.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class StaffInvitationModel(Base, UUIDMixin, TimestampMixin):
    """Staff invitation model.

    Invitations are sent to new staff members. They include a secure token
    that can be used to accept the invitation and join the tenant.
    """

    __tablename__ = "staff_invitations"
    __table_args__ = (
        Index("ix_staff_invitations_tenant_email", "tenant_id", "email", unique=True),
        Index("ix_staff_invitations_token_hash", "token_hash"),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    pre_assigned_role_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    invited_by_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<StaffInvitationModel(email={self.email}, tenant_id={self.tenant_id})>"
