"""Pydantic request/response schemas for the marketplace endpoints."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

# Image URLs accepted on listing creation/update. We require HTTPS in
# production and restrict to a known set of CDN hosts so a developer
# can't paste an arbitrary URL whose origin later starts hosting
# malicious content (or whose owner stops paying for SSL and breaks
# the catalog).
#
# `IMAGE_HOST_ALLOWLIST` is the union of platform-managed hosts; extras
# come from `NUMU_MARKETPLACE_IMAGE_HOSTS` (comma-separated) so staging
# / regional CDNs can be added without a code deploy.
_BUILTIN_IMAGE_HOSTS = (
    "numueg.app",
    "numu.io",
    "r2.cloudflarestorage.com",
    "cdn.numueg.app",
    "images.numueg.app",
)
_EXTRA_IMAGE_HOSTS = tuple(
    h.strip().lstrip("*.").lower()
    for h in os.getenv("NUMU_MARKETPLACE_IMAGE_HOSTS", "").split(",")
    if h.strip()
)
_IS_PROD = os.getenv("ENVIRONMENT", "development") == "production"


def _validate_marketplace_image_url(value: str | None) -> str | None:
    """Reject URLs that won't render trustworthy in the catalog.

    Rules:
      - None passes through (field is optional).
      - Length cap (1 KB) — defensive against DOS or accidental data: URIs.
      - Production must be HTTPS; dev allows http://localhost(:port).
      - Host must end in one of the allowlisted suffixes. We compare on
        the suffix so `cdn.numueg.app`, `r2.cloudflarestorage.com`,
        and any future managed bucket all pass without enumeration.
    """
    if value is None or value == "":
        return value
    if len(value) > 1024:
        raise ValueError("image url too long (max 1024 chars)")
    try:
        parsed = urlparse(value)
    except ValueError as e:
        raise ValueError(f"image url is not a valid URL: {e}") from e
    if parsed.scheme not in ("http", "https"):
        raise ValueError("image url must be http or https")
    if _IS_PROD and parsed.scheme != "https":
        raise ValueError("image url must be https in production")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("image url is missing a host")
    # Localhost passthrough in dev — useful for a bring-your-own-CDN
    # workflow during local development.
    if not _IS_PROD and host in ("localhost", "127.0.0.1"):
        return value
    allow = _BUILTIN_IMAGE_HOSTS + _EXTRA_IMAGE_HOSTS
    if not any(host == suffix or host.endswith("." + suffix) for suffix in allow):
        raise ValueError(
            f"image url host '{host}' is not on the allowlist. "
            f"Upload via the marketplace image-upload endpoint or use a "
            f"configured CDN host."
        )
    return value


# ── Common ────────────────────────────────────────────────────────────────────


class MarketplaceThemeOut(BaseModel):
    id: str
    name: str
    slug: str
    description: str | None = None
    short_description: str | None = None
    price_cents: int = 0
    currency: str = "USD"
    status: str
    thumbnail_url: str | None = None
    preview_url: str | None = None
    demo_store_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = None
    supported_languages: list[str] = Field(default_factory=list)
    supported_features: dict[str, Any] = Field(default_factory=dict)
    install_count: int = 0
    average_rating: float = 0.0
    review_count: int = 0
    developer_id: str | None = None  # Hidden from public catalog


# ── Catalog ───────────────────────────────────────────────────────────────────


class CatalogListResponse(BaseModel):
    themes: list[MarketplaceThemeOut]
    total: int
    page: int
    per_page: int


class ThemeDetailResponse(MarketplaceThemeOut):
    latest_version: dict[str, Any] | None = None


# ── Developer ─────────────────────────────────────────────────────────────────


class CreateListingRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=3, max_length=64)
    description: str | None = None
    short_description: str | None = Field(default=None, max_length=500)
    price_cents: int = Field(default=0, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=10)
    thumbnail_url: str | None = None
    preview_url: str | None = None
    demo_store_url: str | None = None
    tags: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=100)
    supported_languages: list[str] = Field(default_factory=lambda: ["en", "ar"])
    supported_features: dict[str, Any] = Field(default_factory=dict)

    # Validate every URL field against the marketplace image allowlist
    # so a developer can't point thumbnails at attacker.example. Same
    # rules apply on update via UpdateListingRequest.
    _check_thumbnail = field_validator("thumbnail_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )
    _check_preview = field_validator("preview_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )
    _check_demo = field_validator("demo_store_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )


class UpdateListingRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    short_description: str | None = Field(default=None, max_length=500)
    price_cents: int | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=10)
    thumbnail_url: str | None = None
    preview_url: str | None = None
    demo_store_url: str | None = None
    tags: list[str] | None = None
    category: str | None = Field(default=None, max_length=100)
    supported_languages: list[str] | None = None
    supported_features: dict[str, Any] | None = None

    _check_thumbnail = field_validator("thumbnail_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )
    _check_preview = field_validator("preview_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )
    _check_demo = field_validator("demo_store_url")(
        lambda cls, v: _validate_marketplace_image_url(v)  # type: ignore[misc]
    )


class SubmitVersionRequest(BaseModel):
    """Submit a new version for build.

    Pair this with the existing /api/v1/themes/upload endpoint:
    1. Developer uploads ZIP -> gets back `build_id`/`zip_path`.
    2. Developer POSTs to /marketplace/developer/themes/{id}/versions
       with `version_string` + the `source_zip_path` returned by upload.
    """

    version_string: str = Field(
        min_length=5, max_length=50, description="Semver, e.g. '1.0.0'"
    )
    source_zip_path: str = Field(
        min_length=1, max_length=1024, description="Path to uploaded ZIP"
    )
    release_notes: str | None = None


class VersionStatusResponse(BaseModel):
    version_id: str
    version_string: str | None = None
    status: str
    build_log: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None


class VersionSummaryOut(BaseModel):
    id: str
    version_string: str
    status: str
    release_notes: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    checksum: str | None = None
    created_at: str | None = None


# ── Admin ─────────────────────────────────────────────────────────────────────


class PendingReviewItem(BaseModel):
    """Full review packet for an admin moderating a theme version.

    Carries everything an admin needs to decide approve/reject without
    further round-trips:
      - listing metadata (description, slug, pricing, marketing assets)
      - developer profile (email, # of themes published — flag a brand-
        new dev as higher-risk; trust experienced devs more)
      - version artifacts (bundle/css URLs, size, checksum)
      - build + security signals (build log tail, AST scan results)
      - schemas (so the admin can sanity-check the merchant-facing
        settings UI before publish)
    """

    # Version identity
    version_id: str
    version_string: str
    theme_id: str
    release_notes: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    build_log: str | None = None

    # Listing metadata
    theme_name: str | None = None
    theme_slug: str | None = None
    theme_description: str | None = None
    theme_short_description: str | None = None
    theme_category: str | None = None
    theme_tags: list[str] = []
    theme_supported_languages: list[str] = []
    theme_supported_features: dict[str, Any] = {}
    theme_status: str | None = None
    price_cents: int = 0
    currency: str = "USD"

    # Marketing assets
    thumbnail_url: str | None = None
    preview_url: str | None = None
    demo_store_url: str | None = None

    # Developer profile
    developer_id: str | None = None
    developer_email: str | None = None
    developer_name: str | None = None
    developer_total_themes: int = 0
    developer_published_themes: int = 0

    # Theme schemas — admins eyeball these to confirm the customizer
    # surface area is sane before approving. Lists/dicts both accepted
    # because Shopify-style schemas are arrays.
    settings_schema: list[Any] | dict[str, Any] | None = None
    section_schemas: list[Any] | dict[str, Any] | None = None

    # Version history: every prior version of this theme with its status.
    # Lets the admin see the developer's track record at a glance — has
    # this theme had build failures? Were prior versions rejected?
    version_history: list[dict[str, Any]] = []


class PendingReviewListResponse(BaseModel):
    pending: list[PendingReviewItem]


class ReviewDecisionRequest(BaseModel):
    decision: str = Field(description="'approve' or 'reject'")
    notes: str | None = Field(default=None, max_length=2000)

    @field_validator("decision")
    @classmethod
    def _check_decision(cls, v: str) -> str:
        if v not in ("approve", "reject"):
            raise ValueError("decision must be 'approve' or 'reject'")
        return v


class ReviewDecisionResponse(BaseModel):
    version_id: str
    status: str


# ── Store install ─────────────────────────────────────────────────────────────


class InstallThemeRequest(BaseModel):
    marketplace_theme_id: str


class InstallationResponse(BaseModel):
    installation_id: str | None = None
    marketplace_theme_id: str
    marketplace_version_id: str
    is_active: bool


class ActivateThemeRequest(BaseModel):
    marketplace_theme_id: str


class InstalledThemeOut(BaseModel):
    installation_id: str
    is_active: bool
    installed_at: str | None = None
    theme: MarketplaceThemeOut | None = None
    version: dict[str, Any] | None = None


class InstalledListResponse(BaseModel):
    installed: list[InstalledThemeOut]


# ── Paid theme purchases ─────────────────────────────────────────────────────


class CreateCheckoutSessionRequest(BaseModel):
    """Initiate a Stripe checkout for a paid theme."""

    marketplace_theme_id: str
    success_url: str = Field(
        ..., description="Where Stripe redirects after a successful charge"
    )
    cancel_url: str = Field(
        ..., description="Where Stripe redirects when the buyer cancels"
    )


class CreateCheckoutSessionResponse(BaseModel):
    purchase_id: str
    payment_intent_id: str
    client_secret: str
    amount_cents: int
    currency: str


class PurchaseOut(BaseModel):
    id: str
    marketplace_theme_id: str
    amount_cents: int
    currency: str
    status: str
    refunded_amount_cents: int = 0
    created_at: str
    theme_name: str | None = None


class PurchaseListResponse(BaseModel):
    purchases: list[PurchaseOut]


class RefundPurchaseRequest(BaseModel):
    # Optional partial refund. None = full refund of the remaining
    # un-refunded balance.
    amount_cents: int | None = None
    reason: str | None = Field(default=None, max_length=500)


# ── Reviews ─────────────────────────────────────────────────────────────────


class CreateReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None


class UpdateReviewRequest(BaseModel):
    rating: int | None = Field(default=None, ge=1, le=5)
    title: str | None = Field(default=None, max_length=200)
    body: str | None = None


class DeveloperResponseRequest(BaseModel):
    response: str = Field(..., min_length=1, max_length=2000)


class ReviewOut(BaseModel):
    id: str
    marketplace_theme_id: str
    user_id: str
    rating: int
    title: str | None = None
    body: str | None = None
    is_verified_purchase: bool = False
    developer_response: str | None = None
    developer_response_at: str | None = None
    helpful_count: int = 0
    created_at: str | None = None
    updated_at: str | None = None


class ReviewListResponse(BaseModel):
    reviews: list[ReviewOut]
    total: int
    page: int
    per_page: int
