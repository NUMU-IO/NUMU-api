"""Menu database model — store navigation / link lists.

Public schema with a tenant_id discriminator (RLS), one row per menu.
"""

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class MenuModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A store navigation menu (link list) with a unique handle per store."""

    __tablename__ = "menus"
    __table_args__ = (
        UniqueConstraint("store_id", "handle", name="uq_menus_store_handle"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    handle: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    # Bilingual title: {"en": ..., "ar": ...}
    title: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Nested items (depth <= 3): {id, label:{en,ar}, url, type, resource_id?, children:[...]}
    items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return f"<MenuModel(id={self.id}, handle={self.handle})>"
