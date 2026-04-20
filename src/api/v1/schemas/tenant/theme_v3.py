"""Pydantic request/response schemas for V3 theme editor endpoints."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class AutosaveDraftRequest(BaseModel):
    payload: dict[str, Any]
    change_summary: Optional[str] = None


class AutosaveDraftResponse(BaseModel):
    success: bool = True
    draft: dict[str, Any]


class PublishResponse(BaseModel):
    success: bool = True
    published: dict[str, Any]


class VersionListResponse(BaseModel):
    versions: list[dict[str, Any]]
    page: int
    per_page: int


class RestoreVersionRequest(BaseModel):
    version_id: UUID


class DiscardDraftResponse(BaseModel):
    success: bool = True
    published: dict[str, Any]


class SchemaResponse(BaseModel):
    settings_schema: dict[str, Any] = Field(default_factory=dict)
    section_schemas: dict[str, Any] = Field(default_factory=dict)
    block_schemas: dict[str, Any] = Field(default_factory=dict)
    theme_type: str = "internal"  # "internal" or "external"
