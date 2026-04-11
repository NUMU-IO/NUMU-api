"""Subscription and billing domain entities."""

from datetime import datetime
from enum import StrEnum

from src.core.entities.base import BaseEntity


class SubscriptionStatus(StrEnum):
    ACTIVE = "active"
    PAST_DUE = "past_due"  # renewal failed, in dunning
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class BillingCycle(StrEnum):
    MONTHLY = "monthly"
    ANNUAL = "annual"


class DiscountType(StrEnum):
    PERCENT = "percent"
    FIXED = "fixed"
    FREE_MONTHS = "free_months"


class Subscription(BaseEntity):
    """Represents a tenant's active subscription."""

    tenant_id: str
    plan: str  # starter, pro, enterprise
    billing_cycle: BillingCycle = BillingCycle.MONTHLY
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    paymob_customer_id: str | None = None
    paymob_subscription_id: str | None = None
    payment_method_last4: str | None = None
    next_renewal_at: datetime | None = None
    started_at: datetime | None = None
    cancelled_at: datetime | None = None


class Invoice(BaseEntity):
    """A billing invoice for a subscription period."""

    tenant_id: str
    period_start: datetime
    period_end: datetime
    amount_cents: int
    currency: str = "EGP"
    status: str = "draft"  # draft, paid, failed, refunded, void
    paymob_transaction_id: str | None = None
    discount_code_id: str | None = None
    discount_amount_cents: int = 0
    paid_at: datetime | None = None
    sent_at: datetime | None = None


class DiscountCode(BaseEntity):
    """Promotional discount code."""

    code: str
    description: str
    type: DiscountType
    value: int  # percent (0-100), fixed (piasters), or months
    max_uses: int | None = None
    current_uses: int = 0
    applies_to_plans: list[str] | None = None  # None = all plans
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    stackable: bool = False
