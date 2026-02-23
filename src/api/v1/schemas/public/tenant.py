"""Tenant Pydantic schemas for API requests/responses."""

import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class CreateTenantRequest(BaseModel):
    """Request schema for creating a new tenant/store."""

    name: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Display name for the store",
        examples=["My Awesome Store"],
    )
    subdomain: str = Field(
        ...,
        min_length=3,
        max_length=63,
        description="Unique subdomain for the store (e.g., 'mystore' for mystore.octyrafiy.com)",
        examples=["mystore", "fashion-hub"],
    )
    plan: str = Field(
        default="free",
        description="Subscription plan",
        examples=["free", "pro", "enterprise"],
    )

    @field_validator("subdomain")
    @classmethod
    def validate_subdomain(cls, v: str) -> str:
        """Validate subdomain format (RFC 1123 compliant)."""
        v = v.lower().strip()

        # Check length
        if len(v) < 3 or len(v) > 63:
            raise ValueError("Subdomain must be between 3 and 63 characters")

        # Must be lowercase alphanumeric with hyphens, no start/end with hyphen
        pattern = r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?$"
        if not re.match(pattern, v):
            raise ValueError(
                "Subdomain must contain only lowercase letters, numbers, and hyphens. "
                "Cannot start or end with a hyphen."
            )

        # Reserved subdomains
        reserved = {
            "www",
            "api",
            "admin",
            "app",
            "dashboard",
            "mail",
            "ftp",
            "localhost",
        }
        if v in reserved:
            raise ValueError(f"Subdomain '{v}' is reserved and cannot be used")

        return v

    @field_validator("plan")
    @classmethod
    def validate_plan(cls, v: str) -> str:
        """Validate subscription plan."""
        allowed_plans = {"free", "demo", "starter", "pro", "enterprise"}
        if v.lower() not in allowed_plans:
            raise ValueError(f"Plan must be one of: {', '.join(sorted(allowed_plans))}")
        return v.lower()


class UpdateTenantRequest(BaseModel):
    """Request schema for updating tenant settings."""

    name: str | None = Field(None, min_length=1, max_length=255)
    plan: str | None = None
    is_active: bool | None = None
    settings: dict | None = None


class TenantResponse(BaseModel):
    """Response schema for tenant data."""

    id: UUID
    name: str
    subdomain: str
    plan: str
    is_active: bool
    owner_id: UUID | None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class TenantCreatedResponse(BaseModel):
    """Response schema for successful tenant creation."""

    message: str = "Store created successfully"
    tenant: TenantResponse
    store_url: str = Field(
        ...,
        description="Full URL to access the new store",
        examples=["https://mystore.octyrafiy.com"],
    )
