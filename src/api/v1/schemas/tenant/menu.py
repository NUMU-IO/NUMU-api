"""Menu (store navigation / link list) request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MenuItemSchema(BaseModel):
    """A single navigation item. ``children`` nest up to depth 3."""

    id: str | None = Field(None, description="Stable client id for the item")
    label: dict[str, str] = Field(
        default_factory=dict, description="Bilingual label {en, ar}"
    )
    url: str = Field("", description="Resolved destination URL/path")
    type: str = Field(
        "link",
        description="Semantic: home|collection|product|page|http|catalog|search|link",
    )
    resource_id: str | None = Field(
        None, description="Optional resource id for the type"
    )
    children: list[MenuItemSchema] = Field(
        default_factory=list, description="Nested sub-items"
    )


MenuItemSchema.model_rebuild()


class CreateMenuRequest(BaseModel):
    """Create a new menu for the store."""

    handle: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique menu handle within the store",
    )
    title: dict[str, str] = Field(
        default_factory=dict, description="Bilingual title {en, ar}"
    )
    items: list[MenuItemSchema] = Field(default_factory=list)
    is_active: bool = Field(True)


class UpdateMenuRequest(BaseModel):
    """Partial update of a menu (PUT by handle — upserts if absent)."""

    title: dict[str, str] | None = Field(None)
    items: list[MenuItemSchema] | None = Field(None)
    is_active: bool | None = Field(None)


class MenuResponse(BaseModel):
    """Menu response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Menu UUID")
    store_id: str = Field(description="Owning store UUID")
    handle: str = Field(description="Unique handle within the store")
    title: dict[str, str] = Field(description="Bilingual title {en, ar}")
    items: list[dict[str, Any]] = Field(description="Nested navigation items")
    is_active: bool = Field(description="Whether the menu is active")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
