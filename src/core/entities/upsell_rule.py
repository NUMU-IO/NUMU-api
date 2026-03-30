"""Upsell rule entity for post-purchase offers."""

from enum import StrEnum
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity


class UpsellTriggerType(StrEnum):
    """What triggers the upsell."""

    PRODUCT = "product"
    CATEGORY = "category"
    CART_VALUE = "cart_value"
    ANY = "any"


class UpsellDiscountType(StrEnum):
    """Discount type for the upsell offer."""

    PERCENTAGE = "percentage"
    FIXED = "fixed"
    NONE = "none"


class UpsellRule(BaseEntity):
    """A rule that defines when and what to upsell after purchase."""

    store_id: UUID
    name: str
    is_active: bool = True

    # Trigger
    trigger_type: UpsellTriggerType = UpsellTriggerType.ANY
    trigger_product_ids: list[UUID] = Field(default_factory=list)
    trigger_category_ids: list[UUID] = Field(default_factory=list)
    trigger_min_cart_value: int = 0  # cents

    # Offer
    offer_product_id: UUID  # The product to upsell
    discount_type: UpsellDiscountType = UpsellDiscountType.PERCENTAGE
    discount_value: int = 0  # percentage (0-100) or fixed amount in cents

    # Limits
    priority: int = 0  # Higher = shown first
    max_uses: int | None = None  # None = unlimited
    uses_count: int = 0

    # Display
    headline_ar: str = "عرض خاص لك! 🎁"
    headline_en: str = "Special offer for you! 🎁"
    description_ar: str | None = None
    description_en: str | None = None
