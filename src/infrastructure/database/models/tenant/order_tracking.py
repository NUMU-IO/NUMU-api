"""Order Tracking app (``order-tracking``, «مشوار»): its four tables.

The app's backend lives in NUMU-api (docs/Plans/APPS/02-order-tracking). It
keeps only what core does not: status, attempts and timestamps are read live
from ``shipments`` and ``orders``. No table holds a name, phone, email or
street. Uninstall purges all four after the retention window
(``numu_apps.PURGERS``); the shopper data-rights export and delete cover the
three that hold a shopper's orders.
"""

from datetime import date, datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
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


def _store_fk(**kw) -> Mapped[PyUUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("public.stores.id", ondelete="CASCADE"), **kw
    )


def _order_fk(**kw) -> Mapped[PyUUID]:
    return mapped_column(
        UUID(as_uuid=True), ForeignKey("public.orders.id", ondelete="CASCADE"), **kw
    )


class OrderTrackingParcelModel(Base, TimestampMixin, TenantMixin):
    """One row per shipped order: its area and the window promised at
    checkout, fixed when it ships so a zone edited later does not move it."""

    __tablename__ = "order_tracking_parcels"
    __table_args__ = (
        Index("ix_order_tracking_parcels_created", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(primary_key=True)
    order_id: Mapped[PyUUID] = _order_fk(primary_key=True)
    #: The order's newest forward shipment; NULL while it has none.
    shipment_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True))
    #: ISO 3166-2 (``EG-C``), from ``core/value_objects/geography.py``.
    governorate_code: Mapped[str | None] = mapped_column(String(8))
    city: Mapped[str | None] = mapped_column(String(100))
    eta_min: Mapped[date | None] = mapped_column(Date)
    eta_max: Mapped[date | None] = mapped_column(Date)


class OrderTrackingRetryRequestModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A shopper's "Try again" after a failed delivery. The note is blanked
    30 days after the merchant handles the request."""

    __tablename__ = "order_tracking_retry_requests"
    __table_args__ = (
        Index("ix_order_tracking_retry_store_order", "store_id", "order_id"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(nullable=False)
    order_id: Mapped[PyUUID] = _order_fk(nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    #: ``morning`` · ``afternoon`` · ``evening``
    window: Mapped[str] = mapped_column(String(10), nullable=False)
    note: Mapped[str | None] = mapped_column(String(200))
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    courier_notified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class OrderTrackingMessageModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One WhatsApp message per (order, kind, attempt): the dedupe key and the
    counters. Never the phone.

    ``status``: ``scheduled`` → ``sent`` | ``skipped`` | ``failed``.
    ``send_at`` is when it is due (quiet hours move it to 09:00), so the sweep
    can re-queue a send whose Celery message was lost.
    """

    __tablename__ = "order_tracking_messages"
    __table_args__ = (
        UniqueConstraint(
            "store_id", "order_id", "kind", "attempt", name="uq_order_tracking_message"
        ),
        Index("ix_order_tracking_messages_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(nullable=False)
    order_id: Mapped[PyUUID] = _order_fk(nullable=False)
    #: ``out_for_delivery`` · ``delivery_failed``
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    send_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OrderTrackingSettingsModel(Base, TimestampMixin, TenantMixin):
    """One row per store, created with the defaults on the front's first call."""

    __tablename__ = "order_tracking_settings"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = _store_fk(primary_key=True)
    out_for_delivery_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    failed_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    show_items: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    show_contact: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
