"""MetaMatchQualitySnapshot database model.

One row per (store, pixel, event) poll of Meta's **Dataset Quality API** —
the platform's only source of truth for Event Match Quality.

Why this table exists: until it did, ``MetaMatchQualityService.get_snapshots``
returned a hardcoded empty list and the merchant hub rendered a permanent
"connect Meta Business" empty state. NUMU could not answer "what is this
store's EMQ?" for any store, which made every signal-quality change
unfalsifiable — you cannot improve what you cannot measure.

Append-only, one row per poll: the history is the point. A merchant needs to
see that `Purchase` moved 6.1 → 8.2 *after* a change landed, and Meta computes
its score over a rolling recent window, so a single "current" row would erase
exactly the comparison that proves the work.

``match_key_coverage`` and ``diagnostics`` are stored as JSONB rather than
normalised: they are Meta's payload, Meta owns their shape, and pinning that
shape into columns would make us re-migrate every time they add a field. The
same reasoning as ``meta_event_log.request_payload``.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class MetaMatchQualitySnapshotModel(Base, UUIDMixin, TenantMixin):
    """Database model for ``meta_match_quality_snapshot`` rows."""

    __tablename__ = "meta_match_quality_snapshot"
    __table_args__ = (
        # The dashboard's only read pattern: newest snapshot per event for
        # one store+pixel. Covers the ORDER BY so it is an index scan, not a
        # sort over the whole history.
        Index(
            "idx_meta_mq_store_pixel_event_captured",
            "store_id",
            "pixel_id",
            "event_name",
            "captured_at",
        ),
        # Retention sweep + the platform-wide admin overview both scan by age.
        Index("idx_meta_mq_captured_at", "captured_at"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    pixel_id: Mapped[str] = mapped_column(Text, nullable=False)
    event_name: Mapped[str] = mapped_column(Text, nullable=False)

    # Meta's `composite_score`, 0.0–10.0. NUMERIC(3,1) not float: this is
    # displayed verbatim to merchants and compared across polls, so binary
    # float drift would show up as a score that "changed" without moving.
    emq_score: Mapped[float] = mapped_column(Numeric(3, 1), nullable=False)
    dedup_rate: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    # 7-day average % of Pixel events also covered by CAPI — Meta measuring
    # the browser-vs-server gap we previously had to infer by hand.
    event_coverage: Mapped[float | None] = mapped_column(Numeric(5, 2), nullable=True)
    total_events: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # {"em": 0.0, "fbp": 63.6, …} — per-identifier coverage percentages.
    match_key_coverage: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    # Meta's own [{name, description, solution, percentage, …}]. Rendered
    # verbatim in the hub — their copy names the fix better than ours would.
    diagnostics: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    data_freshness: Mapped[str | None] = mapped_column(Text, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
