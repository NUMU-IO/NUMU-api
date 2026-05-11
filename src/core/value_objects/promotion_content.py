"""PromotionContent — discriminated union of surface-specific config.

The `content` JSONB column on `promotions` stores per-surface
non-translatable settings (colors, layout, position, image URL, …).
Translatable copy lives in `promotion_translations`. Pydantic v2's
`Discriminator` keeps the union strict.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic import Discriminator as PydanticDiscriminator


class _BaseContent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DiscountCodeContent(_BaseContent):
    """Code-based promo: code lives on the linked Coupon. No extra config."""

    surface: Literal["discount_code"] = "discount_code"


class AutomaticContent(_BaseContent):
    """Automatic discount: label is translatable; no extra config here."""

    surface: Literal["automatic"] = "automatic"


class AnnouncementBarContent(_BaseContent):
    """Top-of-page banner config."""

    surface: Literal["announcement_bar"] = "announcement_bar"
    background: str = "#000000"
    text_color: str = "#FFFFFF"
    icon: str | None = None
    dismissible: bool = True
    link_url: str | None = None


class PopupContent(_BaseContent):
    """Triggered modal config."""

    surface: Literal["popup"] = "popup"
    layout: Literal["centered", "side"] = "centered"
    image_url: str | None = None
    form_fields: list[Literal["email", "phone", "name"]] = Field(default_factory=list)
    discount_code_to_reveal: str | None = None
    show_after_dismiss_days: int = Field(default=30, ge=0)


class FloatingWidgetContent(_BaseContent):
    """Sticky in-corner widget config."""

    surface: Literal["floating_widget"] = "floating_widget"
    position: Literal["bottom-right", "bottom-left", "top-right", "top-left"] = (
        "bottom-right"
    )
    icon: str = "tag"
    expanded_default: bool = False
    color_bg: str = "#000000"


class CookieBannerContent(_BaseContent):
    """Compliance / cookie consent banner config."""

    surface: Literal["cookie_banner"] = "cookie_banner"
    position: Literal["bottom", "modal"] = "bottom"
    accept_required: bool = False
    policy_url: str | None = None
    preference_categories: list[str] = Field(default_factory=lambda: ["essential"])


# A discriminated union — Pydantic picks the right variant via the
# `surface` literal, giving us strict per-surface validation.
PromotionContent = Annotated[
    DiscountCodeContent
    | AutomaticContent
    | AnnouncementBarContent
    | PopupContent
    | FloatingWidgetContent
    | CookieBannerContent,
    PydanticDiscriminator("surface"),
]


def empty_content_for(surface: str) -> _BaseContent:
    """Return a default-filled content object for a surface string.

    Convenience for callers that need a no-config baseline (e.g., when
    upgrading a legacy row that has `content = {}`).
    """
    match surface:
        case "discount_code":
            return DiscountCodeContent()
        case "automatic":
            return AutomaticContent()
        case "announcement_bar":
            return AnnouncementBarContent()
        case "popup":
            return PopupContent()
        case "floating_widget":
            return FloatingWidgetContent()
        case "cookie_banner":
            return CookieBannerContent()
        case _:
            raise ValueError(f"unknown promotion surface: {surface!r}")
