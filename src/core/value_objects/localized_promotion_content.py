"""Localized copy fields for a promotion (headline / body / CTA).

Wraps individual `LocalizedString`s for the translatable fields a
promotion can carry. The base `Promotion.content` JSONB holds
non-translatable settings; this VO holds the translatable ones.
"""

from pydantic import BaseModel, ConfigDict

from src.core.value_objects.localized_string import LocalizedString


class LocalizedPromotionContent(BaseModel):
    """Translatable copy for a single locale of a promotion."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    headline: LocalizedString | None = None
    body: LocalizedString | None = None
    cta_label: LocalizedString | None = None
    cta_url: str | None = None
    label: LocalizedString | None = None
