"""Pydantic request/response schemas for V3 theme editor endpoints."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from src.core.entities.theme_settings_v3 import ThemeSettingsV3


class AutosaveDraftRequest(BaseModel):
    """Request body for autosave.

    `payload` is the full V3 ThemeSettingsV3 dict. Pydantic re-parses it
    into the strongly-typed model in the service layer so the URL allowlist
    runs at the boundary as well as in storage.
    """

    payload: dict[str, Any] = Field(
        description="ThemeSettingsV3-shaped dict",
    )
    change_summary: str | None = Field(
        default=None,
        max_length=500,
        description="Short label describing this change for version history",
    )


class AutosaveDraftResponse(BaseModel):
    draft: dict[str, Any]


class PublishResponse(BaseModel):
    published: dict[str, Any]


class VersionListItem(BaseModel):
    id: str
    theme_id: str
    change_summary: str | None = None
    is_published: bool
    is_autosave: bool
    version_label: str | None = None
    created_at: str | None = None
    created_by: str | None = None


class VersionListResponse(BaseModel):
    versions: list[VersionListItem]
    page: int
    per_page: int


class DiscardDraftResponse(BaseModel):
    published: dict[str, Any]


class SchemaResponse(BaseModel):
    theme_id: str
    theme_slug: str | None = None
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    section_schemas: dict[str, Any] = Field(default_factory=dict)
    block_schemas: dict[str, Any] = Field(default_factory=dict)
    theme_type: str = "internal"  # "internal" or "external"


class ResolveResponse(BaseModel):
    """Resolved storefront theme settings (always V3-shaped).

    Returned by `/resolve` for the storefront SDK to render — never includes
    draft data, only published.
    """

    theme: ThemeSettingsV3
