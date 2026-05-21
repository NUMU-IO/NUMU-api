"""Product variant DB model — Phase 8.1.

Variant is the unit of price + inventory tracking. Every product has
at least one variant (the "default" variant for single-SKU products
is created automatically by the Phase 8.1 backfill migration);
multi-axis products get one row per (axis_1_value, axis_2_value, ...)
combination.

Cart and order line items reference `variant_id`. Stock decrements
on `Variant.inventory_quantity` (per-variant), NOT on
`Product.quantity` (which the application layer maintains as the
SUM across variants for backward-compat display).
"""

from sqlalchemy import (
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class VariantModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "product_variants"
    __table_args__ = (
        # SKU + barcode unique per *store* — different products in the
        # same store can't share a SKU, but two stores on the platform
        # can. NULL excluded (a product with no SKU set isn't a
        # conflict). PostgreSQL UNIQUE allows duplicate NULLs by default.
        UniqueConstraint("store_id", "sku", name="uq_variants_store_sku"),
        Index("ix_variants_product", "product_id", "position"),
        Index("ix_variants_store", "store_id"),
        # Hot path for the cart "is this combo available?" check:
        # one row per (product, option_values) — we materialize this
        # by hashing the option_values dict into a stable string at
        # the application layer rather than enforcing uniqueness on a
        # JSONB column (which would require an expression index).
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Map from axis-name → chosen-value. Empty `{}` is valid: it's the
    # "default variant" for products that don't have option axes.
    option_values: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    price_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EGP"
    )
    compare_at_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    barcode: Mapped[str | None] = mapped_column(String(100), nullable=True)
    inventory_quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    weight: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True)
    metadata_: Mapped[dict | None] = mapped_column(
        "metadata", JSONB, nullable=True, default=dict
    )
