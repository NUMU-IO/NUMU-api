"""Access request database model (public schema).

Provides a workflow for staff to request elevated permissions with justification,
which can be approved or denied by authorized reviewers.
"""

from datetime import datetime
from enum import StrEnum
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class AccessRequestStatus(StrEnum):
    """Status of access request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AccessRequestModel(Base, UUIDMixin, TimestampMixin):
    """Access request model.

    Staff can request elevated permissions with justification. Requests
    are reviewed by authorized users (owners, staff.roles.edit).
    """

    __tablename__ = "access_requests"
    __table_args__ = (
        Index("ix_access_requests_tenant", "tenant_id"),
        Index("ix_access_requests_requester", "requester_user_id"),
        Index("ix_access_requests_status", "status"),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    requester_user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    requested_role_ids: Mapped[list[PyUUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, default=list
    )
    requested_permissions: Mapped[list[str]] = mapped_column(
        ARRAY(String(100)), nullable=False, default=list
    )
    justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[AccessRequestStatus] = mapped_column(
        Enum(AccessRequestStatus, name="accessrequeststatus", schema="public"),
        default=AccessRequestStatus.PENDING,
        nullable=False,
    )
    reviewer_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    review_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<AccessRequestModel(id={self.id}, status={self.status})>"
