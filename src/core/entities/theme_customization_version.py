"""Theme customization version entity for auto-save history."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ThemeCustomizationVersion(BaseEntity):
    """A versioned snapshot of a V3 theme customization.

    Created on every significant auto-save and on every publish.
    Enables merchants to browse history and restore previous versions.
    """

    store_id: UUID
    theme_id: str  # "bazar", "modern", or BYOT UUID
    settings_blob: dict[str, Any] = Field(default_factory=dict)
    change_summary: str | None = None
    created_by: UUID | None = None
    is_published: bool = False
    is_autosave: bool = True
    version_label: str | None = None  # Optional merchant label: "Holiday Sale"
