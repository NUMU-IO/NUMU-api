"""Product Pydantic schemas."""

from decimal import Decimal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.dependencies.sanitization import SanitizedStr
from src.core.value_objects.money import Currency


class CreateProductRequest(BaseModel):
    """Create product request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Egyptian Cotton T-Shirt",
                "slug": "egyptian-cotton-tshirt",
                "sku": "ECT-001",
                "description": "Premium Egyptian cotton t-shirt, available in multiple sizes.",
                "short_description": "Soft Egyptian cotton tee",
                "product_type": "physical",
                "price": "250.00",
                "price_currency": "EGP",
                "compare_at_price": "350.00",
                "cost_price": "120.00",
                "quantity": 100,
                "low_stock_threshold": 10,
                "images": ["https://cdn.numu.com/products/tshirt-front.jpg"],
                "category_id": None,
                "tags": ["clothing", "cotton", "summer"],
                "attributes": {"size": "M", "color": "white"},
            }
        }
    )

    # Note: store_id is passed as a path parameter, not in the body
    name: SanitizedStr = Field(
        ..., min_length=1, max_length=255, description="Product display name"
    )
    slug: str | None = Field(
        None, max_length=255, description="URL-friendly slug; auto-generated if omitted"
    )
    sku: str | None = Field(
        None, max_length=100, description="Stock Keeping Unit identifier"
    )
    description: SanitizedStr | None = Field(
        None, max_length=10000, description="Full product description (HTML allowed)"
    )
    short_description: SanitizedStr | None = Field(
        None, max_length=500, description="Brief description for listings"
    )
    product_type: str = Field(
        default="physical", description="Product type: physical or digital"
    )
    price: Decimal = Field(
        ..., ge=0, description="Product price in the store currency"
    )
    price_currency: str = Field(
        default="EGP", max_length=3, description="ISO 4217 currency code"
    )
    compare_at_price: Decimal | None = Field(
        None, ge=0, description="Original price before discount (strike-through price)"
    )
    cost_price: Decimal | None = Field(
        None, ge=0, description="Cost of goods for profit calculation"
    )
    quantity: int = Field(
        default=0, ge=0, description="Available stock quantity"
    )
    low_stock_threshold: int = Field(
        default=5, ge=0, description="Threshold to trigger low-stock alerts"
    )
    images: list[str] = Field(
        default_factory=list, max_length=50, description="Product image URLs (max 50)"
    )
    category_id: UUID | None = Field(
        None, description="Category UUID this product belongs to"
    )
    tags: list[str] = Field(
        default_factory=list, max_length=50, description="Searchable tags"
    )
    attributes: dict = Field(
        default_factory=dict, description="Arbitrary key-value attributes (size, color, etc.)"
    )

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
    """Update product request schema — all fields optional (partial update)."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "price": "275.00",
                "quantity": 80,
                "status": "active",
            }
        }
    )

    name: SanitizedStr | None = Field(
        None, min_length=1, max_length=255, description="Product display name"
    )
    slug: str | None = Field(
        None, max_length=255, description="URL-friendly slug"
    )
    sku: str | None = Field(
        None, max_length=100, description="Stock Keeping Unit identifier"
    )
    description: SanitizedStr | None = Field(
        None, max_length=10000, description="Full product description"
    )
    short_description: SanitizedStr | None = Field(
        None, max_length=500, description="Brief description for listings"
    )
    price: Decimal | None = Field(
        None, ge=0, description="Product price in the store currency"
    )
    compare_at_price: Decimal | None = Field(
        None, ge=0, description="Original price before discount"
    )
    cost_price: Decimal | None = Field(
        None, ge=0, description="Cost of goods for profit calculation"
    )
    quantity: int | None = Field(
        None, ge=0, description="Available stock quantity"
    )
    low_stock_threshold: int | None = Field(
        None, ge=0, description="Threshold to trigger low-stock alerts"
    )
    images: list[str] | None = Field(
        None, max_length=50, description="Product image URLs"
    )
    category_id: UUID | None = Field(
        None, description="Category UUID"
    )
    tags: list[str] | None = Field(
        None, max_length=50, description="Searchable tags"
    )
    attributes: dict | None = Field(
        None, description="Arbitrary key-value attributes"
    )
    status: str | None = Field(
        None, description="Product status: active, draft, or archived"
    )


