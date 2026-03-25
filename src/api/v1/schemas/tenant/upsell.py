"""Upsell rule Pydantic schemas for store management."""

from pydantic import BaseModel, ConfigDict, Field


class CreateUpsellRuleRequest(BaseModel):
    """Create upsell rule request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Buy X get Y 20% off",
                "trigger_type": "product",
                "trigger_product_ids": ["550e8400-e29b-41d4-a716-446655440000"],
                "offer_product_id": "660e8400-e29b-41d4-a716-446655440000",
                "discount_type": "percentage",
                "discount_value": 20,
                "priority": 10,
                "headline_en": "Special offer for you! 🎁",
                "headline_ar": "عرض خاص لك! 🎁",
            }
        }
    )

    name: str = Field(
        ..., min_length=1, max_length=200, description="Rule display name"
    )
    is_active: bool = Field(default=True, description="Whether the rule is active")

    # Trigger
    trigger_type: str = Field(
        default="any",
        description="Trigger type: product, category, cart_value, or any",
    )
    trigger_product_ids: list[str] | None = Field(
        None, description="Product UUIDs that trigger this upsell"
    )
    trigger_category_ids: list[str] | None = Field(
        None, description="Category UUIDs that trigger this upsell"
    )
    trigger_min_cart_value: int = Field(
        default=0, ge=0, description="Minimum cart value in cents"
    )

    # Offer
    offer_product_id: str = Field(..., description="UUID of the product to upsell")
    discount_type: str = Field(
        default="percentage",
        description="Discount type: percentage, fixed, or none",
    )
    discount_value: int = Field(
        default=0,
        ge=0,
        description="Discount value (percentage 0-100 or fixed in cents)",
    )

    # Limits
    priority: int = Field(default=0, description="Priority (higher = shown first)")
    max_uses: int | None = Field(
        None, ge=1, description="Maximum uses (null = unlimited)"
    )

    # Display
    headline_ar: str = Field(
        default="عرض خاص لك! 🎁", max_length=200, description="Arabic headline"
    )
    headline_en: str = Field(
        default="Special offer for you! 🎁",
        max_length=200,
        description="English headline",
    )
    description_ar: str | None = Field(None, description="Arabic description")
    description_en: str | None = Field(None, description="English description")


class UpdateUpsellRuleRequest(BaseModel):
    """Update upsell rule request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "discount_value": 30,
                "is_active": False,
            }
        }
    )

    name: str | None = Field(
        None, min_length=1, max_length=200, description="Rule display name"
    )
    is_active: bool | None = Field(None, description="Whether the rule is active")

    # Trigger
    trigger_type: str | None = Field(None, description="Trigger type")
    trigger_product_ids: list[str] | None = Field(
        None, description="Product UUIDs that trigger this upsell"
    )
    trigger_category_ids: list[str] | None = Field(
        None, description="Category UUIDs that trigger this upsell"
    )
    trigger_min_cart_value: int | None = Field(
        None, ge=0, description="Minimum cart value in cents"
    )

    # Offer
    offer_product_id: str | None = Field(
        None, description="UUID of the product to upsell"
    )
    discount_type: str | None = Field(None, description="Discount type")
    discount_value: int | None = Field(None, ge=0, description="Discount value")

    # Limits
    priority: int | None = Field(None, description="Priority")
    max_uses: int | None = Field(None, ge=1, description="Maximum uses")

    # Display
    headline_ar: str | None = Field(None, max_length=200, description="Arabic headline")
    headline_en: str | None = Field(
        None, max_length=200, description="English headline"
    )
    description_ar: str | None = Field(None, description="Arabic description")
    description_en: str | None = Field(None, description="English description")


class UpsellRuleResponse(BaseModel):
    """Upsell rule response schema."""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="Rule UUID")
    store_id: str = Field(description="Owning store UUID")
    name: str = Field(description="Rule display name")
    is_active: bool = Field(description="Whether the rule is active")

    # Trigger
    trigger_type: str = Field(description="Trigger type")
    trigger_product_ids: list[str] = Field(description="Trigger product UUIDs")
    trigger_category_ids: list[str] = Field(description="Trigger category UUIDs")
    trigger_min_cart_value: int = Field(description="Minimum cart value in cents")

    # Offer
    offer_product_id: str = Field(description="Offer product UUID")
    discount_type: str = Field(description="Discount type")
    discount_value: int = Field(description="Discount value")

    # Limits
    priority: int = Field(description="Priority")
    max_uses: int | None = Field(description="Maximum uses")
    uses_count: int = Field(description="Current usage count")

    # Display
    headline_ar: str = Field(description="Arabic headline")
    headline_en: str = Field(description="English headline")
    description_ar: str | None = Field(description="Arabic description")
    description_en: str | None = Field(description="English description")

    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class UpsellOfferResponse(BaseModel):
    """Public upsell offer response for storefront."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: str = Field(description="Upsell rule UUID")
    product: dict = Field(
        description="Product info: id, name, slug, price, compare_at_price, images, is_in_stock"
    )
    discount_type: str = Field(description="Discount type")
    discount_value: int = Field(description="Discount value")
    discounted_price: int = Field(description="Price after discount in cents")
    original_price: int = Field(description="Original price in cents")
    headline: str = Field(description="Headline text (locale-appropriate)")
    description: str | None = Field(description="Description text")
