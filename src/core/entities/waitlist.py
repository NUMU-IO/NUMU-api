"""Waitlist entity for beta launch merchant onboarding."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import ConfigDict, Field

from src.core.entities.base import BaseEntity, _utc_now


class WaitlistStatus(StrEnum):
    """Waitlist entry status."""

    PENDING = "pending"
    INVITED = "invited"
    CONVERTED = "converted"


class WaitlistEntry(BaseEntity):
    """Waitlist entry for beta merchant signups.

    Tracks prospective merchants from initial signup through
    beta invitation to store creation (conversion).
    """

    email: str
    name: str | None = None
    company_name: str | None = None
    phone: str | None = None
    status: WaitlistStatus = WaitlistStatus.PENDING
    priority_score: int = Field(default=0, ge=0, le=1000)

    # Referral tracking
    referral_code: str | None = None
    referred_by: UUID | None = None
    referral_count: int = 0

    # Invite tracking
    invite_code: str | None = None
    invited_at: datetime | None = None
    converted_at: datetime | None = None

    # Metadata
    source: str | None = None  # e.g. "landing_page", "referral", "social"
    notes: str | None = None

    model_config = ConfigDict(
        validate_assignment=True,
        from_attributes=True,
        use_enum_values=False,
        populate_by_name=True,
    )

    def invite(self, invite_code: str) -> None:
        """Mark entry as invited with the given code."""
        self.status = WaitlistStatus.INVITED
        self.invite_code = invite_code
        self.invited_at = _utc_now()
        self.touch()

    def convert(self) -> None:
        """Mark entry as converted (store created)."""
        self.status = WaitlistStatus.CONVERTED
        self.converted_at = _utc_now()
        self.touch()

    def bump_priority(self, amount: int = 10) -> None:
        """Increase priority score (capped at 1000)."""
        self.priority_score = min(self.priority_score + amount, 1000)
        self.touch()

    @property
    def is_invitable(self) -> bool:
        """Check if entry can be sent an invite."""
        return self.status == WaitlistStatus.PENDING
