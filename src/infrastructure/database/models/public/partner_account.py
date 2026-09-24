"""Partner account: an outside developer or company building on NUMU.

One row per user (v1: one owner per partner). The gate is this row plus the
``require_approved_partner`` dependency, not a user role: roles in this
codebase are store-scoped, and a partner is not a store role.

``status`` is a plain string with a CHECK constraint rather than a Postgres
enum, so adding a state later is not an ``ALTER TYPE`` migration.

See docs/Plans/apps-developer-work/03-PLATFORM-DESIGN.md § 2.
"""

from datetime import datetime
from typing import Any
from uuid import UUID as PyUUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin

PARTNER_STATUSES = ("pending", "approved", "rejected", "suspended")
PARTNER_KINDS = ("individual", "company")
PARTNER_ROLES = ("owner", "admin", "developer")


class PartnerAccountModel(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "partner_accounts"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'suspended')",
            name="ck_partner_accounts_status",
        ),
        CheckConstraint(
            "kind IN ('individual', 'company')", name="ck_partner_accounts_kind"
        ),
        Index("ix_partner_accounts_status", "status"),
        Index("uq_partner_accounts_referral_code", "referral_code", unique=True),
        {"schema": "public"},
    )

    user_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    #: Shown on app listings.
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: For the agreement and, later, payouts.
    legal_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    country: Mapped[str] = mapped_column(String(2), nullable=False, default="EG")
    website_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    support_email: Mapped[str] = mapped_column(String(255), nullable=False)
    support_phone: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    agreement_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    agreement_accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    agreement_accepted_ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: What the partner is told on reject/suspend: {"ar": ..., "en": ...}.
    review_notes: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reviewed_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    share_bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: What a referral link carries (``numueg.app/signup?ref=``). Minted on
    #: first use; 10 characters, so it never collides with a lead's 8.
    referral_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Share of a referred merchant's plan payments, in basis points, for
    #: ``referral_months`` after that merchant's first paid invoice.
    referral_bps: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    referral_months: Mapped[int] = mapped_column(Integer, nullable=False, default=12)
    #: Public "Hire an expert" profile, opt-in by the partner.
    directory_listed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    #: logo_url, bio {ar, en}, services, languages, city.
    directory_profile: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    #: Admin-granted badge, and the admin's override to hide a listing.
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    directory_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class PartnerReferralModel(Base, UUIDMixin, TimestampMixin):
    """The partner that brought a merchant. One per tenant, first touch;
    only an admin changes it afterwards. ``first_paid_at`` starts the
    commission window."""

    __tablename__ = "partner_referrals"
    __table_args__ = (
        Index("ix_partner_referrals_partner", "partner_id"),
        {"schema": "public"},
    )

    partner_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.partner_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    first_paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class PartnerMemberModel(Base, UUIDMixin, TimestampMixin):
    """A teammate on a partner account. The account's own ``user_id`` is the
    owner and has no row here. ``user_id`` stays null until the invite is
    accepted; a user belongs to at most one partner."""

    __tablename__ = "partner_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'developer')", name="ck_partner_members_role"
        ),
        CheckConstraint(
            "status IN ('invited', 'active')", name="ck_partner_members_status"
        ),
        UniqueConstraint(
            "partner_id", "email", name="uq_partner_members_partner_email"
        ),
        Index("ix_partner_members_email", "email"),
        {"schema": "public"},
    )

    partner_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.partner_accounts.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=True,
        unique=True,
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    invited_by: Mapped[PyUUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="invited")
