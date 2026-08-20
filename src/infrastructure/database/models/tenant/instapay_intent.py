"""Persistence for the per-order manual-payment payload.

Table name is historical: it predates the second manual rail (Vodafone
Cash) and a rename would buy nothing but a coordinated deploy. The
``method`` column is the discriminator — see
:class:`src.core.entities.instapay.ManualPaymentMethod`.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.instapay import (
    ManualPaymentIntentStatus,
    ManualPaymentMethod,
)
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class InstapayIntentModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Row-per-order manual-payment payload (InstaPay / Vodafone Cash).

    The reference code is globally unique (indexed for webhook-matching
    when a real API eventually arrives). One row per order — enforced by
    the unique constraint on ``order_id``, which also means an order can
    only ever be on one manual rail.
    """

    __tablename__ = "instapay_intents"
    __table_args__ = (
        Index(
            "ix_instapay_intents_order_id",
            "order_id",
            unique=True,
        ),
        Index(
            "ix_instapay_intents_reference_code",
            "reference_code",
            unique=True,
        ),
        Index(
            "ix_instapay_intents_status_expires",
            "status",
            "expires_at",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.orders.id", ondelete="CASCADE"),
        nullable=False,
    )
    reference_code: Mapped[str] = mapped_column(String(16), nullable=False)
    # Which manual rail this intent belongs to. ``server_default``
    # backfills every pre-Vodafone-Cash row as InstaPay, which is
    # what they all are.
    method: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        server_default=ManualPaymentMethod.INSTAPAY.value,
        default=ManualPaymentMethod.INSTAPAY.value,
    )
    # IPA for InstaPay, wallet number for Vodafone Cash. Column name
    # is rail-neutral; the entity calls it ``display_destination``.
    display_destination: Mapped[str] = mapped_column(String(80), nullable=False)
    display_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    # Empty string on rails with nothing to scan (Vodafone Cash).
    qr_payload: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[ManualPaymentIntentStatus] = mapped_column(
        Enum(
            ManualPaymentIntentStatus,
            name="instapay_intent_status_enum",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=ManualPaymentIntentStatus.AWAITING_PAYMENT,
    )
