"""Access request entity."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class AccessRequestStatus(StrEnum):
    """Status of an access request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class AccessRequest:
    """Access request entity."""

    id: UUID
    tenant_id: UUID
    requester_user_id: UUID
    requested_role_ids: tuple[UUID, ...]
    requested_permissions: tuple[str, ...]
    justification: str | None
    status: AccessRequestStatus
    reviewer_user_id: UUID | None = None
    reviewed_at: datetime | None = None
    review_reason: str | None = None
    expires_at: datetime | None = None

    @property
    def is_expired(self) -> bool:
        """Check if request has expired."""
        if self.expires_at is None:
            return False
        return datetime.utcnow() > self.expires_at

    @property
    def is_pending(self) -> bool:
        """Check if request is still pending."""
        return self.status == AccessRequestStatus.PENDING and not self.is_expired

    @property
    def is_awaiting_review(self) -> bool:
        """Check if request is awaiting review."""
        return self.status == AccessRequestStatus.PENDING
