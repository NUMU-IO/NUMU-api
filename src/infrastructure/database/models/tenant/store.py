"""Store database model (tenant schema)."""

from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.store import StoreStatus
from src.core.value_objects.money import Currency
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class StoreModel(Base, UUIDMixin, TimestampMixin):
    """Store database model (tenant schema).
    
    This represents the store configuration within a tenant's schema.
    Note: The owner_id references a user in the public.users table.
    """

    __tablename__ = "stores"
    # No schema specified - will use the tenant's search_path

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
        Enum(StoreStatus),
        default=StoreStatus.PENDING_APPROVAL,
        nullable=False,
    )
    default_currency: Mapped[Currency] = mapped_column(
        Enum(Currency),
        default=Currency.USD,
        nullable=False,
    )
    contact_email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    contact_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    address: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    social_links: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True, default=dict)

    # Relationships (within tenant schema)
    products = relationship("ProductModel", back_populates="store", lazy="selectin")
    categories = relationship("CategoryModel", back_populates="store", lazy="selectin")
    customers = relationship("CustomerModel", back_populates="store", lazy="selectin")
    orders = relationship("OrderModel", back_populates="store", lazy="selectin")

    def __repr__(self) -> str:
        return f"<StoreModel(id={self.id}, name={self.name})>"
