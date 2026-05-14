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

    # Popup-only labels for the inline lead-capture form. The merchant
    # dashboard already exposes these fields; without them on the DTO
    # the form's payload tripped `extra="forbid"` and 422'd promotion
    # creation. The storefront PopupModal will start reading these
    # once the email-capture flow is wired up — for now they're stored
    # and round-tripped to the merchant editor unchanged.
    email_label: LocalizedString | None = None
    phone_label: LocalizedString | None = None
    consent_label: LocalizedString | None = None
    submit_label: LocalizedString | None = None
    success_headline: LocalizedString | None = None
    success_body: LocalizedString | None = None
