"""Ordered product series for books and other sequential catalogs."""

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class SeriesModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "series"
    __table_args__ = (
        UniqueConstraint("store_id", "slug", name="uq_series_store_slug"),
        Index("ix_series_store", "store_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_image_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    metadata_: Mapped[dict] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default="{}"
    )


class SeriesProductModel(Base, TimestampMixin, TenantMixin):
    __tablename__ = "series_products"
    __table_args__ = (
        UniqueConstraint("series_id", "product_id", name="uq_series_product"),
        UniqueConstraint("series_id", "position", name="uq_series_position"),
        Index("ix_series_products_product", "product_id"),
        {"schema": "public"},
    )

    series_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.series.id", ondelete="CASCADE"),
        primary_key=True,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        primary_key=True,
    )
    volume_label: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
