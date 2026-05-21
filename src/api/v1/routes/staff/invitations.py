"""Staff invitation routes."""

import logging
import secrets
from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.database import get_db
from src.api.dependencies.permissions import require_staff_invite
from src.api.dependencies.services import (
    get_email_service,
    get_password_service,
    get_token_service,
)
from src.api.dependencies.tenant import get_current_tenant
from src.api.utils.cookies import set_auth_cookies
from src.infrastructure.database.models.public import TenantModel
from src.infrastructure.database.models.public.membership_override import (
    MembershipRoleModel,
)
from src.infrastructure.database.models.public.staff_invitation import (
    StaffInvitationModel,
)
from src.infrastructure.database.models.public.staff_session import StaffSessionModel
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.external_services.password_service import PasswordService
from src.infrastructure.external_services.token_service import TokenService
from src.infrastructure.repositories.user_repository import UserRepository
from src.infrastructure.services.invitation_service import InvitationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/staff/invitations", tags=["staff"])


@router.post("")
async def invite_staff(
    membership: Annotated[TenantMembershipModel, Depends(require_staff_invite)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    email_service: Annotated[object, Depends(get_email_service)],
    email: str = Body(..., embed=True),
    role_ids: list[UUID] | None = Body(None, embed=True),
    message: str | None = Body(None, embed=True),
):
    """Invite a new staff member to the tenant."""
    import logging

    logger = logging.getLogger(__name__)

    service = InvitationService(db)
    invitation, token = await service.create_invitation(
        tenant_id=tenant.id,
        email=email,
        invited_by_id=user_id,
        role_ids=role_ids,
        message=message,
    )

    invite_url = await service.get_invitation_url(token, tenant.subdomain)

    inviter_name: str | None = None
    user_repo = UserRepository(db)
    inviter = await user_repo.get_by_id(user_id)
    if inviter:
        inviter_name = f"{inviter.first_name} {inviter.last_name}".strip() or str(
            inviter.email
        )

    email_sent = False
    try:
        email_sent = await email_service.send_staff_invitation_email(
            email=email,
            invite_url=invite_url,
            tenant_name=tenant.name,
            inviter_name=inviter_name,
            personal_message=message,
            tenant_id=tenant.id,
        )
        logger.info("staff_invitation_email_result: to=%s sent=%s", email, email_sent)
    except Exception as e:
        logger.error(
            "staff_invitation_email_failed: to=%s error=%s",
            email,
            str(e),
            exc_info=True,
        )

    return {
        "invitation_id": str(invitation.id),
        "url": invite_url,
        "expires_at": invitation.expires_at.isoformat(),
        "email_sent": email_sent,
    }


@router.get("/check")
async def check_invitation(
    token: str,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Check an invitation token without consuming it.

    Used by the accept-invitation page to determine whether the user already
    has an account (and therefore only needs to enter a password) vs. needs
    to create one, and to show the invitation target tenant/email.
    """
    service = InvitationService(db)
    is_valid, error = await service.check_invitation_valid(token)
    if not is_valid:
        raise HTTPException(status_code=400, detail=error)

    invitation = await service.invite_repo.get_by_token_hash(service._hash_token(token))
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invalid invitation token")

    user_model = (
        await db.execute(select(UserModel).where(UserModel.email == invitation.email))
    ).scalar_one_or_none()

    tenant = (
        await db.execute(
            select(TenantModel).where(TenantModel.id == invitation.tenant_id)
        )
    ).scalar_one_or_none()

    return {
        "email": invitation.email,
        "existing_user": user_model is not None,
        "tenant_name": tenant.name if tenant else None,
        "tenant_subdomain": tenant.subdomain if tenant else None,
        "expires_at": invitation.expires_at.isoformat(),
    }


@router.post("/accept")
async def accept_invitation(
    response: Response,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db)],
    password_service: Annotated[PasswordService, Depends(get_password_service)],
    token_service: Annotated[TokenService, Depends(get_token_service)],
    token: str = Body(...),
    first_name: str | None = Body(None),
    last_name: str | None = Body(None),
    password: str = Body(...),
):
    """Accept a staff invitation and log the user in.

    Flow:
    1. Validate invitation token.
    2. If the email has an existing user: verify the supplied password.
       Otherwise require first_name / last_name and create the user.
    3. Create (or reactivate) the tenant membership and assign roles.
    4. Mark the invitation accepted.
    5. Create a staff session record and issue JWT cookies so the caller
       is logged in on return.
    """
    service = InvitationService(db)

    token_preview = (
        f"{token[:6]}...{token[-4:]}" if token and len(token) > 12 else "<short>"
    )
    logger.info(
        "invite_accept_attempt token_len=%s token_preview=%s",
        len(token) if token else 0,
        token_preview,
    )

    is_valid, error = await service.check_invitation_valid(token)
    if not is_valid:
        token_hash = service._hash_token(token)
        hits = (
            await db.execute(
                select(
                    StaffInvitationModel.id,
                    StaffInvitationModel.email,
                    StaffInvitationModel.accepted_at,
                    StaffInvitationModel.revoked_at,
                    StaffInvitationModel.expires_at,
                ).where(StaffInvitationModel.token_hash == token_hash)
            )
        ).all()
        logger.error(
            "invite_accept_failed reason=%r hash_match_rows=%d", error, len(hits)
        )
        for row in hits:
            logger.error(
                "invite_accept_hash_hit id=%s email=%s accepted_at=%s "
                "revoked_at=%s expires_at=%s",
                row.id,
                row.email,
                row.accepted_at,
                row.revoked_at,
                row.expires_at,
            )
        raise HTTPException(status_code=400, detail=error)

    invitation = await service.invite_repo.get_by_token_hash(service._hash_token(token))
    if invitation is None:
        raise HTTPException(status_code=400, detail="Invalid invitation token")

    # Find or create the user.
    user_model = (
        await db.execute(select(UserModel).where(UserModel.email == invitation.email))
    ).scalar_one_or_none()

    if user_model is None:
        if not first_name or not last_name:
            raise HTTPException(
                status_code=400,
                detail="first_name and last_name are required to create a new account",
            )
        if len(password) < 6:
            raise HTTPException(
                status_code=400, detail="Password must be at least 6 characters"
            )
        user_model = UserModel(
            id=uuid4(),
            email=invitation.email,
            first_name=first_name,
            last_name=last_name,
            hashed_password=password_service.hash_password(password),
            email_verified_at=datetime.utcnow(),
        )
        db.add(user_model)
        await db.flush()
    else:
        if not password_service.verify_password(password, user_model.hashed_password):
            raise HTTPException(status_code=401, detail="Invalid email or password")

    tenant_id = invitation.tenant_id

    # Create or reactivate the membership.
    existing_membership = (
        await db.execute(
            select(TenantMembershipModel).where(
                TenantMembershipModel.user_id == user_model.id,
                TenantMembershipModel.tenant_id == tenant_id,
                TenantMembershipModel.deleted_at.is_(None),
            )
        )
    ).scalar_one_or_none()

    if existing_membership is not None:
        existing_membership.status = MembershipStatus.ACTIVE
        existing_membership.updated_at = datetime.utcnow()
        membership = existing_membership
    else:
        membership = TenantMembershipModel(
            id=uuid4(),
            user_id=user_model.id,
            tenant_id=tenant_id,
            status=MembershipStatus.ACTIVE,
        )
        db.add(membership)
        await db.flush()

    # Assign roles (idempotent — skip already-assigned ones).
    if invitation.pre_assigned_role_ids:
        existing_role_ids = set(
            (
                await db.execute(
                    select(MembershipRoleModel.role_id).where(
                        MembershipRoleModel.membership_id == membership.id
                    )
                )
            ).scalars()
        )
        for role_id in invitation.pre_assigned_role_ids:
            if role_id in existing_role_ids:
                continue
            db.add(
                MembershipRoleModel(
                    id=uuid4(),
                    membership_id=membership.id,
                    role_id=role_id,
                    assigned_by_id=invitation.invited_by_id,
                )
            )

    invitation.accepted_at = datetime.utcnow()
    invitation.updated_at = datetime.utcnow()

    # Always record a session so the user is logged in after accepting.
    client_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    db.add(
        StaffSessionModel(
            id=uuid4(),
            jti=secrets.token_hex(16),
            user_id=user_model.id,
            membership_id=membership.id,
            ip=client_ip,
            user_agent=user_agent,
            created_at=datetime.utcnow(),
        )
    )

    await db.commit()
    await db.refresh(user_model)
    await db.refresh(membership)

    # Load the domain entity once persisted so the token service gets the
    # shape it expects (Email value object, UserRole enum, etc).
    user_entity = await UserRepository(db).get_by_email_str(invitation.email)
    if user_entity is None:
        # Shouldn't happen — we just committed the row.
        logger.error(
            "invite_accept_post_commit_user_missing email=%s", invitation.email
        )
        raise HTTPException(status_code=500, detail="Failed to load user after accept")

    access_token = token_service.create_access_token(
        user_entity,
        tenant_id=tenant_id,
        membership_id=membership.id,
        perm_version=membership.permission_version or 1,
    )
    refresh_token = token_service.create_refresh_token(
        user_entity,
        tenant_id=tenant_id,
        membership_id=membership.id,
        perm_version=membership.permission_version or 1,
    )
    set_auth_cookies(response, access_token, refresh_token)

    try:
        from src.core.events.staff_events import StaffActivatedEvent
        from src.infrastructure.events.setup import get_event_bus

        get_event_bus().publish(
            StaffActivatedEvent(
                membership_id=str(membership.id),
                user_id=str(user_model.id),
                tenant_id=str(tenant_id),
                role_ids=[str(r) for r in invitation.pre_assigned_role_ids],
            )
        )
    except Exception:
        logger.exception("invite_accept_event_publish_failed")

    return {
        "status": "accepted",
        "user_id": str(user_model.id),
        "membership_id": str(membership.id),
        "tenant_id": str(tenant_id),
    }


@router.get("")
async def list_invitations(
    membership: Annotated[TenantMembershipModel, Depends(require_staff_invite)],
    db: Annotated[AsyncSession, Depends(get_db)],
    tenant: Annotated[TenantModel, Depends(get_current_tenant)],
):
    """List pending staff invitations."""
    from src.infrastructure.repositories.invitation_repository import (
        InvitationRepository,
    )

    repo = InvitationRepository(db)
    invitations = await repo.get_pending_by_tenant(tenant.id)

    return {
        "invitations": [
            {
                "id": str(inv.id),
                "email": inv.email,
                "expires_at": inv.expires_at.isoformat(),
                "created_at": inv.created_at.isoformat(),
            }
            for inv in invitations
        ]
    }


@router.delete("/{invitation_id}")
async def revoke_invitation(
    invitation_id: UUID,
    membership: Annotated[TenantMembershipModel, Depends(require_staff_invite)],
    db: Annotated[AsyncSession, Depends(get_db)],
    user_id: Annotated[UUID, Depends(get_current_user_id)],
):
    """Revoke a staff invitation."""
    from src.infrastructure.repositories.invitation_repository import (
        InvitationRepository,
    )

    repo = InvitationRepository(db)
    await repo.revoke(invitation_id)

    return {"status": "revoked"}
