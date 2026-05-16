"""Product Pydantic schemas."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.api.dependencies.sanitization import SanitizedStr
from src.core.value_objects.money import Currency


class SizeChartRow(BaseModel):
    """One row of the size-chart table — size label + one value per column."""

    size: str = Field("", max_length=40)
    values: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("values", mode="before")
    @classmethod
    def _coerce_values(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        # Anything non-string gets str-coerced so bad client payloads don't
        # reach the DB as mixed-type JSON.
        return [str(x)[:40] if x is not None else "" for x in v]


class SizeChartSchema(BaseModel):
    """Validator for `product.attributes.size_chart` (and the store-level
    default stored at `store.settings.size_chart`).

    Accepts three modes:
      - "default" — product falls back to the store default
      - "custom"  — product carries its own chart
      - "off"     — button is hidden even if a store default exists

    Legacy payloads that only had `enabled: bool` are upgraded to a mode
    by the storefront resolver, so both shapes are accepted here.
    """

    model_config = ConfigDict(extra="ignore")

    enabled: bool = False
    mode: Literal["default", "custom", "off"] = "default"
    column_headers: list[str] = Field(default_factory=list, max_length=20)
    rows: list[SizeChartRow] = Field(default_factory=list, max_length=60)
    unit: Literal["cm", "in"] = "cm"
    notes: str = Field("", max_length=2000)
    image_url: str = Field("", max_length=2048)

    @field_validator("column_headers", mode="before")
    @classmethod
    def _coerce_headers(cls, v: Any) -> list[str]:
        if not isinstance(v, list):
            return []
        return [str(x)[:80] if x is not None else "" for x in v]


def _validate_size_chart(attributes: dict) -> dict:
    """If `attributes.size_chart` is present, run it through SizeChartSchema
    so typos or malformed payloads don't land in the DB. Unknown keys are
    silently dropped (extra="ignore"); invalid shapes raise ValueError,
    which Pydantic surfaces as a 422 to the caller."""
    raw = attributes.get("size_chart")
    if raw is None:
        return attributes
    if not isinstance(raw, dict):
        attributes.pop("size_chart", None)
        return attributes
    validated = SizeChartSchema.model_validate(raw).model_dump()
    # Truncate each row's `values` to the header count — catches
    # column-drift from editors that don't clean up on column removal.
    headers_len = len(validated["column_headers"])
    for row in validated["rows"]:
        row["values"] = row["values"][:headers_len]
    attributes["size_chart"] = validated
    return attributes


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
    price: Decimal = Field(..., ge=0, description="Product price in the store currency")
    price_currency: str = Field(
        default="EGP", max_length=3, description="ISO 4217 currency code"
    )
    compare_at_price: Decimal | None = Field(
        None, ge=0, description="Original price before discount (strike-through price)"
    )
    cost_price: Decimal | None = Field(
        None, ge=0, description="Cost of goods for profit calculation"
    )
    quantity: int = Field(default=0, ge=0, description="Available stock quantity")
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
        default_factory=dict,
        description="Arbitrary key-value attributes (size, color, etc.)",
    )
    status: str | None = Field(
        None, description="Product status: active, draft, or archived"
    )
    seo_title: str | None = Field(None, max_length=70, description="SEO page title")
    seo_description: str | None = Field(
        None, max_length=160, description="SEO meta description"
    )
    meta_catalog_id: str | None = Field(
        None,
        max_length=255,
        description=(
            "Meta Commerce Catalog product ID. When set, the storefront "
            "uses this as `content_ids` on Pixel/CAPI events so Meta "
            "dynamic ads match conversions back to a catalog row."
        ),
    )
    # Phase 8.1 — option axes + variant matrix. Both default to empty,
    # in which case the product CRUD creates a single "default variant"
    # automatically (matching the migration's backfill behavior).
    options: list[ProductOptionInput] = Field(
        default_factory=list,
        max_length=3,
        description="Up to 3 option axes (Size, Color, Material, ...)",
    )
    variants: list[VariantInput] = Field(
        default_factory=list,
        description="Per-variant price/SKU/inventory rows",
    )

    @field_validator("attributes", mode="after")
    @classmethod
    def _normalize_attributes(cls, v: dict) -> dict:
        return _validate_size_chart(v)


class ProductOptionInput(BaseModel):
    """One option axis on the create/update payload."""

    name: SanitizedStr = Field(..., min_length=1, max_length=64)
    position: int = Field(default=0, ge=0, le=2)
    values: list[SanitizedStr] = Field(default_factory=list, max_length=50)


class VariantInput(BaseModel):
    """One purchasable variant on the create/update payload.

    On create, `id` is omitted; on update, an existing id preserves the
    row (otherwise a fresh variant is created and any variant whose id
    isn't present in the request gets soft-deleted via FK cascade when
    its product changes).
    """

    id: UUID | None = None
    position: int = Field(default=0, ge=0)
    option_values: dict[str, str] = Field(default_factory=dict)
    price: Decimal = Field(..., ge=0)
    price_currency: str = Field(default="EGP", max_length=3)
    compare_at_price: Decimal | None = Field(None, ge=0)
    cost_price: Decimal | None = Field(None, ge=0)
    sku: str | None = Field(None, max_length=100)
    barcode: str | None = Field(None, max_length=100)
    inventory_quantity: int = Field(default=0, ge=0)
    image_url: str | None = Field(None, max_length=2048)
    weight: float | None = None

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
    slug: str | None = Field(None, max_length=255, description="URL-friendly slug")
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
    quantity: int | None = Field(None, ge=0, description="Available stock quantity")
    low_stock_threshold: int | None = Field(
        None, ge=0, description="Threshold to trigger low-stock alerts"
    )
    images: list[str] | None = Field(
        None, max_length=50, description="Product image URLs"
    )
    category_id: UUID | None = Field(None, description="Category UUID")
    tags: list[str] | None = Field(None, max_length=50, description="Searchable tags")
    attributes: dict | None = Field(None, description="Arbitrary key-value attributes")
    status: str | None = Field(
        None, description="Product status: active, draft, or archived"
    )
    seo_title: str | None = Field(None, max_length=70, description="SEO page title")
    seo_description: str | None = Field(
        None, max_length=160, description="SEO meta description"
    )
    meta_catalog_id: str | None = Field(
        None,
        max_length=255,
        description=(
            "Meta Commerce Catalog product ID. When set, the storefront "
            "uses this as `content_ids` on Pixel/CAPI events so Meta "
            "dynamic ads match conversions back to a catalog row."
        ),
    )
    # Phase 8.1 — options + variants on update. Omitted → no change.
    # Empty list → drop options/variants and recreate the default.
    options: list[ProductOptionInput] | None = Field(
        None, max_length=3, description="Replace option axes (None = no change)"
    )
    variants: list[VariantInput] | None = Field(
        None, description="Replace variant matrix (None = no change)"
    )

    @field_validator("attributes", mode="after")
    @classmethod
    def _normalize_attributes(cls, v: dict | None) -> dict | None:
        if v is None:
            return None
        return _validate_size_chart(v)


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
    meta_catalog_id: str | None = Field(
        default=None,
        description=(
            "Meta Commerce Catalog product ID — used as `content_ids` on "
            "storefront Pixel events when set, so Meta dynamic ads can "
            "match the conversion back to a catalog row. Null falls back "
            "to the product UUID."
        ),
    )
    # Phase 8.1 — option axes (e.g. [{name:"Size",values:["S","M","L"]}]).
    # Empty list when the product has no variants (single SKU). Themes
    # branch on `options.length > 0` to render a variant picker.
    options: list[dict] = Field(
        default_factory=list, description="Option axes for variants"
    )
    # Phase 8.1 — purchasable variants. Always at least one row per
    # product (the migration backfills a default variant for legacy
    # data); for single-axis products this is exactly the product's
    # own price + inventory normalized. Theme code keys off `variants[i].id`
    # for cart line items.
    variants: list[ProductVariantSummary] = Field(
        default_factory=list,
        description="Purchasable variants — at least one per product",
    )
    created_at: str = Field(description="ISO 8601 creation timestamp")
    updated_at: str = Field(description="ISO 8601 last-update timestamp")


class ProductVariantSummary(BaseModel):
    """Per-variant payload nested in ProductResponse.

    Themes read this to render a variant picker + bind the chosen
    variant_id on add-to-cart. Cost price omitted — same reason as
    on the parent ProductResponse, that's merchant-internal data.
    """

    id: str = Field(description="Variant UUID")
    position: int = Field(description="Display order within product")
    option_values: dict[str, str] = Field(
        default_factory=dict, description="axis-name → chosen-value"
    )
    price: str = Field(description="Cents (string for precision)")
    price_currency: str = Field(description="ISO 4217")
    compare_at_price: str | None = None
    sku: str | None = None
    barcode: str | None = None
    inventory_quantity: int = Field(description="Available stock for this variant")
    is_in_stock: bool = Field(description="Whether inventory_quantity > 0")
    image_url: str | None = None
    weight: float | None = None


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
            "example": {"image_url": "https://cdn.numu.com/products/img-001.jpg"}
        }
    )

    image_url: str = Field(description="URL of the image to delete")
