"""Saved payment method model — stores card tokens for one-click charges."""

from uuid import UUID as PyUUID

from sqlalchemy import Boolean, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class SavedPaymentMethodModel(Base, UUIDMixin, TimestampMixin):
    """Saved payment method for one-click upsell charges."""

    __tablename__ = "saved_payment_methods"
    __table_args__ = {"schema": "public"}

    customer_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    gateway: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # "paymob" or "kashier"
    card_token: Mapped[str] = mapped_column(String(500), nullable=False)
    display_name: Mapped[str] = mapped_column(
        String(100), nullable=True
    )  # "Visa •••• 4242"
    card_brand: Mapped[str | None] = mapped_column(String(50), nullable=True)
    last_four: Mapped[str | None] = mapped_column(String(4), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    def __repr__(self) -> str:
        return f"<SavedPaymentMethod(id={self.id}, customer={self.customer_id}, gateway={self.gateway})>"
