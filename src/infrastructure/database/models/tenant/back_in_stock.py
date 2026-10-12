"""Back in Stock app (``back-in-stock``): its settings and its waiting list.

The app's backend lives in NUMU-api (docs/Plans/APPS/01-back-in-stock). Both
tables belong to the app: uninstall purges them after the retention window
(``numu_apps.PURGERS``), and the shopper data-rights export and delete cover
the waiters.

One partial unique index lives only in the migration, because it uses an
expression the SQLite test database cannot build: at most one ``waiting`` row
per (store, product, variant, contact), with ``variant_id`` NULL treated as a
value.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class BackInStockSettingsModel(Base, TimestampMixin, TenantMixin):
    """One row per store once the merchant saves; no row means the defaults."""

    __tablename__ = "back_in_stock_settings"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        primary_key=True,
    )
    #: ``phone_or_email`` · ``phone`` · ``email``: what the widget asks for.
    contact: Mapped[str] = mapped_column(String(20), nullable=False)
    signup_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    wa_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    email_cap: Mapped[int] = mapped_column(Integer, nullable=False)


class BackInStockWaiterModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A shopper waiting for one variant (or one product) to be buyable again.

    ``status``: ``waiting`` → ``queued`` → ``notified`` | ``failed``; or
    ``unsubscribed`` / ``closed``. ``updated_at`` marks the last status change,
    which the retention rule counts from. ``product_id`` has no foreign key on
    purpose: the row outlives a deleted product (it is closed, and the titles
    keep the history readable).
    """

    __tablename__ = "back_in_stock_waiters"
    __table_args__ = (
        Index(
            "ix_bis_waiters_store_product_status", "store_id", "product_id", "status"
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    variant_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    #: ``whatsapp`` · ``email``
    channel: Mapped[str] = mapped_column(String(10), nullable=False)
    #: E.164 phone or lower-cased email; NULL once erased (retention).
    contact: Mapped[str | None] = mapped_column(String(254), nullable=True)
    locale: Mapped[str] = mapped_column(String(5), nullable=False, default="ar")
    status: Mapped[str] = mapped_column(String(15), nullable=False, default="waiting")
    product_title: Mapped[str] = mapped_column(String(255), nullable=False)
    variant_title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    message_id: Mapped[str | None] = mapped_column(String(255))
    fail_reason: Mapped[str | None] = mapped_column(String(64))
    link_token: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    unsub_token: Mapped[str] = mapped_column(String(32), nullable=False, unique=True)
    clicked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    order_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True))
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The matching order lines, in the store currency's minor units.
    revenue: Mapped[int | None] = mapped_column(Integer)
