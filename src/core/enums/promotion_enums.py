"""Promotion-related enums.

These mirror the Postgres ENUM types created in
`alembic/versions/20260507_create_promotion_enums.py`. The string values
must stay byte-identical to the DB enum labels.
"""

from enum import StrEnum


class PromotionSurface(StrEnum):
    """The presentation type of a promotion."""

    DISCOUNT_CODE = "discount_code"
    AUTOMATIC = "automatic"
    ANNOUNCEMENT_BAR = "announcement_bar"
    POPUP = "popup"
    FLOATING_WIDGET = "floating_widget"
    COOKIE_BANNER = "cookie_banner"


class PromotionStatus(StrEnum):
    """Lifecycle state of a promotion."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    ACTIVE = "active"
    PAUSED = "paused"
    EXPIRED = "expired"
    ARCHIVED = "archived"


class DisplayTrigger(StrEnum):
    """Customer-side event that fires a popup / banner display."""

    ON_LOAD = "on_load"
    ON_DELAY = "on_delay"
    ON_SCROLL_PCT = "on_scroll_pct"
    ON_EXIT_INTENT = "on_exit_intent"
    ON_ADD_TO_CART = "on_add_to_cart"
    ALWAYS = "always"


class DisplayFrequency(StrEnum):
    """How often the same shopper should see a given display."""

    ONCE_PER_SESSION = "once_per_session"
    ONCE_PER_VISITOR = "once_per_visitor"
    EVERY_VISIT = "every_visit"
    UNTIL_DISMISSED = "until_dismissed"
    UNTIL_REDEEMED = "until_redeemed"


class TargetKind(StrEnum):
    """Audience / catalog dimension that a target rule applies to."""

    AUDIENCE = "audience"
    PRODUCT = "product"
    CATEGORY = "category"
    CUSTOMER_TAG = "customer_tag"
    GEO = "geo"


class PromotionEventType(StrEnum):
    """Type of analytics event for a promotion."""

    IMPRESSION = "impression"
    CLICK = "click"
    DISMISS = "dismiss"
    REDEEM = "redeem"
    CONVERT = "convert"
    # Form-capture surfaces (popup, floating widget) record `submit` when
    # the visitor completes the embedded email/phone form. The captured
    # fields are serialized into the event row's `metadata` blob — we
    # don't add a separate submissions table because analytics already
    # lives off `promotion_events` and PII deletion goes through one path.
    SUBMIT = "submit"
