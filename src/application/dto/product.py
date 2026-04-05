"""Product DTOs."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.product import Product


@dataclass
class ProductDTO(BaseDTO):
    """Product data transfer object."""

    id: UUID
    store_id: UUID
    name: str
    slug: str
    sku: str | None
    description: str | None
    short_description: str | None
    product_type: str
    status: str
    price: Decimal
    price_currency: str
    compare_at_price: Decimal | None
    cost_price: Decimal | None
    quantity: int
    is_in_stock: bool
    is_low_stock: bool
    is_on_sale: bool
    images: list[str]
    category_id: UUID | None
    tags: list[str]
    attributes: dict
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: Product) -> "ProductDTO":
        """Create DTO from Product entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            name=entity.name,
            slug=entity.slug,
            sku=entity.sku,
            description=entity.description,
            short_description=entity.short_description,
            product_type=entity.product_type.value,
            status=entity.status.value,
            price=entity.price.amount,
            price_currency=entity.price.currency.value,
            compare_at_price=entity.compare_at_price.amount
            if entity.compare_at_price
            else None,
            cost_price=entity.cost_price.amount if entity.cost_price else None,
            quantity=entity.quantity,
            is_in_stock=entity.is_in_stock,
            is_low_stock=entity.is_low_stock,
            is_on_sale=entity.is_on_sale,
            images=entity.images,
            category_id=entity.category_id,
            tags=entity.tags,
            attributes=entity.attributes,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateProductDTO(BaseDTO):
    """Create product data transfer object."""

    name: str
    price: Decimal
    slug: str | None = None
    sku: str | None = None
    description: str | None = None
    short_description: str | None = None
    product_type: str = "physical"
    status: str | None = None
    price_currency: str = "USD"
    compare_at_price: Decimal | None = None
    cost_price: Decimal | None = None
    quantity: int = 0
    low_stock_threshold: int = 5
    images: list[str] = field(default_factory=list)
    category_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    seo_title: str | None = None
    seo_description: str | None = None


@dataclass
class UpdateProductDTO(BaseDTO):
    """Update product data transfer object."""

    name: str | None = None
    slug: str | None = None
    sku: str | None = None
    description: str | None = None
    short_description: str | None = None
    price: Decimal | None = None
    compare_at_price: Decimal | None = None
    cost_price: Decimal | None = None
    quantity: int | None = None
    low_stock_threshold: int | None = None
    images: list[str] | None = None
    category_id: UUID | None = None
    tags: list[str] | None = None
    attributes: dict | None = None
    status: str | None = None
    seo_title: str | None = None
    seo_description: str | None = None
