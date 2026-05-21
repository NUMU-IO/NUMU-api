"""Staff invitation entity."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class InvitationStatus(StrEnum):
    """Status of a staff invitation."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REVOKED = "revoked"
    EXPIRED = "expired"


@dataclass(frozen=True)
class StaffInvitation:
    """Staff invitation entity."""

    id: UUID
    tenant_id: UUID
    email: str
    token_hash: str
    pre_assigned_role_ids: tuple[UUID, ...]
    invited_by_id: UUID | None
    expires_at: datetime
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None
    resent_count: int = 0
    message: str | None = None
    status: InvitationStatus = InvitationStatus.PENDING

    @property
    def is_expired(self) -> bool:
        """Check if invitation has expired."""
        return datetime.utcnow() > self.expires_at

    @property
    def is_valid(self) -> bool:
        """Check if invitation is still valid."""
        return (
            self.status == InvitationStatus.PENDING
            and not self.is_expired
            and self.revoked_at is None
        )
