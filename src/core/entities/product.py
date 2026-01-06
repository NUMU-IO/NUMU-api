"""Product entity representing a product in a store."""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from src.core.entities.base import BaseEntity
from src.core.value_objects.money import Currency, Money


class ProductStatus(str, Enum):
    """Product status enumeration."""

    DRAFT = "draft"
    ACTIVE = "active"
    ARCHIVED = "archived"
    OUT_OF_STOCK = "out_of_stock"


class ProductType(str, Enum):
    """Product type enumeration."""

    PHYSICAL = "physical"
    DIGITAL = "digital"
    SERVICE = "service"


class Product(BaseEntity):
    """Product entity representing a product in a store."""

    def __init__(
        self,
        store_id: UUID,
        name: str,
        slug: str,
        price: Money,
        sku: str | None = None,
        description: str | None = None,
        short_description: str | None = None,
        product_type: ProductType = ProductType.PHYSICAL,
        status: ProductStatus = ProductStatus.DRAFT,
        quantity: int = 0,
        low_stock_threshold: int = 5,
        weight: Decimal | None = None,
        dimensions: dict | None = None,
        images: list[str] | None = None,
        category_id: UUID | None = None,
        tags: list[str] | None = None,
        attributes: dict | None = None,
        metadata: dict | None = None,
        compare_at_price: Money | None = None,
        cost_price: Money | None = None,
        id: UUID | None = None,
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
    ) -> None:
        super().__init__(id=id, created_at=created_at, updated_at=updated_at)
        self.store_id = store_id
        self.name = name
        self.slug = slug
        self.sku = sku
        self.description = description
        self.short_description = short_description
        self.product_type = product_type
        self.status = status
        self.price = price
        self.compare_at_price = compare_at_price
        self.cost_price = cost_price
        self.quantity = quantity
        self.low_stock_threshold = low_stock_threshold
        self.weight = weight
        self.dimensions = dimensions or {}
        self.images = images or []
        self.category_id = category_id
        self.tags = tags or []
        self.attributes = attributes or {}
        self.metadata = metadata or {}

    @property
    def is_in_stock(self) -> bool:
        """Check if product is in stock."""
        return self.quantity > 0

    @property
    def is_low_stock(self) -> bool:
        """Check if product is low on stock."""
        return self.quantity <= self.low_stock_threshold

    @property
    def is_on_sale(self) -> bool:
        """Check if product is on sale."""
        if self.compare_at_price is None:
            return False
        return self.price.amount < self.compare_at_price.amount

    def update_quantity(self, delta: int) -> None:
        """Update product quantity by delta (can be negative)."""
        new_quantity = self.quantity + delta
        if new_quantity < 0:
            raise ValueError("Quantity cannot be negative")
        self.quantity = new_quantity
        if self.quantity == 0:
            self.status = ProductStatus.OUT_OF_STOCK
        self.updated_at = datetime.utcnow()

    def publish(self) -> None:
        """Publish the product."""
        self.status = ProductStatus.ACTIVE
        self.updated_at = datetime.utcnow()

    def archive(self) -> None:
        """Archive the product."""
        self.status = ProductStatus.ARCHIVED
        self.updated_at = datetime.utcnow()
