"""Waitlist request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr


class JoinWaitlistRequest(BaseModel):
    """Public waitlist signup request."""

    email: EmailStr
    name: SanitizedStr | None = Field(None, max_length=255)
    company_name: SanitizedStr | None = Field(None, max_length=255)
    phone: str | None = Field(None, max_length=20)
    referral_code: str | None = Field(None, max_length=20)
    source: str | None = Field(None, max_length=50)


class WaitlistPositionResponse(BaseModel):
    """Response after joining the waitlist."""

    id: UUID
    email: str
    referral_code: str
    position: int
    message: str


class WaitlistEntryResponse(BaseModel):
    """Admin-facing waitlist entry response."""

    id: UUID
    email: str
    name: str | None
    company_name: str | None
    phone: str | None
    status: str
    priority_score: int
    referral_code: str | None
    referral_count: int
    invite_code: str | None
    invited_at: datetime | None
    converted_at: datetime | None
    source: str | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class InviteWaitlistRequest(BaseModel):
    """Admin request to send beta invite."""

    entry_id: UUID


class DirectInviteRequest(BaseModel):
    """Admin request to create a new entry and immediately invite them."""

    email: EmailStr
    name: SanitizedStr | None = Field(None, max_length=255)
    company_name: SanitizedStr | None = Field(None, max_length=255)
    notes: str | None = Field(None, max_length=500)


class UpdatePriorityRequest(BaseModel):
    """Admin request to update priority score."""

    priority_score: int = Field(ge=0, le=1000)
    notes: str | None = Field(None, max_length=500)
