"""Try-a-Demo request/response schemas."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field


class StartDemoRequest(BaseModel):
    email: EmailStr
    language: Literal["ar", "en"] = "ar"
    turnstile_token: str | None = Field(None, max_length=2048)
    niche: Literal["fashion"] = "fashion"


class StartDemoResponse(BaseModel):
    tenant_id: UUID
    store_id: UUID
    subdomain: str
    expires_at: datetime
    dashboard_url: str
    storefront_url: str
    # Tokens are also set as cookies, but returned in the body so the
    # landing page can pass them via URL params to the merchant hub
    # (cross-origin token handoff).
    access_token: str
    refresh_token: str
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
