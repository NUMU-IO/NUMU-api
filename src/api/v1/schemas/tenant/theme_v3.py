"""Pydantic request/response schemas for V3 theme editor endpoints."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.api.v1.schemas.tenant.common import SettingsSchemaShape
from src.core.entities.theme_settings_v3 import ThemeSettingsV3

# JSONB columns can store up to ~1 GB in PostgreSQL, but a customization
# that big means something is wrong (runaway preset duplication, embedded
# data URLs, etc.) and the editor's autosave debouncer can't keep up. Cap
# at 256 KB serialized — comfortably above any sane theme (today's
# my-test-theme is ~6 KB) but small enough to fail fast on accidents.
MAX_CUSTOMIZATION_BYTES = 256 * 1024


def customization_payload_size(payload: dict[str, Any]) -> int:
    """Return the serialized byte size of a customization payload.

    Used by the route layer to enforce MAX_CUSTOMIZATION_BYTES with an
    explicit 413 (Payload Too Large) rather than the generic 422 Pydantic
    would produce.
    """
    return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))


class AutosaveDraftRequest(BaseModel):
    """Request body for autosave.

    `payload` is the full V3 ThemeSettingsV3 dict. Pydantic re-parses it
    into the strongly-typed model in the service layer so the URL allowlist
    runs at the boundary as well as in storage.

    `expected_etag` is the value the client received from the most recent
    GET /draft or PUT /autosave. If it doesn't match the server's current
    etag, a different tab / device has saved since this draft was loaded
    and we'd silently overwrite their work. The route returns 409 in that
    case and the client surfaces "newer changes elsewhere; reload to
    continue".
    """

    payload: dict[str, Any] = Field(
        description="ThemeSettingsV3-shaped dict",
    )
    change_summary: str | None = Field(
        default=None,
        max_length=500,
        description="Short label describing this change for version history",
    )
    expected_etag: str | None = Field(
        default=None,
        description=(
            "Optimistic-concurrency token from the previous draft fetch. "
            "Omit on first save; mandatory afterwards to detect conflicts."
        ),
    )


class AutosaveDraftResponse(BaseModel):
    draft: dict[str, Any]
    # Echo the new etag so the client uses it for the next autosave.
    etag: str | None = None


class DraftResponse(BaseModel):
    """Wrap the draft + its etag so the client gets both in one fetch."""

    draft: dict[str, Any]
    etag: str | None = None


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
    settings_schema: SettingsSchemaShape = Field(default_factory=dict)
    section_schemas: dict[str, Any] = Field(default_factory=dict)
    block_schemas: dict[str, Any] = Field(default_factory=dict)
    theme_type: str = "internal"  # "internal" or "external"


class ResolveResponse(BaseModel):
    """Resolved storefront theme settings (always V3-shaped).

    Returned by `/resolve` for the storefront SDK to render — never includes
    draft data, only published.
    """

    theme: ThemeSettingsV3
