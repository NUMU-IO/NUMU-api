"""Commercial readiness for a tenant: registration, tax id, where money lands.

Separate from ``merchant_leads`` on purpose, and the reason is lifetime.
A lead is an acquisition record with no foreign keys, kept deliberately
after its tenant is deleted so we still know the person existed. That is
exactly the wrong lifetime for a merchant's tax id and bank details,
which should die with the tenant. This table cascades.

Nothing here gates anything today. The fields are collected in the hub
and surfaced in admin so we can see how many merchants are actually
able to take non-COD money and issue a compliant invoice; no store is
blocked from selling for want of a tax id. When a gate does arrive —
gateway KYC, or the CBE posture on pay-as-you-go — this is the record it
will read.

``is_registered_business`` is a nullable boolean because three states
matter: yes, no, and not-asked-yet. A NOT NULL default of false would
silently claim every merchant told us they are unregistered.

Payout details are encrypted with the same Fernet-backed SecretsManager
used for channel credentials. ``payout_masked`` holds a display tail so
the hub and admin can show "ending 4471" without a decrypt round-trip,
and ``payout_key_id`` records which key wrapped the row so a rotation
can find what it still needs to re-wrap.
"""

from datetime import datetime
from uuid import UUID as PyUUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, LargeBinary, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class MerchantBusinessProfileModel(Base, UUIDMixin, TimestampMixin):
    """A tenant's commercial details. One row per tenant, or none."""

    __tablename__ = "merchant_business_profiles"
    __table_args__ = (
        Index("ix_merchant_business_profiles_tenant", "tenant_id"),
        {"schema": "public"},
    )

    tenant_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )

    # NULL = never asked. Distinct from False, which is a real answer.
    is_registered_business: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tax_id: Mapped[str | None] = mapped_column(String(50), nullable=True)

    payout_bank_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payout_account_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    payout_encrypted: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    payout_key_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payout_masked: Mapped[str | None] = mapped_column(String(24), nullable=True)

    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def has_payout_account(self) -> bool:
        """True when an account number has actually been stored."""
        return self.payout_encrypted is not None

    @property
    def is_complete(self) -> bool:
        """Every part of commercial readiness answered.

        An unregistered business is a complete answer — it needs no tax
        id — so completeness turns on having answered the registration
        question, having a tax id *if* registered, and having somewhere
        to send money.
        """
        if self.is_registered_business is None:
            return False
        if self.is_registered_business and not self.tax_id:
            return False
        return self.has_payout_account

    def __repr__(self) -> str:
        return (
            f"<MerchantBusinessProfileModel(tenant_id={self.tenant_id}, "
            f"registered={self.is_registered_business}, "
            f"complete={self.is_complete})>"
        )
