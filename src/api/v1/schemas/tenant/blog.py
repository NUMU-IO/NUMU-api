"""Blog + Article request/response schemas."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CreateBlogRequest(BaseModel):
    """Create a new blog (article collection) for the store."""

    handle: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Unique blog handle within the store (URL: /blogs/<handle>). Immutable after creation.",
    )
    title: dict[str, str] = Field(
        default_factory=dict, description="Bilingual title {en, ar}"
    )
    description: dict[str, str] = Field(
        default_factory=dict, description="Bilingual description {en, ar}"
    )
    is_published: bool = Field(True)


class UpdateBlogRequest(BaseModel):
    """Partial update of a blog. The handle is immutable (article URLs embed it)."""

    title: dict[str, str] | None = Field(None)
    description: dict[str, str] | None = Field(None)
    is_published: bool | None = Field(None)


class BlogResponse(BaseModel):
    """Blog response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Blog UUID")
    store_id: str = Field(description="Owning store UUID")
    handle: str = Field(description="Unique handle within the store")
    title: dict[str, str] = Field(description="Bilingual title {en, ar}")
    description: dict[str, str] = Field(description="Bilingual description {en, ar}")
    is_published: bool = Field(description="Whether the blog is visible")
    article_count: int = Field(0, description="Number of articles (all statuses)")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class CreateArticleRequest(BaseModel):
    """Create an article inside a blog (starts as draft unless told otherwise)."""

    handle: str | None = Field(
        default=None,
        max_length=255,
        description="URL handle; derived from the English/Arabic title when omitted.",
    )
    title: dict[str, str] = Field(
        default_factory=dict, description="Bilingual title {en, ar}"
    )
    excerpt: dict[str, str] = Field(
        default_factory=dict, description="Bilingual summary {en, ar}"
    )
    body: dict[str, str] = Field(
        default_factory=dict, description="Bilingual rich-text HTML body {en, ar}"
    )
    image_url: str | None = Field(None, max_length=1024)
    author: str | None = Field(None, max_length=255)
    tags: list[str] = Field(default_factory=list)
    seo: dict[str, Any] = Field(
        default_factory=dict,
        description="SEO overrides: {title:{en,ar}, description:{en,ar}}",
    )
    status: str = Field(
        "draft",
        pattern=r"^(draft|scheduled|published)$",
        description="Initial state; `scheduled` requires scheduled_at.",
    )
    scheduled_at: str | None = Field(
        None, description="ISO 8601 UTC time to auto-publish (status=scheduled)."
    )


class UpdateArticleRequest(BaseModel):
    """Partial update of an article, including lifecycle transitions.

    Renaming the handle is allowed — the old handle is remembered and the
    storefront redirects it to the new URL.
    """

    handle: str | None = Field(None, min_length=1, max_length=255)
    title: dict[str, str] | None = Field(None)
    excerpt: dict[str, str] | None = Field(None)
    body: dict[str, str] | None = Field(None)
    image_url: str | None = Field(None, max_length=1024)
    author: str | None = Field(None, max_length=255)
    tags: list[str] | None = Field(None)
    seo: dict[str, Any] | None = Field(None)
    status: str | None = Field(
        None,
        pattern=r"^(draft|scheduled|published|archived)$",
        description="Lifecycle transition; `scheduled` requires scheduled_at.",
    )
    scheduled_at: str | None = Field(None)


class ArticleResponse(BaseModel):
    """Article response schema (merchant-facing — includes drafts)."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Article UUID")
    store_id: str = Field(description="Owning store UUID")
    blog_id: str = Field(description="Owning blog UUID")
    blog_handle: str = Field(description="Owning blog handle")
    handle: str = Field(description="URL handle within the blog")
    title: dict[str, str] = Field(description="Bilingual title {en, ar}")
    excerpt: dict[str, str] = Field(description="Bilingual summary {en, ar}")
    body: dict[str, str] = Field(description="Bilingual rich-text HTML body {en, ar}")
    image_url: str | None = Field(None)
    author: str | None = Field(None)
    tags: list[str] = Field(default_factory=list)
    seo: dict[str, Any] = Field(default_factory=dict)
    status: str = Field(description="draft | scheduled | published | archived")
    published_at: str | None = Field(None, description="ISO 8601 publish time")
    scheduled_at: str | None = Field(None, description="ISO 8601 scheduled time")
    previous_handles: list[str] = Field(default_factory=list)
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")
