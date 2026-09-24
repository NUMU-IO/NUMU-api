"""Paid apps (apps plan, Phase 7): subscriptions and the partner ledger.

Public schema with explicit keys, like the wallet and billing tables: an app
charge is money between NUMU and a merchant (charged from their wallet), and
a partner's share is money NUMU owes a partner.

* ``app_subscriptions``: one row per paid installation. The price is a
  snapshot taken when the merchant subscribes, so a later price change never
  reaches an existing subscriber silently. Deleted with the installation.
* ``partner_ledger_entries``: append-only. The balance a partner is owed is
  ``SUM(amount_cents)``; there is no stored balance to drift from it.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class AppSubscriptionModel(Base, UUIDMixin, TimestampMixin):
    """A store's paid subscription to one app."""

    __tablename__ = "app_subscriptions"
    __table_args__ = (
        UniqueConstraint("installation_id", name="uq_app_subscriptions_installation"),
        Index("ix_app_subscriptions_period_end", "status", "current_period_end"),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    app_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: active | past_due (a renewal could not be charged) | cancelled
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    price_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    #: monthly | annual
    cycle: Mapped[str] = mapped_column(String(10), nullable=False)
    current_period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    current_period_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    #: The merchant cancelled: access runs to the period end, then no renewal.
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    #: The current period is the free trial: nothing was charged for it.
    is_trial: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    #: Usage pricing the merchant approved when subscribing (snapshot).
    usage_cap_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage_unit_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    coupon_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_coupons.id", ondelete="SET NULL"),
        nullable=True,
    )
    coupon_cycles_left: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vat_grandfathered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class AppTrialModel(Base):
    """A store used its free trial of an app. Survives uninstalling, so a
    reinstall does not start a second trial."""

    __tablename__ = "app_trials"
    __table_args__ = ({"schema": "public"},)

    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    app_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppUsageRecordModel(Base, UUIDMixin):
    """One metered charge an app reported, already taken from the wallet."""

    __tablename__ = "app_usage_records"
    __table_args__ = (
        UniqueConstraint(
            "installation_id", "idempotency_key", name="uq_app_usage_idempotency"
        ),
        Index("ix_app_usage_sub_period", "subscription_id", "period_start"),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    app_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    installation_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_installations.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    period_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PartnerLedgerEntryModel(Base, UUIDMixin):
    """One movement of money NUMU owes a partner. Never updated or deleted."""

    __tablename__ = "partner_ledger_entries"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_partner_ledger_idempotency"),
        Index("ix_partner_ledger_partner_created", "partner_id", "created_at"),
        {"schema": "public"},
    )

    partner_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.partner_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )
    #: sale (+ the partner's share) | payout (-) | adjustment (+/-)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: sale only: what the merchant paid, and NUMU's fee out of it.
    gross_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platform_fee_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    share_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    discount_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    vat_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    theme_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="SET NULL"),
        nullable=True,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    app_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="SET NULL"),
        nullable=True,
    )
    subscription_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    #: payout: the bank transfer reference.
    reference: Mapped[str | None] = mapped_column(String(128), nullable=True)
    actor_user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppCouponModel(Base, UUIDMixin, TimestampMixin):
    """A partner's discount code for one of their apps. The partner funds it:
    it comes out of the partner's share, never NUMU's fee."""

    __tablename__ = "app_coupons"
    __table_args__ = (
        UniqueConstraint("app_id", "code", name="uq_app_coupons_app_code"),
        CheckConstraint(
            "(percent_off IS NULL) <> (amount_off_cents IS NULL)",
            name="ck_app_coupons_one_discount",
        ),
        {"schema": "public"},
    )

    partner_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.partner_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    app_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="CASCADE"),
        nullable=False,
    )
    code: Mapped[str] = mapped_column(String(40), nullable=False)
    percent_off: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_off_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_cycles: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_redemptions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    store_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class AppCouponRedemptionModel(Base, UUIDMixin):
    """A store used a coupon. One per store and coupon."""

    __tablename__ = "app_coupon_redemptions"
    __table_args__ = (
        UniqueConstraint("coupon_id", "store_id", name="uq_app_coupon_redemption"),
        {"schema": "public"},
    )

    coupon_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_coupons.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[PyUUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_subscriptions.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AppFeeInvoiceModel(Base, UUIDMixin):
    """NUMU's numbered invoice to a merchant for NUMU's fee on one app charge
    and the VAT on it, or the credit note that reverses one."""

    __tablename__ = "app_fee_invoices"
    __table_args__ = (
        UniqueConstraint("number", name="uq_app_fee_invoices_number"),
        UniqueConstraint(
            "wallet_transaction_id", "kind", name="uq_app_fee_invoices_tx_kind"
        ),
        Index("ix_app_fee_invoices_tenant_created", "tenant_id", "created_at"),
        {"schema": "public"},
    )

    number: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
    )
    store_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    app_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.apps.id", ondelete="SET NULL"),
        nullable=True,
    )
    wallet_transaction_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.wallet_transactions.id", ondelete="CASCADE"),
        nullable=False,
    )
    theme_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.marketplace_themes.id", ondelete="SET NULL"),
        nullable=True,
    )
    original_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.app_fee_invoices.id", ondelete="SET NULL"),
        nullable=True,
    )
    list_price_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    discount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    fee_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vat_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    vat_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    share_bps: Mapped[int] = mapped_column(Integer, nullable=False)
    total_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="EGP")
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
