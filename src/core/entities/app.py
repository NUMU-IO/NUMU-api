"""App platform entities — Phase 6.

Apps are third-party extensions that ship code (blocks, embeds) and
config that themes can render via `useApp(slug)` and the
`@app/<slug>/<block>` block resolver. Two records per relationship:

* `App`           — the published app itself (developer-owned, global).
* `AppInstallation` — a per-store activation with merchant-supplied
                      settings. Themes only see installed apps.

Storefront resolution path:
    GET /storefront/store/{store_id}/apps/{slug}
        → look up App by slug
        → look up AppInstallation by (store_id, app.id, is_enabled)
        → return { manifest, blocks[], data, settings }
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class AppStatus(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"
    SUSPENDED = "suspended"


class App(BaseEntity):
    """A published app available for stores to install."""

    slug: str
    name: str
    description: str | None = None
    developer_id: UUID | None = None
    status: AppStatus = AppStatus.DRAFT
    version: str = "0.1.0"
    icon_url: str | None = None
    # The manifest is the contract the theme reads. Today the only
    # required keys are `blocks` (list of {type, name, schema}) and
    # `endpoints` (optional public data endpoint URL).
    manifest: dict[str, Any] = Field(default_factory=dict)


class AppInstallation(BaseEntity):
    """A per-store activation of an App."""

    tenant_id: UUID
    store_id: UUID
    app_id: UUID
    is_enabled: bool = True
    # Merchant-supplied configuration (form-driven by the app's manifest).
    # Examples: API tokens, enabled blocks, display preferences.
    settings: dict[str, Any] = Field(default_factory=dict)
