"""Product bundle (Frequently Bought Together) database model.

Stores merchant-curated product bundles. Each row represents a single
bundled product linked to a primary (trigger) product. A primary product
can have many bundled products, each with an optional discount and a
display position for ordering.

Multi-tenant via TenantMixin; scoped to a store via store_id FK.
"""

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ProductBundleModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Frequently Bought Together / Product Bundle database model.

    Design decisions:
    - One row per (primary_product, bundled_product) pair — simple, queryable,
      and avoids JSONB arrays that are hard to index/join.
    - discount_type uses a plain VARCHAR instead of a DB-level ENUM so we can
      add new types (e.g. "buy_x_get_y") without a migration.
    - position column enables drag-and-drop ordering in the dashboard.
    - section_title_* columns let the merchant customise the widget heading
      per primary product (defaults are set at the application layer).
    """

    __tablename__ = "product_bundles"
    __table_args__ = (
        # Prevent duplicate (primary, bundled) pairs within a store
        UniqueConstraint(
            "store_id",
            "primary_product_id",
            "bundled_product_id",
            name="uq_bundle_pair_per_store",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The product whose detail page shows the bundle widget
    primary_product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The product being recommended alongside the primary
    bundled_product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # ── Discount ──────────────────────────────────────────────────────────
    discount_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="none",
        server_default="none",
        comment="percentage | fixed | none",
    )
    discount_value: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Percentage (0-100) or fixed amount in cents",
    )

    # ── Display ───────────────────────────────────────────────────────────
    position: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        server_default="0",
        comment="Sort order in the bundle widget (lower = first)",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )

    # Optional per-bundle section title (overrides global default)
    section_title_en: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Custom widget heading (English)",
    )
    section_title_ar: Mapped[str | None] = mapped_column(
        String(200),
        nullable=True,
        comment="Custom widget heading (Arabic)",
    )

    # ── Relationships (lazy to avoid N+1 in list queries) ─────────────────
    primary_product = relationship(
        "ProductModel",
        foreign_keys=[primary_product_id],
        lazy="selectin",
    )
    bundled_product = relationship(
        "ProductModel",
        foreign_keys=[bundled_product_id],
        lazy="selectin",
    )

    def __repr__(self) -> str:
        return (
            f"<ProductBundleModel(id={self.id}, "
            f"primary={self.primary_product_id}, "
            f"bundled={self.bundled_product_id})>"
        )
