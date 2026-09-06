"""Merchant lead database model (public schema — outlives the tenant).

Every person who reaches for NUMU gets a row here: the demo door, the
signup door, and eventually the sales inbox. The point of a separate
table is lifetime. Lead details used to live on ``tenants``
(``demo_name`` / ``demo_email`` / ``demo_whatsapp``), and the demo
cleanup task hard-deletes expired demo tenants every two hours — so a
merchant who tried the product, liked it and got busy for a week
vanished from our systems along with any record that they had ever
shown up.

Nothing here is foreign-keyed to ``tenants`` or ``users``. ``tenant_id``
and ``user_id`` are plain UUID columns on purpose: an FK with a cascade
would reintroduce exactly the deletion this table exists to survive,
and an FK with ``ON DELETE SET NULL`` would still lose the association.
A dangling id is the correct outcome — it records that this lead did
produce a tenant, even after that tenant is gone.

One row per email. Somebody who tries the demo on Sunday and signs up
properly on Wednesday is one lead with two touches, not two leads:
``source`` keeps the first door they came through, ``last_source`` the
most recent, and the milestone timestamps fill in as they progress.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import DateTime, Index, String, Text
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin

# Where a lead first reached us. Kept as plain strings rather than an
# enum: new acquisition surfaces (a partner form, an importer, a sales
# rep keying one in) should not need a migration to be recorded.
LEAD_SOURCE_DEMO = "demo"
LEAD_SOURCE_SIGNUP = "signup"

# Furthest point the lead has reached. Ordered, and only ever moves
# forward — see ``MerchantLeadModel.advance_status``.
LEAD_STATUS_ORDER = (
    "new",
    "demo_started",
    "registered",
    "store_created",
    "activated",
)


class MerchantLeadModel(Base, UUIDMixin, TimestampMixin):
    """A person who reached for NUMU, whether or not they became a tenant."""

    __tablename__ = "merchant_leads"
    __table_args__ = (
        Index("ix_merchant_leads_status_created", "status", "created_at"),
        Index(
            "ix_merchant_leads_qualification",
            "sells_what",
            "monthly_orders_band",
            postgresql_where=sa_text("sells_what IS NOT NULL"),
        ),
        Index("ix_merchant_leads_tenant_id", "tenant_id"),
        {"schema": "public"},
    )

    # ── Identity ──────────────────────────────────────────────────
    email: Mapped[str] = mapped_column(
        String(255), nullable=False, unique=True, index=True
    )
    name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    # E.164, normalised by the same PhoneField validator the register
    # endpoint uses, so a lead's number is dialable without cleanup.
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    language: Mapped[str | None] = mapped_column(String(5), nullable=True)
    # NULL means "same as phone" — the signup form's one-tick answer,
    # stored as absence rather than as a second boolean that could
    # disagree with the number beside it.
    whatsapp_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # ── Acquisition ───────────────────────────────────────────────
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    last_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Which pricing card they clicked. Mirrored here so the signal lives
    # in the acquisition record rather than only on a user row that may
    # be deleted with its tenant.
    plan_intent: Mapped[str | None] = mapped_column(String(20), nullable=True)

    utm_source: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_medium: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_campaign: Mapped[str | None] = mapped_column(String(120), nullable=True)
    utm_content: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # Full URLs, truncated on write rather than rejected — attribution
    # is worth more slightly clipped than dropped.
    referrer: Mapped[str | None] = mapped_column(String(500), nullable=True)
    landing_path: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # ── Qualification ─────────────────────────────────────────────
    # Answered in the onboarding wizard. ``sells_what`` mirrors the
    # wizard's niche id, which was previously applied to the store's
    # configuration and then discarded — the answer is worth keeping
    # even though the configuration it produced is already saved.
    sells_what: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sells_where_today: Mapped[str | None] = mapped_column(String(32), nullable=True)
    monthly_orders_band: Mapped[str | None] = mapped_column(String(20), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)

    # ── What the lead became ──────────────────────────────────────
    # Deliberately NOT foreign keys. See the module docstring.
    tenant_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    user_id: Mapped[PyUUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    store_subdomain: Mapped[str | None] = mapped_column(String(63), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default="new")
    demo_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    registered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    store_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_product_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    first_order_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The merchant started paying us. The north-star conversion event.
    first_commission_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # ── Sales ─────────────────────────────────────────────────────
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    def advance_status(self, candidate: str) -> None:
        """Move ``status`` forward to *candidate*, never backwards.

        A lead who has already created a store and then re-enters the
        demo modal must not be demoted to ``demo_started`` — the funnel
        position is the furthest point reached, not the latest event.
        Unknown values are ignored rather than raising: a bad status
        string is never a good enough reason to fail a signup.
        """
        try:
            here = LEAD_STATUS_ORDER.index(self.status)
            there = LEAD_STATUS_ORDER.index(candidate)
        except ValueError:
            return
        if there > here:
            self.status = candidate

    def __repr__(self) -> str:
        return (
            f"<MerchantLeadModel(id={self.id}, email={self.email}, "
            f"source={self.source}, status={self.status})>"
        )
