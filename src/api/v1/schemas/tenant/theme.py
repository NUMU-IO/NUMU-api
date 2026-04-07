"""Schemas for external theme management (BYOT)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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


class RemoveExternalThemeRequest(BaseModel):
    """Request to remove an external theme and revert to built-in."""

    fallback_theme: str = Field(
        default="modern",
        description="Built-in theme to revert to after removal",
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
    settings_schema: dict | None = None  # The full schema for the customizer UI


class StoreThemesListResponse(BaseModel):
    """Response with all themes available to a store (built-in + external)."""

    themes: list[StoreThemeListItem]
    active_theme_id: str | None = None
