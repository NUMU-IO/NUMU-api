"""Blog + Article domain entities (merchant content marketing).

Mirrors the Pages domain conventions (bilingual JSONB text, handle-based
storefront URLs) and adds the article lifecycle:

    draft → scheduled → published → archived
              └────────────↑ (publish now skips scheduling)

``previous_handles`` keeps every handle an article ever had so renames
never break inbound links — the storefront resolves an old handle to the
current article and redirects to the canonical URL.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ArticleStatus(StrEnum):
    """Article lifecycle state."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class Blog(BaseEntity):
    """A named article collection (a store can have several: News, Guides…).

    ``handle`` is unique per store and forms the storefront URL
    (``/blogs/<handle>``). It is immutable after creation in v1 — article
    URLs embed it, so renaming a blog would break every article link.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    handle: str
    title: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    is_published: bool = True


class Article(BaseEntity):
    """A merchant-authored article inside a blog.

    ``title``/``excerpt``/``body`` are bilingual (``{en, ar}``); the body is
    rich-text HTML sanitized at render time (host built-ins run it through
    ``sanitizeHtml``; BYOT themes use the SDK's ``<RichText>``).
    """

    store_id: UUID
    tenant_id: UUID | None = None
    blog_id: UUID
    handle: str
    title: dict[str, str] = Field(default_factory=dict)
    excerpt: dict[str, str] = Field(default_factory=dict)
    body: dict[str, str] = Field(default_factory=dict)
    image_url: str | None = None
    # Display byline (free text in v1; staff-user linkage is a later pass).
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    # SEO overrides: {"title": {en, ar}, "description": {en, ar}}.
    seo: dict[str, Any] = Field(default_factory=dict)
    status: ArticleStatus = ArticleStatus.DRAFT
    published_at: datetime | None = None
    scheduled_at: datetime | None = None
    previous_handles: list[str] = Field(default_factory=list)

    @property
    def is_publicly_visible(self) -> bool:
        return self.status == ArticleStatus.PUBLISHED
