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

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin

PARTNER_STATUSES = ("pending", "approved", "rejected", "suspended")
PARTNER_KINDS = ("individual", "company")


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
