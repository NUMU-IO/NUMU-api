"""Billing database models — invoices, discount codes, subscription metadata.

These live in the public schema (not tenant-scoped) because billing is
a platform-level concern between NUMU and the tenant owner.
"""

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class BillingInvoiceModel(Base, UUIDMixin, TimestampMixin):
    """Subscription invoice for a billing period."""

    __tablename__ = "billing_invoices"
    __table_args__ = {"schema": "public"}

    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    period_start: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[str] = mapped_column(DateTime(timezone=True), nullable=False)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft"
    )  # draft, paid, failed, refunded, void
    paymob_transaction_id: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    discount_code_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.discount_codes.id", ondelete="SET NULL"),
        nullable=True,
    )
    discount_amount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    paid_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[str | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DiscountCodeModel(Base, UUIDMixin, TimestampMixin):
    """Promotional discount codes (LAUNCH50, FOUNDING100, etc.)."""

    __tablename__ = "discount_codes"
    __table_args__ = {"schema": "public"}

    code: Mapped[str] = mapped_column(
        String(50), unique=True, nullable=False, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # percent, fixed, free_months
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    applies_to_plans: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    valid_from: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stackable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
