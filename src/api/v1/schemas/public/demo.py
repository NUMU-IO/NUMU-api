"""Try-a-Demo request/response schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from src.api.v1.schemas.public.attribution import AttributionPayload
from src.application.dto.phone_field import RequiredPhoneField


class StartDemoRequest(BaseModel):
    # Name, email and WhatsApp are all required so every demo is
    # attributable to a reachable person, not just an inbox. WhatsApp was
    # optional here for exactly the reason you would expect — requiring it
    # costs top-of-funnel conversions — and the result was a pile of demo
    # tenants nobody could follow up on. In this market WhatsApp is the
    # channel that gets read, so an unreachable lead is not a lead. Stored
    # as E.164 by the same validator the register endpoint uses.
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    whatsapp: RequiredPhoneField
    language: Literal["ar", "en"] = "ar"
    turnstile_token: str | None = Field(None, max_length=2048)
    niche: Literal["fashion"] = "fashion"
    # Where this visitor came from. Optional on the wire so an older
    # cached landing bundle keeps working through the rollout.
    attribution: AttributionPayload | None = None


class StartDemoResponse(BaseModel):
    # "created" = new demo provisioned, tokens returned.
    # "magic_link_sent" = email already belongs to an existing user;
    # a login link was emailed and tokens are omitted.
    status: Literal["created", "magic_link_sent"] = "created"
    tenant_id: UUID | None = None
    store_id: UUID | None = None
    subdomain: str | None = None
    expires_at: datetime | None = None
    dashboard_url: str | None = None
    storefront_url: str | None = None
    # Tokens are also set as cookies, but returned in the body so the
    # landing page can pass them via URL params to the merchant hub
    # (cross-origin token handoff).
    access_token: str | None = None
    refresh_token: str | None = None
    message: str


class ConvertDemoRequest(BaseModel):
    """Promote a demo tenant to a real account. No payment required —
    the user lands in a 30-day trial."""

    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    store_name: str = Field(min_length=1, max_length=255)
    subdomain: str = Field(min_length=3, max_length=63)
    phone: str | None = Field(None, max_length=20)


class ConvertDemoResponse(BaseModel):
    tenant_id: UUID
    subdomain: str
    message: str
