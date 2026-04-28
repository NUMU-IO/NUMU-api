"""Persistence for customer-submitted payment proofs (InstaPay today).

One order can have many proofs (rejected uploads can be retried), so we
deliberately index rather than uniquely constrain ``order_id``. The strict
uniqueness we *do* want is per-store dedup on (image hash) and
(transaction ref), which protects against trivial screenshot replay.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.core.entities.instapay import PaymentProofStatus
from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import (
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class PaymentProofModel(Base, UUIDMixin, TenantMixin, TimestampMixin):
    """Customer-uploaded proof of an out-of-band payment."""

    __tablename__ = "payment_proofs"
    __table_args__ = (
        Index("ix_payment_proofs_order_id", "order_id"),
        Index("ix_payment_proofs_status", "status"),
        Index("ix_payment_proofs_store_created", "store_id", "created_at"),
        # Phase A pHash dedup — narrows ``find_perceptual_neighbours``
        # scans to a covering range. Hamming distance is computed in
        # Python on the small per-store window the index returns.
        Index("ix_payment_proofs_store_phash", "store_id", "perceptual_hash"),
        UniqueConstraint(
            "store_id",
            "proof_image_hash",
            name="uq_payment_proofs_store_image_hash",
        ),
        UniqueConstraint(
            "store_id",
            "transaction_ref",
            name="uq_payment_proofs_store_transaction_ref",
        ),
        # Scoped to store so two different merchants can use the same
        # client-generated key without colliding. An old key on an
        # expired proof also never blocks a fresh key in the same
        # store because the sweeper clears them after 30 days
        # (see ``expire_instapay_orders_task``).
        UniqueConstraint(
            "store_id",
            "idempotency_key",
            name="uq_payment_proofs_store_idempotency_key",
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
    proof_image_key: Mapped[str] = mapped_column(Text, nullable=False)
    proof_image_hash: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    transaction_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    declared_amount_cents: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    status: Mapped[PaymentProofStatus] = mapped_column(
        Enum(
            PaymentProofStatus,
            name="payment_proof_status_enum",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=PaymentProofStatus.AWAITING_REVIEW,
    )
    review_decision_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    review_decision_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 64-bit pHash of the sanitized image. Nullable so older rows
    # predating Phase A continue to work; new uploads always populate.
    perceptual_hash: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )
    # Phase C OCR enrichment. All nullable; rows predating Phase C
    # carry NULLs and the auto-approval rules silently no-op for them.
    ocr_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_extracted_amount_cents: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    ocr_extracted_ipa: Mapped[str | None] = mapped_column(String(80), nullable=True)
    ocr_raw_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    ocr_processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    # Phase C extras — note / txn-ref / recipient-name extracted by
    # the same OCR pass for cross-checks against expected values.
    ocr_extracted_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_extracted_transaction_ref: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    ocr_extracted_recipient_name: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )
    # Phase D — flattened ``decision.reasons`` from the rules engine
    # (e.g. ``ocr_amount_mismatch``). NULL means "approved" or
    # "predates the column"; the review pane treats both the same.
    auto_approval_block_reasons: Mapped[list[str] | None] = mapped_column(
        ARRAY(Text),
        nullable=True,
    )

    # Back-reference so listing-style queries can selectinload proofs
    # alongside their orders in one round-trip. ``lazy="raise"`` makes
    # any accidental sync load in async code fail loudly instead of
    # silently issuing a blocking query.
    order = relationship(
        "OrderModel",
        back_populates="payment_proofs",
        lazy="raise",
    )
