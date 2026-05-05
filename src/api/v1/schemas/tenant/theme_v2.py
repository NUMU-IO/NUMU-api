"""Pydantic schemas for the new Theme Engine API (v2).

Separate from the existing theme.py schemas to avoid breaking changes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# ── Shared sub-schemas ─────────────────────────────────────────────────────────


class ThemeVersionSummary(BaseModel):
    """Light version info embedded in theme responses."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    version: str
    is_latest: bool
    bundle_url: str
    css_url: str | None = None
    checksum: str
    size_bytes: int | None = None
    changelog: str | None = None
    published_at: str | None = None
    created_at: str


# ── Marketplace responses ──────────────────────────────────────────────────────


class ThemeListItem(BaseModel):
    """A single theme in the marketplace listing."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "uuid",
                "name": "Modern",
                "slug": "modern",
                "description": "Clean modern storefront",
                "author": "NUMU",
                "type": "internal",
                "thumbnail_url": "https://assets.numueg.app/themes/modern/thumb.png",
                "is_public": True,
                "status": "published",
                "supported_features": {"darkMode": True, "rtl": True},
                "latest_version": "1.0.0",
            }
        }
    )

    id: str
    name: str
    slug: str
    description: str | None = None
    author: str
    type: str  # internal | external
    thumbnail_url: str | None = None
    is_public: bool
    status: str
    supported_features: dict[str, Any] | None = None
    latest_version: str | None = None


class ThemeListResponse(BaseModel):
    """Paginated marketplace listing."""

    themes: list[ThemeListItem]
    total: int
    page: int
    per_page: int


class ThemeDetailResponse(BaseModel):
    """Full theme detail including all versions."""

    id: str
    name: str
    slug: str
    description: str | None = None
    author: str
    type: str
    thumbnail_url: str | None = None
    is_public: bool
    status: str
    settings_schema: dict[str, Any]
    section_schemas: dict[str, Any] | None = None
    supported_features: dict[str, Any] | None = None
    versions: list[ThemeVersionSummary]
    created_at: str
    updated_at: str


# ── Store installation requests ────────────────────────────────────────────────


class InstallThemeRequest(BaseModel):
    """Request to install a theme on a store."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "theme_id": "uuid",
                "version_id": None,
            }
        }
    )

    theme_id: str = Field(description="UUID of the theme to install")
    version_id: str | None = Field(
        default=None,
        description="Specific version UUID. Defaults to the latest version.",
    )


class CustomizeDraftRequest(BaseModel):
    """Request to save draft customization for an installation."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "draft_customization": {
                    "theme": {"primaryColor": "#ff6b6b"},
                    "header": {"sticky": True},
                }
            }
        }
    )

    draft_customization: dict[str, Any] = Field(
        description="Draft customization settings (not yet live)"
    )


# ── Store installation responses ───────────────────────────────────────────────


class StoreThemeInstallationResponse(BaseModel):
    """Full store theme installation detail."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "install-uuid",
                "store_id": "store-uuid",
                "theme_id": "theme-uuid",
                "theme_slug": "modern",
                "theme_name": "Modern",
                "theme_type": "internal",
                "theme_version": "1.0.0",
                "theme_thumbnail_url": None,
                "bundle_url": None,
                "css_url": None,
                "is_active": True,
                "has_draft_changes": False,
                "customization": {},
                "installed_at": "2026-04-11T00:00:00Z",
                "activated_at": "2026-04-11T00:01:00Z",
                "created_at": "2026-04-11T00:00:00Z",
                "updated_at": "2026-04-11T00:01:00Z",
            }
        }
    )

    id: str
    store_id: str
    theme_id: str
    theme_version_id: str
    theme_slug: str | None = None
    theme_name: str | None = None
    theme_type: str | None = None
    theme_version: str | None = None
    theme_thumbnail_url: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    is_active: bool
    has_draft_changes: bool
    customization: dict[str, Any]
    draft_customization: dict[str, Any] | None = None
    installed_at: str | None = None
    activated_at: str | None = None
    created_at: str
    updated_at: str


class StoreInstalledThemesResponse(BaseModel):
    """List of all theme installations for a store."""

    installations: list[StoreThemeInstallationResponse]
    active_installation_id: str | None = None


# ── Storefront internal API ────────────────────────────────────────────────────


class StorefrontThemeResponse(BaseModel):
    """Theme data returned to the storefront for SSR.

    Internal contract between the storefront(s) and FastAPI. Two shapes
    coexist on the same response so we don't break the older Vite SPA
    storefront mid-rollout:

    - `customization` (V1/V2 legacy flat) — what the existing Vite
      storefront reads. Preserved unchanged.
    - `customization_v3` (V3 sections/blocks) — what the Next.js
      storefront and the @numu/theme-sdk normalize against. Resolved via
      `resolve_theme_settings()` so even stores that have never been
      touched by the V3 customizer get a normalized V3 view.

    Clients should consume whichever shape matches their renderer; both
    are guaranteed to describe the same published state.
    """

    theme_id: str
    theme_slug: str
    theme_type: str  # 'internal' | 'external'
    version: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    # Legacy flat customization (kept for the Vite SPA storefront).
    customization: dict[str, Any]
    # V3-shaped customization. Always populated — when no V3 row exists,
    # it's the normalized in-memory view of the legacy data.
    customization_v3: dict[str, Any]
    settings_schema: dict[str, Any]
    section_schemas: dict[str, Any] | None = None
    installation_id: str
    # Optional integrity hint for the Next.js BYOT loader's SRI check.
    # Populated when the active version is a marketplace version that has
    # a recorded SHA-256.
    bundle_checksum: str | None = None


# ── Activation response ────────────────────────────────────────────────────────


class ActivateThemeResponse(BaseModel):
    """Response after activating a theme."""

    activated: bool
    installation: StoreThemeInstallationResponse
    message: str
