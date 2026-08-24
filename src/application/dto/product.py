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
    # Meta Commerce Catalog product ID — surfaced on the storefront so
    # fireMetaEvent can use it as `content_ids` (Meta dynamic ads then
    # match conversions to a Catalog row). Optional; null falls back to
    # the product UUID in the storefront consumer.
    meta_catalog_id: str | None
    brand: str | None
    robots_noindex: bool
    canonical_url: str | None
    sitemap_exclude: bool
    seo_title: str | None
    seo_description: str | None
    # Alternate template variant suffix (Shopify-style); null = base template.
    template_suffix: str | None
    created_at: datetime
    updated_at: datetime
    # Alt text per image URL, resolved from the entity's ``metadata.media_urls``
    # sidecar. A FIELD here, not the entity's method: the DTO deliberately
    # carries no ``metadata``, so a route holding a DTO has no way to derive
    # this itself. Every storefront listing endpoint serialises DTOs, so
    # without this the alt text the merchant typed would never reach a
    # collection or search result — and calling the entity's method on a DTO
    # is a 500 (see `_image_alts` in routes/storefront/public.py).
    image_alts: dict[str, str] = field(default_factory=dict)

    # ── Commerce controls ───────────────────────────────────────────────
    weight: Decimal | None = None
    requires_shipping: bool = True
    tax_exempt: bool = False
    # `price` above stays the LIST price so a storefront can strike it
    # through; `effective_price` is what the customer actually pays. Any
    # consumer quoting a total must read the latter.
    effective_price: Decimal | None = None
    sale_price: Decimal | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None
    sale_is_active: bool = False
    related_product_ids: list[UUID] = field(default_factory=list)

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
            meta_catalog_id=getattr(entity, "meta_catalog_id", None),
            brand=getattr(entity, "brand", None),
            robots_noindex=getattr(entity, "robots_noindex", False),
            canonical_url=getattr(entity, "canonical_url", None),
            sitemap_exclude=getattr(entity, "sitemap_exclude", False),
            seo_title=getattr(entity, "seo_title", None),
            seo_description=getattr(entity, "seo_description", None),
            template_suffix=getattr(entity, "template_suffix", None),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
            image_alts=entity.image_alts() if hasattr(entity, "image_alts") else {},
            weight=getattr(entity, "weight", None),
            requires_shipping=getattr(entity, "requires_shipping", True),
            tax_exempt=getattr(entity, "tax_exempt", False),
            effective_price=entity.effective_price().amount,
            sale_price=entity.sale_price.amount if entity.sale_price else None,
            sale_starts_at=getattr(entity, "sale_starts_at", None),
            sale_ends_at=getattr(entity, "sale_ends_at", None),
            sale_is_active=entity.sale_is_active(),
            related_product_ids=list(getattr(entity, "related_product_ids", []) or []),
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
    # None → inherit the store's default currency at creation time.
    price_currency: str | None = None
    compare_at_price: Decimal | None = None
    cost_price: Decimal | None = None
    quantity: int = 0
    low_stock_threshold: int = 5
    images: list[str] = field(default_factory=list)
    category_id: UUID | None = None
    tags: list[str] = field(default_factory=list)
    attributes: dict = field(default_factory=dict)
    brand: str | None = None
    robots_noindex: bool = False
    canonical_url: str | None = None
    sitemap_exclude: bool = False
    seo_title: str | None = None
    seo_description: str | None = None
    # Alternate template variant suffix (Shopify-style); null = base template.
    template_suffix: str | None = None

    # Shipping weight. The column and entity field existed all along but no
    # DTO carried it, so it was unreachable through the API.
    weight: Decimal | None = None

    # Commerce flags. Defaults match the entity: physical and taxable.
    requires_shipping: bool = True
    tax_exempt: bool = False

    # Scheduled sale. An open bound means no bound on that side.
    sale_price: Decimal | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None

    # Curated similar products; empty = fall back to the automatic list.
    related_product_ids: list[UUID] = field(default_factory=list)


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
    brand: str | None = None
    robots_noindex: bool = False
    canonical_url: str | None = None
    sitemap_exclude: bool = False
    seo_title: str | None = None
    seo_description: str | None = None
    # Alternate template variant suffix (Shopify-style); null = base template.
    # `template_suffix` is nullable AND clearable, so on a PATCH we cannot treat
    # ``None`` as "leave alone" (that would make the override impossible to
    # remove). `template_suffix_provided` carries whether the client actually
    # sent the key (route derives it from the request's ``model_fields_set``):
    # provided + value → set it, provided + None → clear it, not provided →
    # leave the current variant untouched.
    template_suffix: str | None = None
    template_suffix_provided: bool = False

    # Shipping weight — nullable AND clearable, so it needs the same
    # provided-flag treatment as template_suffix above.
    weight: Decimal | None = None
    weight_provided: bool = False

    # Commerce flags. None = leave alone (a bool cannot express that on
    # its own, so these are tri-state rather than carrying a flag each).
    requires_shipping: bool | None = None
    tax_exempt: bool | None = None

    # Scheduled sale. The three fields move together — a merchant either
    # sets a sale or removes one — so ONE flag covers the group: provided
    # with a price → set it, provided without → clear the whole sale, not
    # provided → leave whatever is running untouched.
    sale_price: Decimal | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None
    sale_provided: bool = False

    # None = leave alone, [] = clear back to the automatic list. A list can
    # express both without a companion flag.
    related_product_ids: list[UUID] | None = None