class ProductResponse(BaseModel):
    """Product response schema."""

    model_config = ConfigDict(
        from_attributes=True,
        json_schema_extra={
            "example": {
                "id": "550e8400-e29b-41d4-a716-446655440000",
                "store_id": "660e8400-e29b-41d4-a716-446655440000",
                "name": "Egyptian Cotton T-Shirt",
                "slug": "egyptian-cotton-tshirt",
                "sku": "ECT-001",
                "description": "Premium Egyptian cotton t-shirt.",
                "short_description": "Soft Egyptian cotton tee",
                "product_type": "physical",
                "status": "active",
                "price": "250.00",
                "price_currency": "EGP",
                "compare_at_price": "350.00",
                "cost_price": "120.00",
                "quantity": 100,
                "is_in_stock": True,
                "is_low_stock": False,
                "is_on_sale": True,
                "images": ["https://cdn.numu.com/products/tshirt-front.jpg"],
                "category_id": None,
                "tags": ["clothing", "cotton"],
                "attributes": {"size": "M", "color": "white"},
                "created_at": "2025-01-15T10:30:00Z",
                "updated_at": "2025-01-15T10:30:00Z",
            }
        },
    )

    id: str = Field(description="Product UUID")
    store_id: str = Field(description="Owning store UUID")
    name: str = Field(description="Product display name")
    slug: str = Field(description="URL-friendly slug")
    sku: str | None = Field(description="Stock Keeping Unit")
    description: str | None = Field(description="Full product description")
    short_description: str | None = Field(description="Brief description")
    product_type: str = Field(description="physical or digital")
    status: str = Field(description="active, draft, or archived")
    price: str = Field(description="Formatted price string")
    price_currency: str = Field(description="ISO 4217 currency code")
    compare_at_price: str | None = Field(description="Strike-through price")
    cost_price: str | None = Field(description="Cost of goods")
    quantity: int = Field(description="Available stock quantity")
    is_in_stock: bool = Field(description="Whether the product is in stock")
    is_low_stock: bool = Field(description="Whether stock is below threshold")
    is_on_sale: bool = Field(description="Whether compare_at_price is set")
    images: list[str] = Field(description="Product image URLs")
    category_id: str | None = Field(description="Category UUID")
    tags: list[str] = Field(description="Searchable tags")
    attributes: dict = Field(description="Key-value attributes")
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class UploadedImageResponse(BaseModel):
    """Uploaded image response schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "url": "https://cdn.numu.com/products/img-001.jpg",
                "key": "products/img-001.jpg",
                "size": 204800,
                "content_type": "image/jpeg",
                "product_id": "550e8400-e29b-41d4-a716-446655440000",
                "variant_urls": {
                    "thumbnail": "https://cdn.numu.com/products/img-001_thumb.jpg"
                },
            }
        }
    )

    url: str = Field(description="Public URL of the uploaded image")
    key: str = Field(description="Storage key / path")
    size: int = Field(description="File size in bytes")
    content_type: str = Field(description="MIME type (e.g. image/jpeg)")
    product_id: str = Field(description="Associated product UUID")
    variant_urls: dict[str, str] = Field(
        default_factory=dict, description="URLs for resized variants (thumbnail, etc.)"
    )


class DeleteImageRequest(BaseModel):
    """Delete image request schema."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "image_url": "https://cdn.numu.com/products/img-001.jpg"
            }
        }
    )

    image_url: str = Field(description="URL of the image to delete")
