"""Staff access policy database model (public schema).

Per-membership access policies for IP allowlists, working hours, and environment restrictions.
"""

from enum import StrEnum
from uuid import UUID as PyUUID
from uuid import uuid4

from sqlalchemy import Boolean, Enum, Index, String
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.dialects.postgresql import JSONB as PGJSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin


class AccessPolicyEnvironment(StrEnum):
    """Environment for access policy enforcement."""

    ALL = "all"
    PROD_ONLY = "prod_only"
    STAGING_ONLY = "staging_only"


class StaffAccessPolicyModel(Base, TimestampMixin):
    """Staff access policy model.

    Per-membership policies for IP allowlists, working hours, and environment.
    Used to restrict staff access based on location, time, and environment.
    """

    __tablename__ = "staff_access_policies"
    __table_args__ = (
        Index("ix_staff_access_policies_membership", "membership_id", unique=True),
        {"schema": "public"},
    )

    id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid4,
    )
    membership_id: Mapped[PyUUID] = mapped_column(
        UUID(as_uuid=True),
        nullable=False,
        unique=True,
        index=True,
    )
    ip_allowlist: Mapped[list[str] | None] = mapped_column(
        ARRAY(String(50)), nullable=True
    )
    working_hours: Mapped[dict | None] = mapped_column(PGJSONB, nullable=True)
    environment: Mapped[AccessPolicyEnvironment] = mapped_column(
        Enum(AccessPolicyEnvironment, name="accesspolicyenv", schema="public"),
        default=AccessPolicyEnvironment.ALL,
        nullable=False,
    )
    enforce_2fa: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    def __repr__(self) -> str:
        return f"<StaffAccessPolicyModel(membership_id={self.membership_id})>"
