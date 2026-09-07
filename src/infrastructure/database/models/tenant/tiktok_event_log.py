"""TikTokEventLog database model.

Audit + idempotency log for every TikTok Events API event the platform
sends (or attempts). Sibling of ``meta_event_log``; lives in the
``public`` schema with a ``tenant_id`` discriminator (RLS enforces
isolation).

The ``UNIQUE (store_id, pixel_id, event_id)`` constraint is the
**server-side dedup primitive**. The Celery task inserts a row *before*
contacting TikTok; an IntegrityError on insert means this pixel already
received the event and the task short-circuits. ``pixel_id`` is part of
the key because TikTok's own dedup window is per Pixel Code, so one
event_id fanned out to several of a store's pixels is correct.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class TikTokEventLogModel(Base, UUIDMixin, TenantMixin):
    """Database model for ``tiktok_event_log`` rows.

    Append-mostly: fields after ``request_payload`` are filled in by the
    Celery task once TikTok responds (or by retry attempts). ``created_at``
    has a server default; there is no ``updated_at`` because callers
    explicitly stamp ``sent_at`` instead.
    """

    __tablename__ = "tiktok_event_log"
    __table_args__ = (
        # (store, PIXEL, event) — not (store, event). TikTok scopes its own
        # deduplication to a single Pixel Code, so the same event_id fanned out
        # to a store's second and third pixels is CORRECT. Keying without
        # pixel_id meant this constraint rejected pixels 2..N as duplicates
        # before they ever reached TikTok: a multi-pixel store was silently a
        # single-pixel store, and the log recorded the drop as an ordinary
        # "duplicate". See migration `tiktok_pixel_dedup_20260908`.
        UniqueConstraint(
            "store_id",
            "pixel_id",
            "event_id",
            name="uq_tiktok_event_log_store_pixel_event_id",
        ),
        Index(
            "idx_tiktok_event_log_store_event",
            "store_id",
            "event_name",
            "created_at",
        ),
        Index(
            "idx_tiktok_event_log_failed",
            "store_id",
            postgresql_where=(
                "response_status >= 400 OR response_status IS NULL "
                "OR response_code <> 0"
            ),
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Plain TEXT — see entity docstring for why event_id isn't a UUID.
    event_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    pixel_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Redacted copy of what we POSTed to TikTok. PII is hashed before it
    # ever reaches this column (see external_services/tiktok/hashing.py),
    # so storage here is safe for support tickets.
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # TikTok business-level code (0 == OK); the Events API answers HTTP
    # 200 even on logical errors and carries the real result here.
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    request_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="1", default=1
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<TikTokEventLogModel(id={self.id}, event_name={self.event_name}, "
            f"event_id={self.event_id}, store_id={self.store_id})>"
        )
