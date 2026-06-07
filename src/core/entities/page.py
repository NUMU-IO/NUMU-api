"""Page (merchant content page) domain entity."""

from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class Page(BaseEntity):
    """A merchant-authored content page (About, Contact, Shipping, …).

    ``handle`` is unique per store and forms the storefront URL
    (``/pages/<handle>``). ``title`` and ``body`` are bilingual
    (``{en, ar}``) so the storefront renders the visitor's language; the
    body is sanitized rich-text HTML.

    ``content_v3`` is reserved for full Shopify-style page customization —
    a page-scoped section/block fragment the theme editor can populate so
    a page can hold real sections, not just a body. It stays an empty
    object until the per-page template editor lands; the storefront falls
    back to rendering ``body`` inside the theme's ``page`` template.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    handle: str
    title: dict[str, str] = Field(default_factory=dict)
    body: dict[str, str] = Field(default_factory=dict)
    # SEO overrides: {"title": {en, ar}, "description": {en, ar}}.
    seo: dict[str, Any] = Field(default_factory=dict)
    is_published: bool = True
    # Theme template key this page renders under (default "page"). Lets a
    # merchant route a page through an alternate template later.
    template: str = "page"
    # Reserved: page-scoped V3 section customization (full Shopify parity).
    content_v3: dict[str, Any] = Field(default_factory=dict)
