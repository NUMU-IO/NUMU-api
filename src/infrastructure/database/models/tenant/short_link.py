"""Short-link DB model — feature #2 of post-attribution-feature roadmap.

Maps a globally-unique 8-char Crockford base32 ``short_code`` to a
pre-composed long URL. The redirector at ``GET /r/{short_code}``
resolves the row and 302s to ``destination_url``.

See alembic migration ``short_links_20260522`` for the table shape
and rationale. SEC notes:
* Globally unique short_code: collision-free at NUMU-wide scale; the
  UNIQUE index backstops the generator's retry loop.
* destination_url is treated as TRUSTED INPUT at read time — the
  service validates the host at create time (matches the store's
  canonical origin) so we never become an open redirector.
* ``is_active`` + ``expires_at`` let the merchant disable a link
  without deleting the row (which would lose click history).
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ShortLinkModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "short_links"
    __table_args__ = (
        # Hot read path for the redirector — must be UNIQUE so the
        # generator's collision-retry loop has something to race
        # against.
        Index(
            "uq_short_links_short_code",
            "short_code",
            unique=True,
        ),
        # Per-store listing in the hub: "all short links you've
        # created, newest first".
        Index("ix_short_links_store_created", "store_id", "created_at"),
        # Per-campaign filter for the campaign-detail "spawned links"
        # view; partial so it doesn't index the standalone-link rows.
        Index(
            "ix_short_links_campaign_id",
            "campaign_id",
            postgresql_where="campaign_id IS NOT NULL",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    short_code: Mapped[str] = mapped_column(String(12), nullable=False)
    destination_url: Mapped[str] = mapped_column(Text, nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketing_campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    click_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_clicked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Note: no ORM relationship to MarketingCampaignModel. The redirect
    # path (`GET /r/{short_code}`) is performance-critical — a one-row
    # lookup — and any `lazy=` strategy on `campaign` either fires a
    # second SELECT on every load (lazy="selectin") or risks accidental
    # sync IO in async context (lazy="select"). Callers that need the
    # campaign for a per-link list (e.g. the campaign-detail "spawned
    # links" view) should JOIN explicitly at query time via
    # ``select(...).outerjoin(MarketingCampaignModel, ...)``. The
    # ``campaign_id`` FK column remains for that purpose.

    def __repr__(self) -> str:
        return (
            f"<ShortLinkModel(short_code={self.short_code}, store_id={self.store_id})>"
        )
