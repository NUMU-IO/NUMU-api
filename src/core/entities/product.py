"""Product entity representing a product in a store."""

from datetime import UTC, datetime
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
    # Reachable by direct link, but absent from listings, search, feeds and
    # the sitemap. For a product a merchant wants to sell to specific
    # customers without putting it in the catalogue.
    UNLISTED = "unlisted"
    ARCHIVED = "archived"
    OUT_OF_STOCK = "out_of_stock"


# Statuses a customer may buy. UNLISTED is deliberately included: the
# whole point of an unlisted product is that someone holding the link can
# buy it. Keep this OUT of listing/search/feed queries, which must stay
# ACTIVE-only, or unlisted products leak back into the catalogue.
PURCHASABLE_STATUSES: tuple[ProductStatus, ...] = (
    ProductStatus.ACTIVE,
    ProductStatus.UNLISTED,
)


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
    # Every slug this product ever had. A rename otherwise 404s every
    # indexed URL and inbound link that pointed at the old one; the
    # storefront resolves an old slug back to this product and 301s to the
    # canonical URL. Capped on write — see core/utils/slug_history.py.
    previous_slugs: list[str] = Field(default_factory=list)
    price: Money
    sku: str | None = None
    description: str | None = None
    short_description: str | None = None
    product_type: ProductType = ProductType.PHYSICAL
    status: ProductStatus = ProductStatus.DRAFT
    # Negative quantity is a legitimate domain state: merchants who enable
    # continue_selling_when_out_of_stock oversell on purpose and the count
    # shows how deep they are in the hole. The ge=0 guard here made every
    # list/read hydration 500 for the whole store the moment one product
    # went negative. API write schemas still enforce ge=0 on merchant input.
    quantity: int = Field(default=0)
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
    brand: str | None = None
    robots_noindex: bool = False
    canonical_url: str | None = None
    sitemap_exclude: bool = False
    seo_title: str | None = None
    seo_description: str | None = None
    # Alternate template variant key suffix (Shopify-style); null = base template.
    template_suffix: str | None = None
    # Meta Commerce Catalog product ID — pinned by merchant in dashboard
    # so storefront Pixel/CAPI events can reference the Catalog row Meta
    # has on file (enables dynamic ad matching). Null = use product.id.
    meta_catalog_id: str | None = None

    # ── Commerce flags ──────────────────────────────────────────────────
    # A digital product should not ask the buyer for an address or attract
    # a shipping fee. Defaults True: every existing row is physical.
    requires_shipping: bool = True
    # Zero-rated goods. Tax used to be computed on every line regardless.
    tax_exempt: bool = False

    # ── Scheduled sale ──────────────────────────────────────────────────
    # `compare_at_price` can express "was/now" but never *when*, so a
    # merchant had to remember to change prices back by hand. When
    # `sale_price` is set and the window is open, it is what the customer
    # pays and `price` becomes the struck-through original.
    sale_price: Money | None = None
    sale_starts_at: datetime | None = None
    sale_ends_at: datetime | None = None

    # Curated "similar products", overriding the automatic list.
    related_product_ids: list[UUID] = Field(default_factory=list)

    def sale_is_active(self, now: datetime | None = None) -> bool:
        """True when a sale price is configured AND the window is open.

        An open-ended bound means "no bound": a sale with only a start
        runs until the merchant ends it, and one with only an end has
        been running since it was set.
        """
        if self.sale_price is None:
            return False
        moment = now or datetime.now(UTC)
        if self.sale_starts_at and moment < self.sale_starts_at:
            return False
        if self.sale_ends_at and moment > self.sale_ends_at:
            return False
        return True

    def effective_price(self, now: datetime | None = None) -> Money:
        """What the customer actually pays right now.

        Every price the storefront quotes, the cart totals and the
        checkout charges must come through here — reading `.price`
        directly is what would silently ignore an active sale.
        """
        if self.sale_is_active(now):
            assert self.sale_price is not None  # narrowed by sale_is_active
            return self.sale_price
        return self.price

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
    def continue_selling_when_out_of_stock(self) -> bool:
        """Has the merchant opted into overselling this product?

        The flag lives on the PRODUCT (`attributes`), never on a variant, so
        the product is the only object that can answer stock questions about
        its own variants. See :meth:`variant_is_in_stock`.
        """
        return bool((self.attributes or {}).get("continue_selling_when_out_of_stock"))

    @property
    def is_in_stock(self) -> bool:
        # Merchants can opt into oversell via the
        # `continue_selling_when_out_of_stock` flag — when set, the product
        # is purchasable regardless of `quantity`. `is_low_stock` /
        # `is_out_of_stock` deliberately keep reflecting actual quantity
        # so merchant-side analytics still flag inventory that needs
        # restocking.
        if self.continue_selling_when_out_of_stock:
            return True
        return self.quantity > 0

    def variant_is_in_stock(self, variant: Any) -> bool:
        """Is `variant` buyable right now?

        ``ProductVariant.is_in_stock`` is a bare ``inventory_quantity > 0``
        and the variant entity holds no reference back to its product, so it
        cannot see the oversell flag. The result was a product that reported
        itself IN stock (the flag is set) while every one of its variants
        reported OUT — the storefront card offered a quick-add, the PDP
        greyed out Add-to-cart, and the cart route 400'd anything that got
        through. Ask the product, and the two levels agree.

        Merchant-facing reads (`/stores/**`, inventory reports) deliberately
        keep using ``variant.is_in_stock`` directly, for the same reason
        ``is_low_stock`` still tracks real quantity: a merchant needs to see
        what actually has to be restocked.
        """
        if self.continue_selling_when_out_of_stock:
            return True
        return bool(getattr(variant, "is_in_stock", True))

    @property
    def is_low_stock(self) -> bool:
        """Check if product is low on stock."""
        return 0 < self.quantity <= self.low_stock_threshold

    @property
    def is_out_of_stock(self) -> bool:
        """Check if product is out of stock (0 or oversold below zero)."""
        return self.quantity <= 0

    @property
    def is_on_sale(self) -> bool:
        """True when the customer pays less than the list price.

        Two independent ways that happens, and the storefront badge must
        light up for both: a permanent markdown (`compare_at_price` above
        `price`) or a scheduled sale whose window is currently open.
        """
        if self.sale_is_active():
            return True
        if self.compare_at_price is None:
            return False
        return self.price < self.compare_at_price

    @property
    def discount_percentage(self) -> float:
        """Percent off the price the customer would otherwise have paid.

        Measured against `compare_at_price` when there is one, else the
        list `price` — so a scheduled sale on a product with no
        compare-at still reports a real number instead of zero.
        """
        reference = self.compare_at_price or self.price
        effective = self.effective_price()
        if reference.amount <= 0 or effective.amount >= reference.amount:
            return 0.0
        discount = (reference.amount - effective.amount) / reference.amount * 100
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

    def image_alts(self) -> dict[str, str]:
        """Alt text per image URL, from the ``media_urls`` sidecar.

        Alt has nowhere else to live: ``images`` is a bare ``text[]`` of URLs.
        ``metadata.media_urls`` is already keyed by URL (the image pipeline
        writes variant URLs there), so alt rides alongside without touching the
        images column or the wholesale-replaced ``attributes``.
        """
        media = (self.metadata or {}).get("media_urls")
        if not isinstance(media, dict):
            return {}
        out: dict[str, str] = {}
        for url, meta in media.items():
            if not isinstance(meta, dict):
                continue
            alt = meta.get("alt")
            if isinstance(alt, str) and alt.strip():
                out[str(url)] = alt.strip()
        return out

    def set_image_alt(self, image_url: str, alt: str | None) -> None:
        """Set (or clear) alt for one image, preserving the rest of its sidecar."""
        media = dict((self.metadata or {}).get("media_urls") or {})
        entry = dict(media.get(image_url) or {})
        if alt and alt.strip():
            entry["alt"] = alt.strip()
        else:
            entry.pop("alt", None)
        media[image_url] = entry
        self.metadata = {**(self.metadata or {}), "media_urls": media}
        self.touch()
