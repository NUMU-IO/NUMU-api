"""Metafield database models (tenant-scoped).

Two tables in the ``public`` schema with a ``tenant_id`` discriminator
(mirrors the pages/coupons shape — tenant isolation is enforced at the
repository layer via the ``get_tenant_id()`` contextvar filter):

  - ``metafield_definitions`` — one typed field declaration per store,
    unique on ``(store_id, owner_type, namespace, key)``.
  - ``metafield_values`` — one concrete value per owner, unique on
    ``(definition_id, owner_id)``. ``value`` is canonical TEXT; the owning
    definition's ``type`` says how to read it back.

``owner_type`` and ``type`` are stored as plain ``String`` columns (not
Postgres ENUM types) — validation lives on the ``MetafieldOwnerType`` /
``MetafieldType`` StrEnums at the entity/schema layer, which sidesteps the
Postgres-enum ``values_callable`` / ALTER TYPE migration friction.
"""

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class MetafieldDefinitionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """Typed metafield declaration for a catalog resource within a store."""

    __tablename__ = "metafield_definitions"
    __table_args__ = (
        UniqueConstraint(
            "store_id",
            "owner_type",
            "namespace",
            "key",
            name="uq_metafield_def_store_owner_ns_key",
        ),
        Index(
            "ix_metafield_definitions_store_owner",
            "store_id",
            "owner_type",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False)
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_public: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    def __repr__(self) -> str:
        return (
            f"<MetafieldDefinitionModel(id={self.id}, "
            f"owner_type={self.owner_type}, key={self.namespace}.{self.key})>"
        )


class MetafieldValueModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One concrete metafield value for one owner (product/collection/page)."""

    __tablename__ = "metafield_values"
    __table_args__ = (
        UniqueConstraint(
            "definition_id",
            "owner_id",
            name="uq_metafield_values_definition_owner",
        ),
        Index(
            "ix_metafield_values_store_owner",
            "store_id",
            "owner_id",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    definition_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.metafield_definitions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    owner_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<MetafieldValueModel(id={self.id}, "
            f"definition_id={self.definition_id}, owner_id={self.owner_id})>"
        )
