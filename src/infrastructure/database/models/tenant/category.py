"""Category database model (public schema with tenant_id discriminator)."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class CategoryModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Category database model with tenant_id discriminator."""

    __tablename__ = "categories"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Every slug this category ever had — see the twin column on products.
    # The storefront resolves an old collection slug back to this row and
    # 301s to the canonical URL instead of 404ing the indexed URL.
    previous_slugs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Alternate template variant key suffix (Shopify-style); null = base template.
    template_suffix: Mapped[str | None] = mapped_column(String(32), nullable=True)
    extra_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships
    store = relationship("StoreModel", back_populates="categories", lazy="selectin")
    products = relationship("ProductModel", back_populates="category", lazy="selectin")
    parent = relationship(
        "CategoryModel", remote_side="CategoryModel.id", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<CategoryModel(id={self.id}, name={self.name})>"
