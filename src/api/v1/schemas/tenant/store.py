"""Store Pydantic schemas."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from src.api.dependencies.sanitization import SanitizedStr


class CreateStoreRequest(BaseModel):
    """Create store request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Nile Fashion",
                "subdomain": "nilefashion",
                "description": "Premium Egyptian fashion and accessories",
                "default_currency": "EGP",
                "default_language": "ar",
                "contact_email": "hello@nilefashion.com",
                "contact_phone": "+201001234567",
                "invite_code": "BETA-2025",
            }
        }
    )

    name: SanitizedStr = Field(
        ..., min_length=1, max_length=255, description="Store display name"
    )
    subdomain: str = Field(
        ...,
        min_length=3,
        max_length=63,
        description="Store subdomain (e.g., 'mystore' for mystore.numu.io)",
    )
    slug: str | None = Field(
        None,
        max_length=255,
        description="URL-friendly slug; auto-generated from name if omitted",
    )
    description: str | None = Field(None, description="Short store description")
    default_currency: str = Field(
        default="EGP", max_length=3, description="ISO 4217 default currency"
    )
    default_language: str = Field(
        default="en", pattern="^(en|ar)$", description="Default language: en or ar"
    )
    contact_email: EmailStr | None = Field(None, description="Public contact email")
    contact_phone: str | None = Field(
        None, max_length=20, description="Public contact phone number"
    )
    invite_code: str | None = Field(
        None, max_length=100, description="Beta invite code (required during beta)"
    )


class UpdateStoreRequest(BaseModel):
    """Update store request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Nile Fashion Updated",
                "description": "Updated store description",
                "contact_email": "support@nilefashion.com",
            }
        }
    )

    name: SanitizedStr | None = Field(
        None, min_length=1, max_length=255, description="Store display name"
    )
    description: SanitizedStr | None = Field(None, description="Store description")
    logo_url: str | None = Field(
        None, max_length=500, description="Store logo image URL"
    )
    banner_url: str | None = Field(
        None, max_length=500, description="Store banner image URL"
    )
    contact_email: EmailStr | None = Field(None, description="Public contact email")
    contact_phone: str | None = Field(
        None, max_length=20, description="Public contact phone"
    )
    address: dict | None = Field(None, description="Store physical address")
    social_links: dict | None = Field(
        None, description="Social media links (e.g., {instagram: '...'})"
    )
    default_language: str | None = Field(
        None, pattern="^(en|ar)$", description="Default language: en or ar"
    )
    settings: dict | None = Field(None, description="Store-level settings")
    theme_settings: dict | None = Field(None, description="Storefront theme settings")


class StoreResponse(BaseModel):
    """Store response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "660e8400-e29b-41d4-a716-446655440000",
                "name": "Nile Fashion",
                "slug": "nile-fashion",
                "subdomain": "nilefashion",
                "custom_domain": None,
                "store_url": "https://nilefashion.numu.io",
                "owner_id": "550e8400-e29b-41d4-a716-446655440000",
                "description": "Premium Egyptian fashion",
                "logo_url": "https://cdn.numu.com/stores/logo.png",
                "banner_url": None,
                "status": "active",
                "default_currency": "EGP",
                "default_language": "ar",
                "contact_email": "hello@nilefashion.com",
                "contact_phone": "+201001234567",
                "address": {},
                "social_links": {},
                "theme_settings": {},
                "created_at": "2025-01-10T08:00:00Z",
                "updated_at": "2025-01-10T08:00:00Z",
            }
        },
    )

    id: str = Field(description="Store UUID")
    name: str = Field(description="Store display name")
    slug: str = Field(description="URL-friendly slug")
    subdomain: str | None = Field(description="Store subdomain")
    custom_domain: str | None = Field(description="Custom domain if configured")
    store_url: str = Field(description="Full public store URL")
    owner_id: str = Field(description="Owner user UUID")
    description: str | None = Field(description="Store description")
    logo_url: str | None = Field(description="Logo image URL")
    banner_url: str | None = Field(description="Banner image URL")
    status: str = Field(description="Store status: active, inactive, suspended")
    default_currency: str = Field(description="Default ISO 4217 currency")
    default_language: str = Field(description="Default language: en or ar")
    contact_email: str | None = Field(description="Public contact email")
    contact_phone: str | None = Field(description="Public contact phone")
    address: dict = Field(description="Physical address object")
    social_links: dict = Field(description="Social media links")
    theme_settings: dict = Field(description="Storefront theme configuration")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CheckSubdomainRequest(BaseModel):
    """Check subdomain availability request."""

    subdomain: str = Field(
        ..., min_length=3, max_length=63, description="Subdomain to check"
    )


class CheckSubdomainResponse(BaseModel):
    """Check subdomain availability response."""

    subdomain: str = Field(description="Subdomain that was checked")
    available: bool = Field(description="Whether the subdomain is available")
    message: str | None = Field(None, description="Additional information")
