"""Partner gates. Both answer 404, so a closed door is indistinguishable
from a missing route (the same convention as ``require_feature_flag``)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_role
from src.api.dependencies.database import get_db
from src.application.services.partner_program import (
    AGREEMENT_VERSION,
    partner_for_user,
    program_enabled,
)
from src.core.entities.user import UserRole


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


async def require_partner_program(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """404 while the Partner program is closed."""
    if not await program_enabled(db):
        raise _not_found()


async def require_approved_partner(
    user: Annotated[tuple[UUID, str], Depends(get_current_user_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UUID:
    """The caller's user id, if they are an approved partner (or a super admin).

    Independent of the program switch on purpose: theme developers were
    backfilled as approved partners and must keep uploading while the program
    is dark. Super admins pass so NUMU's own team is never locked out.
    """
    user_id, role = user
    if str(role).lower() == UserRole.SUPER_ADMIN.value:
        return user_id
    account = await partner_for_user(db, user_id)
    if account is None or account.status != "approved":
        raise _not_found()
    return user_id


async def require_agreed_partner(
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UUID:
    """An approved partner who accepted the CURRENT Partner Agreement.

    Theme developers were backfilled as approved with the placeholder version
    ``legacy-theme-developer`` so theme upload keeps working. Building Partner
    Apps and development stores is governed by the Partner Agreement, so they
    accept it first (``PATCH /partners/me``; the partner portal prompts on
    ``needs_agreement``). The same applies after AGREEMENT_VERSION is bumped.
    """
    account = await partner_for_user(db, user_id)
    if account is not None and account.agreement_version != AGREEMENT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accept the current Partner Agreement first.",
        )
    return user_id
