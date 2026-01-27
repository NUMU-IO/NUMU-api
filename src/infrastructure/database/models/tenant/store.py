"""Store database model (public schema with tenant_id discriminator)."""

from sqlalchemy import Enum, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.store import StoreStatus
from src.core.value_objects.money import Currency
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, TimestampMixin, UUIDMixin


class StoreModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Store database model with tenant_id discriminator.
    
    Stores are scoped to a tenant. The owner_id references a user 
    in the public.users table.
    """

    __tablename__ = "stores"
    __table_args__ = {"schema": "public"}

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    owner_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    logo_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    banner_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    status: Mapped[StoreStatus] = mapped_column(
        Enum(StoreStatus, name="storestatus", schema="public"),
        default=StoreStatus.PENDING_APPROVAL,
        nullable=False,
    )
    default_currency: Mapped[Currency] = mapped_column(
        Enum(Currency, name="currency", schema="public"),
        default=Currency.USD,
        nullable=False,
    )
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    social_links: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships (all within public schema now)
    tenant = relationship("TenantModel", back_populates="stores", lazy="selectin")
    products = relationship("ProductModel", back_populates="store", lazy="selectin")
    categories = relationship("CategoryModel", back_populates="store", lazy="selectin")
    customers = relationship("CustomerModel", back_populates="store", lazy="selectin")
    orders = relationship("OrderModel", back_populates="store", lazy="selectin")
    invoices = relationship("InvoiceModel", back_populates="store", lazy="selectin")

    def __repr__(self) -> str:
        return f"<StoreModel(id={self.id}, name={self.name})>"
