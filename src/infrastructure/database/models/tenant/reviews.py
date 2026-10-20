"""Reviews app (``reviews``, «آراء العملاء»): its two tables.

The app's backend lives in NUMU-api (docs/Plans/APPS/03-reviews). The reviews
themselves are core ``product_reviews`` rows, which every theme already reads;
the app adds a few columns there and keeps only its settings and its review
requests here. No table holds a phone or an email: both are read from the
order when a request is sent. Uninstall purges both after the retention
window (``numu_apps.PURGERS``).
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    Boolean,
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


class ReviewsRequestModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """One review request per delivered order.

    ``status``: ``scheduled`` → ``sent`` → ``completed``, or ``skipped`` ·
    ``cancelled`` · ``failed``. The sweep sends a scheduled row once its
    ``due_at`` has passed and the store's send window is open. Only the
    SHA-256 of the link token is kept; the token itself lives in the message.
    """

    __tablename__ = "reviews_requests"
    __table_args__ = (
        UniqueConstraint("store_id", "order_id", name="uq_reviews_request_order"),
        Index("ix_reviews_requests_status_due", "status", "due_at"),
        Index("ix_reviews_requests_store_created", "store_id", "created_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = _store_fk(nullable=False)
    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(10), nullable=False)
    skip_reason: Mapped[str | None] = mapped_column(String(64))
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    token_hash: Mapped[str | None] = mapped_column(String(64), unique=True)
    provider_message_id: Mapped[str | None] = mapped_column(String(255))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewsSettingsModel(Base, TimestampMixin, TenantMixin):
    """One row per store, created with the defaults on the front's first call.
    The publishing rule is core's ``store.settings.review_moderation``."""

    __tablename__ = "reviews_settings"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = _store_fk(primary_key=True)
    requests_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True
    )
    delay_days: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    photos_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
