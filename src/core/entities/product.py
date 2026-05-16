"""Product entity representing a product in a store."""

from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Money


class ProductStatus(StrEnum):
    """Product status enumeration."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    OUT_OF_STOCK = "out_of_stock"


class ProductType(StrEnum):
    """Product type enumeration."""

    PHYSICAL = "physical"
    DIGITAL = "digital"
    SERVICE = "service"


class Product(BaseEntity):
    """Product entity representing a product in a store.

    Products can be physical, digital, or service-based. They have
    pricing, inventory tracking, and categorization support.
    """

    store_id: UUID
    tenant_id: UUID | None = None
    name: str
    slug: str
    price: Money
    sku: str | None = None
    description: str | None = None
    short_description: str | None = None
    product_type: ProductType = ProductType.PHYSICAL
    status: ProductStatus = ProductStatus.DRAFT
    quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    weight: Decimal | None = None
    dimensions: dict[str, Any] = Field(default_factory=dict)
    images: list[str] = Field(default_factory=list)
    category_id: UUID | None = None
    tags: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)
    compare_at_price: Money | None = None
    cost_price: Money | None = None
    seo_title: str | None = None
    seo_description: str | None = None
    # Meta Commerce Catalog product ID — pinned by merchant in dashboard
    # so storefront Pixel/CAPI events can reference the Catalog row Meta
    # has on file (enables dynamic ad matching). Null = use product.id.
    meta_catalog_id: str | None = None
    # Phase 8.1 — option axes (size / color / material / ...). Each
    # entry is `{"name": "Size", "position": 0, "values": ["S","M","L"]}`.
    # Variants reference these by name (`variant.option_values["Size"] = "M"`).
    # Stored as JSONB to keep options tightly coupled to the product
    # without a join; capped at 3 axes by the validation layer
    # (Shopify-parity).
    options: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("compare_at_price", "cost_price", mode="before")
    @classmethod
    def validate_money_fields(cls, v: Any) -> Any:
        """Allow None or Money objects for optional money fields."""
        if v is None:
            return None
        if isinstance(v, Money):
            return v
        if isinstance(v, dict):
            return Money.model_validate(v)
        return v

    @property
    def is_in_stock(self) -> bool:
        # Merchants can opt into oversell via the
        # `continue_selling_when_out_of_stock` flag — when set, the product
        # is purchasable regardless of `quantity`. `is_low_stock` /
        # `is_out_of_stock` deliberately keep reflecting actual quantity
        # so merchant-side analytics still flag inventory that needs
        # restocking.
        if (self.attributes or {}).get("continue_selling_when_out_of_stock"):
            return True
        return self.quantity > 0

    @property
    def is_low_stock(self) -> bool:
        """Check if product is low on stock."""
        return 0 < self.quantity <= self.low_stock_threshold

    @property
    def is_out_of_stock(self) -> bool:
        """Check if product is out of stock."""
        return self.quantity == 0

    @property
    def is_on_sale(self) -> bool:
        """Check if product is on sale (compare_at_price > price)."""
        if self.compare_at_price is None:
            return False
        return self.price < self.compare_at_price

    @property
    def discount_percentage(self) -> float:
        """Calculate discount percentage if on sale."""
        if not self.is_on_sale or self.compare_at_price is None:
            return 0.0
        discount = (
            (self.compare_at_price.amount - self.price.amount)
            / self.compare_at_price.amount
            * 100
        )
        return float(round(discount, 1))

    @property
    def profit_margin(self) -> float | None:
        """Calculate profit margin if cost price is set."""
        if self.cost_price is None:
            return None
        if self.cost_price.amount == 0:
            return 100.0
        margin = (self.price.amount - self.cost_price.amount) / self.price.amount * 100
        return float(round(margin, 2))

    @property
    def is_published(self) -> bool:
        """Check if product is published (active)."""
        return self.status == ProductStatus.ACTIVE

    @property
    def is_draft(self) -> bool:
        """Check if product is a draft."""
        return self.status == ProductStatus.DRAFT

    @property
    def is_archived(self) -> bool:
        """Check if product is archived."""
        return self.status == ProductStatus.ARCHIVED

    def update_quantity(self, delta: int) -> None:
        """Update product quantity by delta (can be negative).

        Args:
            delta: Amount to add (positive) or subtract (negative)

        Raises:
            ValueError: If resulting quantity would be negative
        """
        new_quantity = self.quantity + delta
        if new_quantity < 0:
            raise ValueError(
                f"Cannot reduce quantity by {abs(delta)}. "
                f"Current quantity is {self.quantity}."
            )
        self.quantity = new_quantity
        if self.quantity == 0:
            self.status = ProductStatus.OUT_OF_STOCK
        elif self.status == ProductStatus.OUT_OF_STOCK and self.quantity > 0:
            self.status = ProductStatus.ACTIVE
        self.touch()

    def set_quantity(self, quantity: int) -> None:
        """Set product quantity to a specific value.

        Args:
            quantity: New quantity (must be >= 0)

        Raises:
            ValueError: If quantity is negative
        """
        if quantity < 0:
            raise ValueError("Quantity cannot be negative")
        self.quantity = quantity
        if self.quantity == 0:
            self.status = ProductStatus.OUT_OF_STOCK
        elif self.status == ProductStatus.OUT_OF_STOCK and self.quantity > 0:
            self.status = ProductStatus.ACTIVE
        self.touch()

    def publish(self) -> None:
        """Publish the product (make it active)."""
        self.status = ProductStatus.ACTIVE
        self.touch()

    def unpublish(self) -> None:
        """Unpublish the product (set to draft)."""
        self.status = ProductStatus.DRAFT
        self.touch()

    def archive(self) -> None:
        """Archive the product."""
        self.status = ProductStatus.ARCHIVED
        self.touch()

    def restore(self) -> None:
        """Restore an archived product to draft status."""
        if self.status == ProductStatus.ARCHIVED:
            self.status = ProductStatus.DRAFT
            self.touch()

    def add_image(self, image_url: str) -> None:
        """Add an image to the product.

        Args:
            image_url: URL of the image to add
        """
        if image_url not in self.images:
            self.images.append(image_url)
            self.touch()

    def remove_image(self, image_url: str) -> None:
        """Remove an image from the product.

        Args:
            image_url: URL of the image to remove
        """
        if image_url in self.images:
            self.images.remove(image_url)
            self.touch()

    def add_tag(self, tag: str) -> None:
        """Add a tag to the product.

        Args:
            tag: Tag to add
        """
        normalized_tag = tag.lower().strip()
        if normalized_tag and normalized_tag not in self.tags:
            self.tags.append(normalized_tag)
            self.touch()

    def remove_tag(self, tag: str) -> None:
        """Remove a tag from the product.

        Args:
            tag: Tag to remove
        """
        normalized_tag = tag.lower().strip()
        if normalized_tag in self.tags:
            self.tags.remove(normalized_tag)
            self.touch()

    def set_attribute(self, key: str, value: Any) -> None:
        """Set a product attribute.

        Args:
            key: Attribute name
            value: Attribute value
        """
        self.attributes[key] = value
        self.touch()

    def remove_attribute(self, key: str) -> None:
        """Remove a product attribute.

        Args:
            key: Attribute name to remove
        """
        self.attributes.pop(key, None)
        self.touch()
