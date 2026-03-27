"""Network contribution log — append-only GDPR rollback ledger."""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import UUIDMixin


class NetworkContributionLogModel(Base, UUIDMixin):
    """Tracks which store contributed which event to network_reputation.

    Used for GDPR shop/redact to decrement aggregates when a merchant's
    data is deleted.  No FK on store_id so logs survive store deletion
    during the 30-day GDPR window.
    """

    __tablename__ = "network_contribution_log"
    __table_args__ = (
        Index("ix_ncl_store_id", "store_id"),
        Index("ix_ncl_phone_hash", "phone_hash"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
    )
    phone_hash: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
