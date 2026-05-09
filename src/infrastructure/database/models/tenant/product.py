"""Product database model (public schema with tenant_id discriminator)."""

from decimal import Decimal

from sqlalchemy import Computed, Enum, ForeignKey, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.product import ProductStatus, ProductType
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ProductModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Product database model with tenant_id discriminator."""

    __tablename__ = "products"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    sku: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    short_description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    product_type: Mapped[ProductType] = mapped_column(
        Enum(ProductType, name="producttype", schema="public"),
        default=ProductType.PHYSICAL,
        nullable=False,
    )
    status: Mapped[ProductStatus] = mapped_column(
        Enum(ProductStatus, name="productstatus", schema="public"),
        default=ProductStatus.DRAFT,
        nullable=False,
        index=True,
    )

    # Pricing (stored in cents)
    price_amount: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    price_currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="EGP"
    )
    compare_at_price: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_price: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Inventory
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    low_stock_threshold: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    # Physical properties
    weight: Mapped[Decimal | None] = mapped_column(Numeric(10, 2), nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Media and categorization
    images: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True, default=list
    )
    category_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    tags: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True, default=list
    )

    # SEO
    seo_title: Mapped[str | None] = mapped_column(String(60), nullable=True)
    seo_description: Mapped[str | None] = mapped_column(String(160), nullable=True)

    # Additional data
    attributes: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Phase 4.1 — Postgres tsvector for full-text search. Maintained
    # by the database via GENERATED ALWAYS AS (...) STORED — see
    # alembic/versions/20260508_add_product_search_tsvector.py.
    # Read-only from the ORM's perspective.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR,
        Computed(
            "setweight(to_tsvector('simple', coalesce(name, '')), 'A') || "
            "setweight(to_tsvector('simple', coalesce(sku, '')), 'B') || "
            "setweight(to_tsvector('simple', coalesce(description, '')), 'C') || "
            "setweight(to_tsvector('simple', coalesce(array_to_string(tags, ' '), '')), 'D')",
            persisted=True,
        ),
        nullable=True,
    )

    # Relationships
    store = relationship("StoreModel", back_populates="products", lazy="selectin")
    category = relationship("CategoryModel", back_populates="products", lazy="selectin")

    def __repr__(self) -> str:
        return f"<ProductModel(id={self.id}, name={self.name})>"
