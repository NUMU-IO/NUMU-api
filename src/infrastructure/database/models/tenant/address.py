"""Customer address database model (public schema with tenant_id discriminator)."""

from sqlalchemy import Boolean, Enum, Float, ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.address import AddressLabel
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class CustomerAddressModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Customer address database model with tenant_id discriminator."""

    __tablename__ = "customer_addresses"
    __table_args__ = {"schema": "public"}

    customer_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    address_line1: Mapped[str] = mapped_column(String(255), nullable=False)
    address_line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    state: Mapped[str | None] = mapped_column(String(100), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(20), nullable=True)
    country: Mapped[str] = mapped_column(String(100), nullable=False)
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    label: Mapped[AddressLabel] = mapped_column(
        Enum(AddressLabel, name="addresslabel", schema="public"),
        nullable=False,
        default=AddressLabel.HOME,
    )
    # Optional GPS / reverse-geocoded location captured from the storefront
    # checkout map picker. Enables reuse across orders and future proximity
    # queries (delivery zones, fraud clustering).
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_accuracy: Mapped[float | None] = mapped_column(Float, nullable=True)
    location_source: Mapped[str | None] = mapped_column(String(20), nullable=True)
    geocoded_address: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Relationships
    customer = relationship(
        "CustomerModel", back_populates="addresses", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<CustomerAddressModel(id={self.id}, customer_id={self.customer_id})>"
