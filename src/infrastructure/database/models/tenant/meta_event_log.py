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
        UniqueConstraint(
            "store_id", "event_id", name="uq_meta_event_log_store_event_id"
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
