"""Waitlist request/response schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr

# ─────────────────────────────────────────────────────────────────────────────
# Waitlist signup
# ─────────────────────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────────────────────
# Beta invite redemption (combined sign-up + create-store flow)
# ─────────────────────────────────────────────────────────────────────────────


class BetaInviteCheckResponse(BaseModel):
    """Public response describing an invite code's redemption state.

    Returned by GET /public/beta/invite/{code} so the accept-invite page can
    pre-fill the form, or — when the invite is already converted — redirect
    the user to login instead of dead-ending.

    `status` matches WaitlistStatus values: "pending" | "invited" | "converted".
    """

    email: str
    name: str | None
    company_name: str | None
    status: str


class BetaRedeemRequest(BaseModel):
    """Public request to atomically create a user + store from an invite code.

    The email used for the new user is taken from the waitlist entry tied to
    the invite — never from the request — so a forwarded invite cannot be
    redeemed into someone else's account.
    """

    invite_code: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=12, max_length=128)
    first_name: SanitizedStr = Field(..., min_length=2, max_length=50)
    last_name: SanitizedStr = Field(..., min_length=2, max_length=50)
    phone: str | None = Field(None, max_length=20)
    store_name: SanitizedStr = Field(..., min_length=3, max_length=60)
    subdomain: str = Field(..., min_length=3, max_length=63)


class BetaRedeemGoogleRequest(BaseModel):
    """Public request to redeem a beta invite via Google OAuth.

    The Google account's email must match the waitlist entry's email — we
    verify this server-side after validating the ID token.
    """

    invite_code: str = Field(..., min_length=1, max_length=100)
    id_token: str = Field(..., description="Google OAuth ID token from sign-in")
    store_name: SanitizedStr = Field(..., min_length=3, max_length=60)
    subdomain: str = Field(..., min_length=3, max_length=63)
