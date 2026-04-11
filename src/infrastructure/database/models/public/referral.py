"""Referral system database models.

Cross-tenant by design — the referrer and referred belong to different
tenants, so these tables live in the public schema without RLS.
"""

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class MerchantReferralModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "merchant_referrals"
    __table_args__ = {"schema": "public"}

    referrer_tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.tenants.id"), nullable=False, index=True
    )
    referred_tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.tenants.id"), nullable=False, unique=True
    )
    referral_code: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    commission_rate: Mapped[float] = mapped_column(
        Numeric(5, 4), nullable=False, default=0.05
    )
    commission_cap_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=500_000
    )
    total_commission_earned_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0
    )
    commission_expires_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    activated_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    free_months_granted: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class ReferralCommissionModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "referral_commissions"
    __table_args__ = {"schema": "public"}

    referral_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.merchant_referrals.id"),
        nullable=False,
        index=True,
    )
    order_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    order_total_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    commission_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    confirmed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_out_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reversed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reversal_reason: Mapped[str | None] = mapped_column(String(100), nullable=True)


class ReferralPayoutModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "referral_payouts"
    __table_args__ = {"schema": "public"}

    referrer_tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True), ForeignKey("public.tenants.id"), nullable=False, index=True
    )
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    payout_method: Mapped[str] = mapped_column(String(50), nullable=False)
    payout_reference: Mapped[str | None] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    period_start: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    period_end: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[str | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
