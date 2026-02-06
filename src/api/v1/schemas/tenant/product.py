"""Product Pydantic schemas."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from src.core.value_objects.money import Currency


class CreateProductRequest(BaseModel):
    """Create product request schema."""

    # Note: store_id is passed as a path parameter, not in the body
    name: str = Field(..., min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    sku: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=10000)
    short_description: str | None = Field(None, max_length=500)
    product_type: str = Field(default="physical")
    price: Decimal = Field(..., ge=0)
    price_currency: str = Field(default="EGP", max_length=3)
    compare_at_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    quantity: int = Field(default=0, ge=0)
    low_stock_threshold: int = Field(default=5, ge=0)
    images: list[str] = Field(default_factory=list, max_length=50)
    category_id: UUID | None = None
    tags: list[str] = Field(default_factory=list, max_length=50)
    attributes: dict = Field(default_factory=dict)

    @field_validator("price_currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        """Validate that currency is a supported currency code."""
        try:
            Currency(v)
        except ValueError:
            supported = [c.value for c in Currency]
            raise ValueError(
                f"Unsupported currency '{v}'. Supported currencies: {supported}"
            )
        return v


class UpdateProductRequest(BaseModel):
    """Update product request schema."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, max_length=255)
    sku: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=10000)
    short_description: str | None = Field(None, max_length=500)
    price: Decimal | None = Field(None, ge=0)
    compare_at_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    quantity: int | None = Field(None, ge=0)
    low_stock_threshold: int | None = Field(None, ge=0)
    images: list[str] | None = Field(None, max_length=50)
    category_id: UUID | None = None
    tags: list[str] | None = Field(None, max_length=50)
    attributes: dict | None = None
    status: str | None = None


class ProductResponse(BaseModel):
    """Product response schema."""

    id: str
    store_id: str
    name: str
    slug: str
    sku: str | None
    description: str | None
    short_description: str | None
    product_type: str
    status: str
    price: str
    price_currency: str
    compare_at_price: str | None
    cost_price: str | None
    quantity: int
    is_in_stock: bool
    is_low_stock: bool
    is_on_sale: bool
    images: list[str]
    category_id: str | None
    tags: list[str]
    attributes: dict
    created_at: str
    updated_at: str

    class Config:
        from_attributes = True


class UploadedImageResponse(BaseModel):
    """Uploaded image response schema."""

    url: str
    key: str
    size: int
    content_type: str
    product_id: str
    variant_urls: dict[str, str] = Field(default_factory=dict)


class DeleteImageRequest(BaseModel):
    """Delete image request schema."""

    image_url: str
