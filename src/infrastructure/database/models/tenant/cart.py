"""Cart database models (public schema with tenant_id discriminator)."""

from sqlalchemy import ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, TimestampMixin, UUIDMixin


class CartItemModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Cart item database model."""

    __tablename__ = "cart_items"
    __table_args__ = {"schema": "public"}

    cart_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.carts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.products.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    variant_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )

    # Relationships
    cart = relationship("CartModel", back_populates="items", lazy="selectin")
    product = relationship("ProductModel", lazy="selectin")

    def __repr__(self) -> str:
        return f"<CartItemModel(id={self.id}, product_id={self.product_id}, qty={self.quantity})>"


class CartModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Cart database model."""

    __tablename__ = "carts"
    __table_args__ = {"schema": "public"}

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationships
    store = relationship("StoreModel", lazy="selectin")
    customer = relationship("CustomerModel", lazy="selectin")
    items = relationship(
        "CartItemModel",
        back_populates="cart",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<CartModel(id={self.id}, customer_id={self.customer_id})>"
