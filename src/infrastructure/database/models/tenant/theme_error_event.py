"""Theme error event model — durable shopper-side bundle-crash telemetry.

Append-only, tenant-scoped log of client-side theme bundle errors reported
by the storefront beacon (``POST /storefront/store/{store_id}/theme-error``).
Stored per ``theme_version`` so a merchant/platform can correlate a crash
spike to a specific publish — "version X started crashing after we shipped
it" (Phase 3 moat item).

Lives in the ``public`` schema with a ``tenant_id`` discriminator (same
pattern as page_views / funnel_events). RLS is ENABLED for read-side tenant
isolation but deliberately NOT forced: the ingest endpoint is public and
stamps ``tenant_id`` server-side after resolving the store, so the
best-effort insert must never be blocked by a missing request tenant
context.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class ThemeErrorEventModel(Base, UUIDMixin, TenantMixin):
    """A single client-side theme bundle error reported by the storefront.

    Append-only — no ``updated_at``. ``occurred_at`` is the event time
    (server-stamped at ingest; the beacon payload carries no client clock)
    and ``created_at`` is the row-insert time — mirrors the meta_event_log
    ``event_time`` / ``created_at`` split.
    """

    __tablename__ = "theme_error_events"
    __table_args__ = (
        # Read query: per-theme_version crash counts + last_seen over a
        # window, scoped to a store. Covers store + version + time-ordering
        # in a single index seek.
        Index(
            "ix_theme_error_events_store_version_occurred",
            "store_id",
            "theme_version",
            "occurred_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    theme_slug: Mapped[str | None] = mapped_column(String(255), nullable=True)
    theme_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    bundle_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Truncated at ingest (the route caps length) — stored as TEXT so a long
    # stack is never rejected on a DB length constraint; the app layer bounds
    # it before insert.
    message: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    def __repr__(self) -> str:
        return (
            f"<ThemeErrorEventModel(id={self.id}, theme_version={self.theme_version})>"
        )
