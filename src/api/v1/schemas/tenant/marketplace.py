"""Pydantic request/response schemas for the marketplace endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

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
    version_id: str
    version_string: str
    theme_id: str
    theme_name: str | None = None
    theme_slug: str | None = None
    developer_id: str | None = None
    release_notes: str | None = None
    size_bytes: int | None = None
    checksum: str | None = None
    created_at: str | None = None


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
