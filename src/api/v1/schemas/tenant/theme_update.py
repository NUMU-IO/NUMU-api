"""Theme update notification request/response schemas (Phase 5.1)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ThemeUpdateNotificationResponse(BaseModel):
    """A theme-version update awaiting the merchant's decision."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Notification UUID")
    store_id: str = Field(description="Owning store UUID")
    theme_id: str = Field(description="Marketplace theme UUID")
    from_version: str = Field(description="Currently-installed version string")
    to_version: str = Field(description="New available version string")
    classification: str = Field(description="'manual' or 'automatic'")
    changes: list[dict[str, Any]] = Field(
        description="Classifier change list [{kind,target,breaking,detail}]"
    )
    release_notes: str = Field(description="Release notes for the new version")
    status: str = Field(description="'pending' | 'applied' | 'skipped'")
    created_at: str = Field(description="ISO 8601 creation timestamp")


class CheckUpdatesResponse(BaseModel):
    """Result of a scan for newer versions of the store's installed theme."""

    notifications: list[ThemeUpdateNotificationResponse] = Field(default_factory=list)
