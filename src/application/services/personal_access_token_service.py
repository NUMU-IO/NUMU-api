"""Personal Access Token (PAT) service.

Mints, authenticates, lists and revokes long-lived API tokens. Tokens are
returned to the merchant exactly once at creation; only their SHA-256 hash is
stored. Authentication resolves the token to its owning user so the rest of the
auth stack (membership → RBAC → plan limits) treats a PAT request identically
to a logged-in session.

Token format: ``numu_pat_<43 url-safe base64 chars>`` (256 bits of entropy).
"""

import hashlib
import secrets
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.public.personal_access_token import (
    PersonalAccessTokenModel,
)
from src.infrastructure.database.models.public.user import UserModel

logger = get_logger(__name__)

PAT_PREFIX = "numu_pat_"
_TOKEN_NBYTES = 32  # 256 bits → 43 url-safe base64 chars
_PREFIX_DISPLAY_LEN = 13  # "numu_pat_" + 4 chars, fits the model's prefix column


def generate_raw_token() -> str:
    """Return a fresh, opaque PAT string. Shown to the user once, never stored."""
    return f"{PAT_PREFIX}{secrets.token_urlsafe(_TOKEN_NBYTES)}"


def hash_token(raw: str) -> str:
    """Return the SHA-256 hex digest used as the stored, indexed lookup key."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_pat(token: str) -> bool:
    """Cheap check so the auth layer can route PATs vs JWTs without a DB hit."""
    return token.startswith(PAT_PREFIX)


class PersonalAccessTokenService:
    """Use-case service for personal access tokens, bound to a DB session."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        user_id: UUID,
        tenant_id: UUID,
        store_id: UUID | None,
        name: str,
        expires_at: datetime | None = None,
    ) -> tuple[str, PersonalAccessTokenModel]:
        """Mint a token. Returns ``(raw_token, record)`` — persist nothing else."""
        raw = generate_raw_token()
        record = PersonalAccessTokenModel(
            user_id=user_id,
            tenant_id=tenant_id,
            store_id=store_id,
            name=name.strip(),
            token_prefix=raw[:_PREFIX_DISPLAY_LEN],
            token_hash=hash_token(raw),
            expires_at=expires_at,
        )
        self._session.add(record)
        await self._session.flush()
        await self._session.refresh(record)
        logger.info(
            "pat_created",
            token_id=str(record.id),
            user_id=str(user_id),
            tenant_id=str(tenant_id),
        )
        return raw, record

    async def authenticate(
        self, raw: str
    ) -> tuple[PersonalAccessTokenModel, UserModel] | None:
        """Resolve a raw token to ``(record, user)`` or ``None`` if unusable.

        Returns ``None`` when the token is unknown, revoked, or expired so the
        caller can raise a single, non-enumerable 401.
        """
        result = await self._session.execute(
            select(PersonalAccessTokenModel).where(
                PersonalAccessTokenModel.token_hash == hash_token(raw)
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return None
        if record.revoked_at is not None:
            return None
        if record.expires_at is not None and record.expires_at <= datetime.now(UTC):
            return None

        user = (
            await self._session.execute(
                select(UserModel).where(UserModel.id == record.user_id)
            )
        ).scalar_one_or_none()
        if user is None:
            return None
        return record, user

    async def mark_used(self, record: PersonalAccessTokenModel) -> None:
        """Stamp ``last_used_at`` for audit/visibility (best-effort)."""
        record.last_used_at = datetime.now(UTC)
        await self._session.flush()

    async def list_for(
        self, *, user_id: UUID, tenant_id: UUID
    ) -> list[PersonalAccessTokenModel]:
        """List a user's tokens within a tenant, newest first (secrets excluded)."""
        result = await self._session.execute(
            select(PersonalAccessTokenModel)
            .where(
                PersonalAccessTokenModel.user_id == user_id,
                PersonalAccessTokenModel.tenant_id == tenant_id,
            )
            .order_by(PersonalAccessTokenModel.created_at.desc())
        )
        return list(result.scalars().all())

    async def revoke(self, *, token_id: UUID, user_id: UUID) -> bool:
        """Revoke a token the user owns. Returns ``False`` if not found/owned."""
        result = await self._session.execute(
            select(PersonalAccessTokenModel).where(
                PersonalAccessTokenModel.id == token_id,
                PersonalAccessTokenModel.user_id == user_id,
            )
        )
        record = result.scalar_one_or_none()
        if record is None:
            return False
        if record.revoked_at is None:
            record.revoked_at = datetime.now(UTC)
            await self._session.flush()
            logger.info("pat_revoked", token_id=str(token_id), user_id=str(user_id))
        return True
