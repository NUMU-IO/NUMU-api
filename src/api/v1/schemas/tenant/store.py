"""Store Pydantic schemas."""

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from src.api.dependencies.sanitization import SanitizedStr
from src.api.v1.schemas.tenant.product import _validate_size_chart
from src.application.dto.phone_field import PhoneField


class CreateStoreRequest(BaseModel):
    """Create store request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Nile Fashion",
                "subdomain": "nilefashion",
                "description": "Premium Egyptian fashion and accessories",
                "default_currency": "EGP",
                "country": "EG",
                "default_language": "ar",
                "contact_email": "hello@nilefashion.com",
                "contact_phone": "+201001234567",
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
        description="Store subdomain (e.g., 'mystore' for mystore.numueg.app)",
    )
    slug: str | None = Field(
        None,
        max_length=255,
        description="URL-friendly slug; auto-generated from name if omitted",
    )
    description: str | None = Field(None, description="Short store description")
    default_currency: str | None = Field(
        default=None,
        max_length=3,
        description=(
            "ISO 4217 default currency. When omitted, the store's market "
            "(country) default is used — e.g. SAR for SA, EGP for EG."
        ),
    )
    country: str = Field(
        default="EG",
        pattern="^[A-Za-z]{2}$",
        description=(
            "ISO 3166-1 alpha-2 market code (e.g. 'EG', 'SA'). Drives the "
            "tax jurisdiction, default currency/language, and gateway "
            "allow-list. Unknown codes fall back to Egypt."
        ),
    )
    default_language: str = Field(
        default="en", pattern="^(en|ar)$", description="Default language: en or ar"
    )
    contact_email: EmailStr | None = Field(None, description="Public contact email")

    @field_validator("country", mode="after")
    @classmethod
    def _uppercase_country(cls, v: str) -> str:
        return v.upper()

    contact_phone: PhoneField = Field(
        None,
        description=(
            "Public contact phone. Accepts E.164 or {country_code, local}; "
            "stored as canonical E.164."
        ),
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
    subdomain: str | None = Field(
        None,
        min_length=3,
        max_length=63,
        description=(
            "New store subdomain (e.g. 'mystore' for mystore.numueg.app). "
            "Must be available and not reserved; the old subdomain is "
            "released immediately."
        ),
    )
    description: SanitizedStr | None = Field(None, description="Store description")
    logo_url: str | None = Field(
        None, max_length=500, description="Store logo image URL"
    )
    banner_url: str | None = Field(
        None, max_length=500, description="Store banner image URL"
    )
    contact_email: EmailStr | None = Field(None, description="Public contact email")
    contact_phone: PhoneField = Field(
        None,
        description=(
            "Public contact phone. Accepts E.164 or {country_code, local}; "
            "stored as canonical E.164."
        ),
    )
    address: dict | None = Field(None, description="Store physical address")
    social_links: dict | None = Field(
        None, description="Social media links (e.g., {instagram: '...'})"
    )
    default_language: str | None = Field(
        None, pattern="^(en|ar)$", description="Default language: en or ar"
    )
    status: str | None = Field(
        None,
        pattern="^(active|inactive)$",
        description="Store status: active or inactive",
    )
    settings: dict | None = Field(None, description="Store-level settings")
    theme_settings: dict | None = Field(None, description="Storefront theme settings")
    business_hours: dict | None = Field(
        None,
        description=(
            "Per-day business hours, e.g. "
            '{"timezone":"Africa/Cairo","days":{"mon":{"open":"09:00",'
            '"close":"22:00","closed":false}, ...}}'
        ),
    )
    country: str | None = Field(
        None,
        pattern="^[A-Za-z]{2}$",
        description="Market country code (e.g. EG, SA). Re-resolves the market.",
    )
    default_currency: str | None = Field(
        None, max_length=3, description="ISO 4217 currency (e.g. EGP, SAR)"
    )

    @field_validator("settings", mode="after")
    @classmethod
    def _normalize_settings(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        # Validate the store-default size chart if present; leaves other
        # keys (payment creds, feature flags, etc.) untouched.
        return _validate_size_chart(v)


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
                "store_url": "https://nilefashion.numueg.app",
                "owner_id": "550e8400-e29b-41d4-a716-446655440000",
                "description": "Premium Egyptian fashion",
                "logo_url": "https://cdn.numu.com/stores/logo.png",
                "banner_url": None,
                "status": "active",
                "default_currency": "EGP",
                "country": "EG",
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
    country: str = Field(default="EG", description="ISO 3166-1 alpha-2 market code")
    default_language: str = Field(description="Default language: en or ar")
    contact_email: str | None = Field(description="Public contact email")
    contact_phone: str | None = Field(description="Public contact phone")
    address: dict = Field(description="Physical address object")
    social_links: dict = Field(description="Social media links")
    settings: dict = Field(default_factory=dict, description="Store-level settings")
    theme_settings: dict = Field(description="Storefront theme configuration")
    business_hours: dict = Field(
        default_factory=dict, description="Per-day business hours configuration"
    )
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


# ── Custom domain (Cloudflare for SaaS) ──────────────────────────────────────


class ConnectCustomDomainRequest(BaseModel):
    """Connect a merchant-owned custom domain to the store."""

    domain: str = Field(
        ...,
        min_length=4,
        max_length=255,
        description="Fully-qualified domain, e.g. 'shop.mybrand.com'",
    )


class CustomDomainDnsRecord(BaseModel):
    """A DNS record the merchant must add at their registrar."""

    type: str = Field(description="Record type, e.g. 'CNAME' or 'TXT'")
    name: str = Field(description="Record name/host")
    value: str = Field(description="Record value/target")


class CustomDomainStatusResponse(BaseModel):
    """Current state of the store's custom domain.

    `status` is the high-level lifecycle the hub renders:
      - pending_dns: registered, waiting for the merchant's CNAME + cert
      - verifying:   CNAME seen, Cloudflare is issuing/validating the cert
      - active:      cert issued, domain live
      - failed:      validation error (see `errors`)
      - none:        no custom domain connected
    """

    connected: bool = Field(description="Whether a custom domain is set")
    domain: str | None = Field(None, description="The connected domain")
    status: str = Field("none", description="Lifecycle status (see schema doc)")
    ssl_status: str | None = Field(None, description="Raw Cloudflare cert status")
    is_active: bool = Field(False, description="Cert issued and domain serving")
    # The single CNAME the merchant adds; shown as the primary instruction.
    cname: CustomDomainDnsRecord | None = Field(
        None, description="CNAME record to add at the registrar"
    )
    # Optional extra ownership-verification records CF may require.
    verification: list[CustomDomainDnsRecord] = Field(
        default_factory=list, description="Extra DCV records, if any"
    )
    errors: list[str] = Field(
        default_factory=list, description="Human-readable validation errors"
    )
    checked_at: str | None = Field(None, description="When status was last polled")
