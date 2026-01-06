"""Store Pydantic schemas."""

from pydantic import BaseModel, EmailStr, Field


class CreateStoreRequest(BaseModel):
    """Create store request schema."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    description: str | None = None
    default_currency: str = Field(default="USD", max_length=3)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)


class UpdateStoreRequest(BaseModel):
    """Update store request schema."""

    name: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    logo_url: str | None = Field(None, max_length=500)
    banner_url: str | None = Field(None, max_length=500)
    contact_email: EmailStr | None = None
    contact_phone: str | None = Field(None, max_length=20)
    address: dict | None = None
    social_links: dict | None = None
    settings: dict | None = None


class StoreResponse(BaseModel):
    """Store response schema."""

    id: str
    name: str
    slug: str
    owner_id: str
    description: str | None
    logo_url: str | None
    banner_url: str | None
    status: str
    default_currency: str
    contact_email: str | None
    contact_phone: str | None
    address: dict
    social_links: dict
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True
