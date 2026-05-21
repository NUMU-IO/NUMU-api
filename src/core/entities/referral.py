"""Referral system domain entities."""

from datetime import datetime
from enum import StrEnum

from src.core.entities.base import BaseEntity


class ReferralStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"  # 12-month window expired
    CANCELLED = "cancelled"


class CommissionStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"  # order delivered
    PAID_OUT = "paid_out"
    REVERSED = "reversed"  # refund/RTO


class PayoutStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class MerchantReferral(BaseEntity):
    referrer_tenant_id: str
    referred_tenant_id: str
    referral_code: str
    status: ReferralStatus = ReferralStatus.PENDING
    commission_rate: float = 0.05  # 5%
    commission_cap_cents: int = 500_000  # 5,000 EGP
    total_commission_earned_cents: int = 0
    commission_expires_at: datetime | None = None
    activated_at: datetime | None = None
    free_months_granted: int = 0


class ReferralCommission(BaseEntity):
    referral_id: str
    order_id: str
    order_total_cents: int
    commission_cents: int
    status: CommissionStatus = CommissionStatus.PENDING
    confirmed_at: datetime | None = None
    paid_out_at: datetime | None = None
    reversed_at: datetime | None = None
    reversal_reason: str | None = None


class ReferralPayout(BaseEntity):
    referrer_tenant_id: str
    amount_cents: int
    payout_method: str  # vodafone_cash, instapay, bank_transfer
    payout_reference: str | None = None
    status: PayoutStatus = PayoutStatus.PENDING
    period_start: datetime | None = None
    period_end: datetime | None = None
    completed_at: datetime | None = None
