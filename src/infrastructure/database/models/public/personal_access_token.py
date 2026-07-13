"""Personal Access Token model (public schema).

Long-lived, hashed API tokens that let machine clients (e.g. the NUMU MCP
server) authenticate to the merchant API without a browser login. A token is
minted by a store owner and is scoped to a single ``user_id`` × ``tenant_id``
pair — it inherits exactly the permissions of that user's membership, so all
existing RBAC / plan-limit checks keep applying unchanged.

Only the SHA-256 hash of the token is persisted; the raw value is shown to the
merchant once at creation time and never stored. Revocation and expiry are
enforced at authentication time (see ``PersonalAccessTokenService``).
"""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from src.infrastructure.database.connection import Base
from src.infrastructure.database.models.base import TimestampMixin, UUIDMixin


class PersonalAccessTokenModel(Base, UUIDMixin, TimestampMixin):
    """A hashed, long-lived API token scoped to a user within a tenant."""

    __tablename__ = "personal_access_tokens"
    __table_args__ = (
        Index("ix_personal_access_tokens_user_tenant", "user_id", "tenant_id"),
        {"schema": "public"},
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # The store the token was minted from — kept for display/audit only;
    # authorization is driven by (user_id, tenant_id) + per-route ownership.
    store_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("public.stores.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    # Human-friendly label chosen by the merchant ("Claude MCP", "n8n", …).
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # First chars of the raw token (e.g. "numu_pat_AbCd") so the UI can show
    # which token is which without ever revealing the secret.
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    # SHA-256 hex digest of the raw token — the only persisted form.
    token_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    # Scope strings ("catalog:read", "orders:write", … or "*"); NULL means an
    # unrestricted legacy token. Enforced centrally in the auth dependency via
    # required_scope_for()/scope_allows() — routes stay scope-unaware.
    scopes: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return (
            f"<PersonalAccessTokenModel(id={self.id}, name={self.name!r}, "
            f"user_id={self.user_id}, tenant_id={self.tenant_id})>"
        )
