"""Theme update notification domain entity (Phase 5.1).

One row per affected install when a theme publishes a new version. Tells the
merchant a newer version exists, whether adopting it is **manual** (needs
review — a breaking schema change) or **automatic** (safe), and exactly what
changed. Applying it is always merchant-confirmed and snapshot-first — a
notification never mutates a store on its own.
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class ThemeUpdateNotification(BaseEntity):
    """A pending/handled theme-version update for one store install."""

    store_id: UUID
    tenant_id: UUID | None = None
    # The marketplace theme this update is for (marketplace_themes.id).
    theme_id: UUID
    # Version the store currently has installed (None on first detection).
    from_version_id: UUID | None = None
    # The new published version this notification is about.
    to_version_id: UUID
    from_version: str = ""
    to_version: str = ""
    # Shopify-style verdict from theme_update_classifier.classify_theme_update.
    classification: Literal["manual", "automatic"] = "automatic"
    # The classifier's change list: [{kind, target, breaking, detail}, ...].
    changes: list[dict[str, Any]] = Field(default_factory=list)
    release_notes: str = ""
    status: Literal["pending", "applied", "skipped"] = "pending"
