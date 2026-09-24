"""Partner gates. Both answer 404, so a closed door is indistinguishable
from a missing route (the same convention as ``require_feature_flag``)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_role
from src.api.dependencies.database import get_db
from src.application.services.partner_program import (
    AGREEMENT_VERSION,
    MANAGER_ROLES,
    partner_for_user,
    partner_membership,
    program_enabled,
)
from src.core.entities.user import UserRole
from src.infrastructure.database.models.public.partner_account import (
    PartnerAccountModel,
)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


async def require_partner_program(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    """404 while the Partner program is closed."""
    if not await program_enabled(db):
        raise _not_found()


@dataclass(frozen=True)
class PartnerContext:
    """Who is calling and for which partner. ``owner_id`` is the partner
    account's user, which apps and development stores are keyed by, so a
    team member acts on the partner's apps rather than their own."""

    user_id: UUID
    owner_id: UUID
    account: PartnerAccountModel | None
    role: str


async def partner_context(
    user: Annotated[tuple[UUID, str], Depends(get_current_user_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> PartnerContext:
    """The approved partner the caller owns or is an active member of.

    Independent of the program switch on purpose: theme developers were
    backfilled as approved partners and must keep uploading while the program
    is dark. Super admins pass as themselves so NUMU's own team is never
    locked out.
    """
    user_id, role = user
    found = await partner_membership(db, user_id)
    if str(role).lower() == UserRole.SUPER_ADMIN.value:
        return PartnerContext(user_id, user_id, found[0] if found else None, "owner")
    if found is None or found[0].status != "approved":
        raise _not_found()
    return PartnerContext(user_id, found[0].user_id, found[0], found[1])


async def require_approved_partner(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
) -> UUID:
    """The partner owner's user id, for the caller or a member of their team."""
    return ctx.owner_id


async def require_partner_manager(
    ctx: Annotated[PartnerContext, Depends(partner_context)],
) -> PartnerContext:
    """An owner or admin of a partner account."""
    if ctx.account is None or ctx.role not in MANAGER_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the partner's owner or an admin can do this.",
        )
    return ctx


async def require_agreed_partner(
    user_id: Annotated[UUID, Depends(require_approved_partner)],
    user: Annotated[tuple[UUID, str], Depends(get_current_user_role)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UUID:
    """An approved partner who accepted the CURRENT Partner Agreement.

    Theme developers were backfilled as approved with the placeholder version
    ``legacy-theme-developer`` so theme upload keeps working. Building Partner
    Apps and development stores is governed by the Partner Agreement, so they
    accept it first (``PATCH /partners/me``; the partner portal prompts on
    ``needs_agreement``). The same applies after AGREEMENT_VERSION is bumped.
    Super admins pass, even with a stale partner account of their own.
    """
    if str(user[1]).lower() == UserRole.SUPER_ADMIN.value:
        return user_id
    account = await partner_for_user(db, user_id)
    if account is not None and account.agreement_version != AGREEMENT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Accept the current Partner Agreement first.",
        )
    return user_id
