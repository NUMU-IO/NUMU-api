"""Admin user management — list and invite platform admins.

URL: /api/v1/admin/users
Requires SUPER_ADMIN role.

"Invite" creates a SUPER_ADMIN user with a random temporary password and
emails it to them (best-effort; if Resend isn't configured the password
comes back in the response so the inviting admin can relay it manually).
"""

from __future__ import annotations

import logging
import secrets
import string
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import require_admin
from src.api.dependencies.database import get_db
from src.api.dependencies.repositories import get_user_repository
from src.api.dependencies.services import get_email_service, get_password_service
from src.api.responses import SuccessResponse
from src.config import settings
from src.core.entities.user import User, UserRole, UserStatus
from src.core.interfaces.services.email_service import (
    EmailMessage,
    IEmailService,
)
from src.core.interfaces.services.password_service import IPasswordService
from src.core.value_objects.email import Email as EmailVO
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.repositories.user_repository import UserRepository

logger = logging.getLogger(__name__)

router = APIRouter()


class AdminUserItem(BaseModel):
    id: str
    email: str
    first_name: str
    last_name: str
    status: str
    created_at: str | None
    last_login_at: str | None


class InviteAdminRequest(BaseModel):
    email: EmailStr
    first_name: str
    last_name: str = ""


class InviteAdminResponse(BaseModel):
    user: AdminUserItem
    email_sent: bool
    # Temporary password — only ever populated when the email couldn't be sent
    # (dev mode, missing Resend key, etc.). Never expose in production unless
    # you want to own the distribution.
    temporary_password: str | None = None


def _generate_password(length: int = 16) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#%*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _to_item(u: UserModel) -> AdminUserItem:
    return AdminUserItem(
        id=str(u.id),
        email=u.email if isinstance(u.email, str) else str(u.email),
        first_name=u.first_name,
        last_name=u.last_name,
        status=u.status.value if hasattr(u.status, "value") else str(u.status),
        created_at=u.created_at.isoformat() if u.created_at else None,
        last_login_at=u.last_login_at.isoformat() if u.last_login_at else None,
    )


