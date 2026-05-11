"""Gift card DB models — Phase 8.3.

Two tables: `gift_cards` (card metadata + current balance) and
`gift_card_transactions` (append-only ledger). The current_balance
on the card is denormalized — the ledger sum is authoritative, but
keeping the column avoids a SUM() on every checkout-redemption read.
"""

from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.core.entities.gift_card import GiftCardStatus, TransactionKind
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class GiftCardModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "gift_cards"
    __table_args__ = (
        # SHA-256 hashes are unique across the entire platform — a
        # customer-typed code maps to exactly one card. The store_id
        # scope is enforced by the application layer (a code from
        # store A can't be redeemed at store B even if hashes match
        # by absurd coincidence).
        UniqueConstraint("code_hash", name="uq_gift_cards_code_hash"),
        # Hot path: list all cards for a store (hub gift-cards page).
        Index("ix_gift_cards_store", "store_id"),
        # Per-customer lookup for "show me my gift cards" account page.
        Index(
            "ix_gift_cards_customer",
            "customer_id",
            postgresql_where="customer_id IS NOT NULL",
        ),
        # Expiry sweep: find cards about to expire to send a reminder.
        Index(
            "ix_gift_cards_expires_at",
            "expires_at",
            postgresql_where="expires_at IS NOT NULL AND status = 'active'",
        ),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_four: Mapped[str] = mapped_column(String(4), nullable=False)
    initial_balance_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    current_balance_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    status: Mapped[GiftCardStatus] = mapped_column(
        Enum(
            GiftCardStatus,
            name="giftcardstatus",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=GiftCardStatus.ACTIVE,
    )
    customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    issued_by_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    issuing_order_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        nullable=True,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class GiftCardTransactionModel(Base, UUIDMixin, TimestampMixin, TenantMixin):
    __tablename__ = "gift_card_transactions"
    __table_args__ = (
        # Newest-first ledger view for the hub's card-detail page.
        Index("ix_gc_tx_card", "gift_card_id", "created_at"),
        Index("ix_gc_tx_order", "order_id"),
        {"schema": "public"},
    )

    store_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=False,
    )
    gift_card_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.gift_cards.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[TransactionKind] = mapped_column(
        Enum(
            TransactionKind,
            name="giftcardtransactionkind",
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
    )
    # Signed delta — positive for issue/refund/adjust-up, negative
    # for redeem/void/adjust-down.
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    order_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    actor_user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    actor_customer_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.customers.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
