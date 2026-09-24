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
    #: sale (+ the partner's share) | referral (+) | payout (-) | adjustment (+/-)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: sale only: what the merchant paid, and NUMU's fee out of it.
    gross_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    platform_fee_cents: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
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
    #: referral only: the referred merchant whose plan payment earned it.
    tenant_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="SET NULL"),
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
