"""MetaEventLog database model.

Audit + idempotency log for every Meta Conversions API event the
platform sends (or attempts). Lives in the ``public`` schema with a
``tenant_id`` discriminator (same pattern as the rest of the
tenant-scoped models — RLS enforces isolation).

The ``UNIQUE (store_id, event_id)`` constraint is the **server-side
dedup primitive**. Phase 2's Celery task inserts a row *before*
contacting Meta; an IntegrityError on insert means the event was
already sent and the task short-circuits.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class MetaEventLogModel(Base, UUIDMixin, TenantMixin):
    """Database model for ``meta_event_log`` rows.

    Append-mostly: fields after ``request_payload`` are filled in by the
    Celery task once Meta responds (or by retry attempts). The created_at
    column has a server default; there is no ``updated_at`` because
    callers explicitly stamp ``sent_at`` instead — keeps the history
    of "when did Meta acknowledge this" precise.
    """

    __tablename__ = "meta_event_log"
    __table_args__ = (
        # (store, PIXEL, event) — not (store, event). Meta scopes its own
        # deduplication to a single Pixel ID, so the same event_id fanned out
        # to a store's second and third pixels is CORRECT. Keying without
        # pixel_id meant this constraint rejected pixels 2..N as duplicates
        # before they ever reached Meta: a multi-pixel store was silently a
        # single-pixel store.
        UniqueConstraint(
            "store_id",
            "pixel_id",
            "event_id",
            name="uq_meta_event_log_store_pixel_event_id",
        ),
        # Dashboard "recent events" query — covers store + event_name +
        # newest-first ordering in a single index seek.
        Index(
            "idx_meta_event_log_store_event",
            "store_id",
            "event_name",
            "created_at",
        ),
        # Partial index for the "failing" filter and for retry sweeps.
        Index(
            "idx_meta_event_log_failed",
            "store_id",
            postgresql_where="response_status >= 400 OR response_status IS NULL",
        ),
        # The outbox claim query: ORDER BY (priority, next_retry_at) with a
        # range filter on next_retry_at. Partial on the two open states, which
        # are a tiny fraction of the table — nearly every row reaches a
        # terminal status within seconds of being written.
        Index(
            "idx_meta_event_log_delivery_due",
            "priority",
            "next_retry_at",
            postgresql_where="status IN ('pending', 'retrying')",
        ),
        # "What does this store still owe Meta?" for the hub + admin views.
        Index(
            "idx_meta_event_log_store_open",
            "store_id",
            "status",
            postgresql_where="status IN ('pending', 'retrying')",
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

    # Redacted copy of what we POSTed to Meta. PII is hashed before it
    # ever reaches this column (see infrastructure/external_services/
    # meta/hashing.py), so storage here is safe for support tickets.
    request_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)

    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    fbtrace_id: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ── Outbox lifecycle ─────────────────────────────────────────────
    # This table is the CAPI outbox, not merely its audit log: the row is
    # the durable record of an owed delivery, and these five columns are
    # what make "owed" a thing the platform can see and act on.
    #
    # `status` is TEXT, not a PG enum — adding a value to a native enum
    # takes a DDL lock, and this vocabulary will grow. Values come from
    # `core.services.meta_delivery_policy.DeliveryStatus`.
    #
    # The server default is `legacy` (rows written before the lifecycle
    # existed); the application always stamps an explicit status, so the
    # default only ever applies to history.
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="legacy", default="pending"
    )
    # Doubles as the retry schedule and the claim lease: a claimed row has
    # it pushed into the future, so a worker that dies mid-send releases the
    # row by simply letting the lease lapse.
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The instant after which sending this event would DOUBLE-COUNT the
    # conversion rather than merge with it — event_time + Meta's 48h dedup
    # window. Not a retention field.
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # 0 conversion, 1 standard, 2 bulk. Orders the claim query so a Purchase
    # never waits behind a PageView backlog.
    priority: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="1", default=1
    )
    # Why the last attempt failed, at the granularity the retry decision
    # uses (`meta_delivery_policy.FailureKind`). A dead token and a
    # malformed payload are both "4xx" and need very different responses.
    failure_kind: Mapped[str | None] = mapped_column(Text, nullable=True)

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

    # Relationship is read-only / no autoload — meta_event_log is hot
    # and we never need the Store object eagerly.
    store = relationship("StoreModel", lazy="noload")

    def __repr__(self) -> str:
        return (
            f"<MetaEventLogModel(id={self.id}, event_name={self.event_name}, "
            f"event_id={self.event_id}, store_id={self.store_id})>"
        )
