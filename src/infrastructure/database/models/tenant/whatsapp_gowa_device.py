"""Per-store GOWA device registry.

GOWA hosts many logged-in WhatsApp accounts in one instance and selects which
one sends via the ``X-Device-Id`` header. This table is the mapping from a
store to its device, and it is the reason the transport never has to guess:
sending with the wrong device id would deliver one merchant's message from
another merchant's number.

One ACTIVE row per store. History is preserved — unpairing marks the row
inactive rather than deleting it, so an investigation into "which number sent
this" survives a re-pair.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class WhatsAppGowaDeviceModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    """A store's paired GOWA device (an actual logged-in WhatsApp account)."""

    __tablename__ = "whatsapp_gowa_devices"
    __table_args__ = (
        # The hot path is "which device sends for this store", run on every
        # outbound notification.
        Index(
            "ix_wa_gowa_device_store_active",
            "store_id",
            "is_active",
        ),
        # Inbound webhooks arrive keyed by device, not by store, so the reverse
        # lookup needs to be just as cheap.
        Index("ix_wa_gowa_device_device_id", "device_id"),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )

    # GOWA's own identifier, passed back as the X-Device-Id header.
    device_id: Mapped[str] = mapped_column(String(128), nullable=False)

    # The WhatsApp number this device is logged in as, E.164. Recorded once
    # pairing completes so the admin UI can show WHICH number a merchant put at
    # risk, and so support can answer "who sent this" without opening GOWA.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Lifecycle, mirroring what GOWA reports:
    #   pending      — device slot created, QR/pairing code not yet scanned
    #   connected    — logged in and reachable
    #   disconnected — session dropped; whatsmeow may reconnect on its own
    #   logged_out   — WhatsApp ended the session (or the number was banned);
    #                  needs a fresh pair, and the merchant must be told
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )

    # Soft-delete. Unpair flips this instead of deleting so the audit trail of
    # which number sent which message survives.
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    paired_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last time GOWA told us anything about this device (ack, inbound message,
    # status change). Staleness here is the signal that a session died quietly,
    # which is the common failure mode for an unofficial transport.
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Why the session ended, verbatim from GOWA, for support triage. A ban and
    # an ordinary disconnect both land as logged_out, and only this text tells
    # them apart.
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Who accepted the ban risk, and when. This transport is unofficial: a
    # merchant's own number can be banned by WhatsApp for automated sending, so
    # switching a merchant onto it is an explicit, attributable decision rather
    # than a silent config change.
    consent_acknowledged_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    consent_acknowledged_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<WhatsAppGowaDevice store={self.store_id} device={self.device_id} "
            f"status={self.status} active={self.is_active}>"
        )
