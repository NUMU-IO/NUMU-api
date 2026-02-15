"""Store Pydantic schemas."""

from pydantic import BaseModel, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr


class CreateStoreRequest(BaseModel):
    """Create store request schema."""

    name: SanitizedStr = Field(..., min_length=1, max_length=255)
    subdomain: str = Field(
        ...,
        min_length=3,
        max_length=63,
        description="Store subdomain (e.g., 'mystore' for mystore.numu.io)",
    )
    slug: str | None = Field(None, max_length=255)
    description: str | None = None
    default_currency: str = Field(default="EGP", max_length=3)
    default_language: str = Field(default="en", pattern="^(en|ar)$")
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)
    invite_code: str | None = Field(None, max_length=100, description="Beta invite code")


class UpdateStoreRequest(BaseModel):
    """Update store request schema."""

    name: SanitizedStr | None = Field(None, min_length=1, max_length=255)
    description: SanitizedStr | None = None
    logo_url: str | None = Field(None, max_length=500)
    banner_url: str | None = Field(None, max_length=500)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)
    address: dict | None = None
    social_links: dict | None = None
    default_language: str | None = Field(None, pattern="^(en|ar)$")
    settings: dict | None = None
    theme_settings: dict | None = None


class StoreResponse(BaseModel):
    """Store response schema."""

    id: str
    name: str
    slug: str
    subdomain: str | None
    custom_domain: str | None
    store_url: str
    owner_id: str
    description: str | None
    logo_url: str | None
    banner_url: str | None
    status: str
    default_currency: str
    default_language: str
    contact_email: str | None
    contact_phone: str | None
    address: dict
    social_links: dict
    theme_settings: dict
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class CheckSubdomainRequest(BaseModel):
    """Check subdomain availability request."""

    subdomain: str = Field(..., min_length=3, max_length=63)


class CheckSubdomainResponse(BaseModel):
    """Check subdomain availability response."""

    subdomain: str
    available: bool
    message: str | None = None
