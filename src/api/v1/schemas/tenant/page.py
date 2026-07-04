"""Page (merchant content page) request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreatePageRequest(BaseModel):
    """Create a new content page for the store."""

    handle: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique page handle within the store (URL: /pages/<handle>)",
    )
    title: dict[str, str] = Field(
        default_factory=dict, description="Bilingual title {en, ar}"
    )
    body: dict[str, str] = Field(
        default_factory=dict, description="Bilingual rich-text body {en, ar}"
    )
    seo: dict[str, Any] = Field(
        default_factory=dict,
        description="SEO overrides: {title:{en,ar}, description:{en,ar}}",
    )
    is_published: bool = Field(True)
    template: str = Field("page", max_length=64)
    template_suffix: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description="Alternate template variant suffix (Shopify-style); null = base template.",
    )


class UpdatePageRequest(BaseModel):
    """Partial update of a page (PUT by handle — upserts if absent)."""

    title: dict[str, str] | None = Field(None)
    body: dict[str, str] | None = Field(None)
    seo: dict[str, Any] | None = Field(None)
    is_published: bool | None = Field(None)
    template: str | None = Field(None, max_length=64)
    template_suffix: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[a-z0-9][a-z0-9-]{0,31}$",
        description="Alternate template variant suffix (Shopify-style); null = base template.",
    )


class PageResponse(BaseModel):
    """Page response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Page UUID")
    store_id: str = Field(description="Owning store UUID")
    handle: str = Field(description="Unique handle within the store")
    title: dict[str, str] = Field(description="Bilingual title {en, ar}")
    body: dict[str, str] = Field(description="Bilingual rich-text body {en, ar}")
    seo: dict[str, Any] = Field(description="SEO overrides")
    is_published: bool = Field(description="Whether the page is visible")
    template: str = Field(description="Theme template key")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