@router.get(
    "",
    response_model=SuccessResponse[list[AdminUserItem]],
    summary="List platform admins",
)
async def list_admins(
    db: Annotated[AsyncSession, Depends(get_db)],
    _admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[list[AdminUserItem]]:
    result = await db.execute(
        select(UserModel)
        .where(UserModel.role == UserRole.SUPER_ADMIN)
        .order_by(UserModel.created_at.desc())
    )
    rows = result.scalars().all()
    return SuccessResponse(data=[_to_item(r) for r in rows])


@router.post(
    "/invite",
    response_model=SuccessResponse[InviteAdminResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Invite a new platform admin",
)
async def invite_admin(
    payload: InviteAdminRequest,
    user_repo: Annotated[UserRepository, Depends(get_user_repository)],
    password_service: Annotated[IPasswordService, Depends(get_password_service)],
    email_service: Annotated[IEmailService, Depends(get_email_service)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[InviteAdminResponse]:
    email_vo = EmailVO(value=payload.email)

    # If the email already belongs to a user, two cases:
    #   1. They're already a SUPER_ADMIN → nothing to do, 409.
    #   2. They're a merchant/customer/other → promote them in place.
    #      Their existing password stays, so they log in as usual and then
    #      see the admin panel. We issue no temp password and send a
    #      "you've been granted admin access" email instead.
    existing = await user_repo.get_by_email(email_vo)
    if existing is not None:
        if existing.role == UserRole.SUPER_ADMIN:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="This user is already a platform admin.",
            )
        existing.role = UserRole.SUPER_ADMIN
        existing.status = UserStatus.ACTIVE
        if existing.email_verified_at is None:
            existing.email_verified_at = datetime.now(UTC)
        created = await user_repo.update(existing)

        logger.warning(
            "admin_user_promoted by=%s target=%s email=%s prev_role=%s",
            admin_id,
            created.id,
            payload.email,
            existing.role,
        )

        # Best-effort "you were granted admin access" email (no temp password).
        email_sent = False
        try:
            sign_in_url = settings.merchant_hub_url.rstrip("/")
            msg = EmailMessage(
                to=payload.email,
                subject="You now have admin access on NUMU",
                html_content=(
                    f"<p>Hi {existing.first_name or payload.first_name},</p>"
                    "<p>Your NUMU account has been granted platform admin "
                    "privileges. Sign in with your existing password and you'll "
                    "see the admin panel.</p>"
                    f'<p><a href="{sign_in_url}">{sign_in_url}</a></p>'
                ),
                text_content=(
                    f"Hi {existing.first_name or payload.first_name},\n\n"
                    "Your NUMU account has been granted platform admin "
                    "privileges. Sign in with your existing password:\n"
                    f"{sign_in_url}\n"
                ),
            )
            email_sent = await email_service.send_email(msg)
        except Exception as exc:
            logger.warning("admin_promote_email_failed error=%s", exc)

        created_item = AdminUserItem(
            id=str(created.id),
            email=payload.email,
            first_name=created.first_name,
            last_name=created.last_name,
            status=created.status.value
            if hasattr(created.status, "value")
            else str(created.status),
            created_at=created.created_at.isoformat()
            if getattr(created, "created_at", None)
            else None,
            last_login_at=created.last_login_at.isoformat()
            if getattr(created, "last_login_at", None)
            else None,
        )
        return SuccessResponse(
            data=InviteAdminResponse(
                user=created_item,
                email_sent=email_sent,
                temporary_password=None,
            ),
            message="Existing user promoted to admin — they keep their current password."
            if email_sent
            else "Existing user promoted to admin — email could not be sent; tell them to sign in with their existing password.",
        )

    # No existing user — create a fresh admin with a temp password.
    temp_password = _generate_password()
    hashed = password_service.hash_password(temp_password)

    new_admin = User(
        email=email_vo,
        hashed_password=hashed,
        first_name=payload.first_name.strip() or "Admin",
        last_name=payload.last_name.strip(),
        role=UserRole.SUPER_ADMIN,
        status=UserStatus.ACTIVE,
        email_verified_at=datetime.now(UTC),
    )
    created = await user_repo.create(new_admin)

    logger.warning(
        "admin_user_invited by=%s new_admin=%s email=%s",
        admin_id,
        created.id,
        payload.email,
    )

    # Best-effort welcome email with the temporary password.
    email_sent = False
    try:
        sign_in_url = settings.merchant_hub_url.rstrip("/")
        msg = EmailMessage(
            to=payload.email,
            subject="You're now a NUMU platform admin",
            html_content=(
                f"<p>Hi {payload.first_name},</p>"
                f"<p>{admin_id} has added you as a platform admin on NUMU.</p>"
                "<p>Sign in with this temporary password and change it from your "
                "profile right away:</p>"
                f"<p><strong>{temp_password}</strong></p>"
                f'<p><a href="{sign_in_url}">{sign_in_url}</a></p>'
            ),
            text_content=(
                f"Hi {payload.first_name},\n\n"
                f"You were added as a platform admin on NUMU.\n"
                f"Temporary password: {temp_password}\n"
                f"Sign in at: {sign_in_url}\n"
                "Please change your password after your first sign-in."
            ),
        )
        email_sent = await email_service.send_email(msg)
    except Exception as exc:
        logger.warning("admin_invite_email_failed error=%s", exc)
        email_sent = False

    created_item = AdminUserItem(
        id=str(created.id),
        email=payload.email,
        first_name=created.first_name,
        last_name=created.last_name,
        status=created.status.value
        if hasattr(created.status, "value")
        else str(created.status),
        created_at=created.created_at.isoformat()
        if getattr(created, "created_at", None)
        else None,
        last_login_at=None,
    )
    return SuccessResponse(
        data=InviteAdminResponse(
            user=created_item,
            email_sent=email_sent,
            temporary_password=None if email_sent else temp_password,
        ),
        message="Admin invited"
        if email_sent
        else "Admin created — email could not be sent; share the temporary password yourself.",
    )


@router.delete(
    "/{user_id}",
    response_model=SuccessResponse[None],
    summary="Revoke platform admin access",
)
async def revoke_admin(
    user_id: Annotated[UUID, Path()],
    db: Annotated[AsyncSession, Depends(get_db)],
    admin_id: Annotated[UUID, Depends(require_admin)],
) -> SuccessResponse[None]:
    if user_id == admin_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You can't revoke your own admin access.",
        )

    result = await db.execute(select(UserModel).where(UserModel.id == user_id))
    target = result.scalar_one_or_none()
    if not target or target.role != UserRole.SUPER_ADMIN:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Admin not found"
        )

    # Soft revoke: mark inactive rather than deleting — preserves audit trail
    # and downstream FK references.
    target.status = UserStatus.INACTIVE
    target.role = UserRole.STORE_OWNER
    await db.commit()

    logger.warning("admin_user_revoked by=%s target=%s", admin_id, user_id)
    return SuccessResponse(data=None, message="Admin access revoked")
