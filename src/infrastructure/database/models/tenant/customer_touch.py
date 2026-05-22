"""CustomerTouch DB model — one row per UTM-tagged inbound visit.

The customer-journey timeline reads this table to show every
acquisition touch a customer had before (and after) converting. See
the alembic migration ``customer_touches_20260522`` for the table
shape.

Anonymous touches carry only ``session_fingerprint``; ``customer_id``
is backfilled at checkout when the session converts. Touches without
any UTM data are NOT captured here — that would dilute the timeline
with noise from refresh / internal nav.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TenantMixin, UUIDMixin


class CustomerTouchModel(Base, UUIDMixin, TenantMixin):
    __tablename__ = "customer_touches"
    __table_args__ = (
        Index(
            "ix_customer_touches_customer_ts",
            "customer_id",
            "ts",
            postgresql_where="customer_id IS NOT NULL",
        ),
        Index("ix_customer_touches_session_ts", "session_fingerprint", "ts"),
        Index("ix_customer_touches_store_ts", "store_id", "ts"),
        Index(
            "ix_customer_touches_campaign",
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
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    session_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    utm_source: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_term: Mapped[str | None] = mapped_column(String(200), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(200), nullable=True)
    gclid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    fbclid: Mapped[str | None] = mapped_column(String(256), nullable=True)
    referrer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    landing_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketing_campaigns.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_first_touch: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return (
            f"<CustomerTouchModel(customer_id={self.customer_id}, "
            f"session={self.session_fingerprint[:8]}, ts={self.ts})>"
        )
