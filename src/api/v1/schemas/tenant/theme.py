"""Schemas for external theme management (BYOT)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from src.api.v1.schemas.tenant.common import SettingsSchemaShape


class ThemeBuildStatus(StrEnum):
    """Status of an external theme build."""

    QUEUED = "queued"
    CLONING = "cloning"
    VALIDATING = "validating"
    BUILDING = "building"
    UPLOADING = "uploading"
    COMPLETE = "complete"
    FAILED = "failed"


# ─── Requests ────────────────────────────────────────────────────────────────


class SubmitExternalThemeRequest(BaseModel):
    """Request to submit an external theme from a GitHub repository."""

    github_url: str = Field(
        ...,
        description="Public GitHub repository URL (e.g., https://github.com/user/my-numu-theme)",
        pattern=r"^https://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+/?$",
    )
    branch: str = Field(
        default="main",
        description="Branch to build from",
    )


class RebuildExternalThemeRequest(BaseModel):
    """Request to rebuild the external theme using its stored source_repo."""

    branch: str = Field(
        default="main",
        description="Branch to rebuild from",
    )


class RemoveExternalThemeRequest(BaseModel):
    """Request to remove an external theme and revert to built-in."""

    fallback_theme: str = Field(
        default="modern",
        description="Built-in theme to revert to after removal",
    )


class ConnectDevServerRequest(BaseModel):
    """Request to connect a local theme dev server to a store.

    The dev_url should point to a running `numu-theme dev` server
    (e.g., http://localhost:4321 or a tunneled URL like https://abc.ngrok.io).
    The backend fetches the theme manifest from the dev server and registers
    the theme as an external theme with mode="dev" so the storefront knows
    to bypass caching.
    """

    dev_url: str = Field(
        ...,
        description="URL of the running numu-theme dev server (e.g., http://localhost:4321)",
        pattern=r"^https?://[^\s]+$",
    )


# ─── Responses ───────────────────────────────────────────────────────────────


class ThemeBuildResponse(BaseModel):
    """Response after submitting a theme for building."""

    build_id: str
    status: ThemeBuildStatus
    message: str


class ThemeBuildStatusResponse(BaseModel):
    """Response for checking build status."""

    build_id: str
    status: ThemeBuildStatus
    theme_id: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    error: str | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


class ExternalThemeInfoResponse(BaseModel):
    """Response with current external theme info for a store."""

    has_external_theme: bool
    theme_id: str | None = None
    bundle_url: str | None = None
    css_url: str | None = None
    version: str | None = None
    source_repo: str | None = None
    built_at: datetime | None = None


class StoreThemeListItem(BaseModel):
    """A single theme entry for the merchant dashboard themes list."""

    id: str
    name: str
    nameAr: str
    layout: str
    description: str
    is_external: bool = False
    bundle_url: str | None = None
    css_url: str | None = None
    version: str | None = None
    source_repo: str | None = None
    # Shape covered by SettingsSchemaShape — see common.py for why this is
    # always list-or-dict.
    settings_schema: SettingsSchemaShape | None = None
    # Optional sections.json manifest extracted from the bundle. Each entry
    # describes a section type the bundle ships — either an OVERRIDE of an
    # existing shared section type or a brand-NEW type with its own schema.
    # Used by the dashboard's section picker so merchants can drop external
    # sections into templates.
    section_schemas: dict | None = None
    mode: str | None = None  # "dev" for local dev server, None/missing for production


class StoreThemesListResponse(BaseModel):
    """Response with all themes available to a store (built-in + external)."""

    themes: list[StoreThemeListItem]
    active_theme_id: str | None = None


from pydantic import BaseModel


class ValidationErrorModel(BaseModel):
    file: str
    message: str
    severity: str


class ThemeValidationResponse(BaseModel):
    valid: bool
    errors: list[ValidationErrorModel]
    warnings: list[ValidationErrorModel]
    contract_version: str | None = None
