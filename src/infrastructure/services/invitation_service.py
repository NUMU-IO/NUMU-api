"""Invitation service for staff invitation management."""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.public.membership_override import (
    MembershipRoleModel,
)
from src.infrastructure.database.models.public.staff_invitation import (
    StaffInvitationModel,
)
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)
from src.infrastructure.repositories.invitation_repository import InvitationRepository
from src.infrastructure.repositories.membership_repository import MembershipRepository


class InvitationService:
    """Service for managing staff invitations."""

    INVITE_EXPIRY_DAYS = 7
    MAX_RESEND_COUNT = 5

    def __init__(
        self,
        session: AsyncSession,
        invite_secret: str | None = None,
    ) -> None:
        self.session = session
        self.invite_repo = InvitationRepository(session)
        self.membership_repo = MembershipRepository(session)
        if invite_secret is None:
            from src.config import settings

            invite_secret = settings.invite_secret
        self.invite_secret = invite_secret

    def _hash_token(self, token: str) -> str:
        """Hash invitation token with HMAC-SHA256."""
        return hmac.new(
            self.invite_secret.encode(),
            token.encode(),
            hashlib.sha256,
        ).hexdigest()

    def _generate_token(self) -> str:
        """Generate secure invitation token."""
        return secrets.token_urlsafe(32)

    async def create_invitation(
        self,
        tenant_id: UUID,
        email: str,
        invited_by_id: UUID,
        role_ids: list[UUID] | None = None,
        message: str | None = None,
    ) -> tuple[StaffInvitationModel, str]:
        """Create a new staff invitation.

        Returns:
            Tuple of (invitation, raw_token)
            Raw token is only returned once and must be sent to user.
        """
        existing = await self.invite_repo.get_by_email(email, tenant_id)
        if existing and existing.accepted_at is None and existing.revoked_at is None:
            if existing.resent_count >= self.MAX_RESEND_COUNT:
                raise ValueError("Maximum resend count reached")
            token = self._generate_token()
            new_hash = self._hash_token(token)
            await self.invite_repo.rotate_token(existing.id, new_hash)
            await self.session.refresh(existing)
            return existing, token

        token = self._generate_token()
        invitation = StaffInvitationModel(
            tenant_id=tenant_id,
            email=email.lower(),
            token_hash=self._hash_token(token),
            pre_assigned_role_ids=role_ids or [],
            invited_by_id=invited_by_id,
            expires_at=datetime.now(UTC) + timedelta(days=self.INVITE_EXPIRY_DAYS),
            message=message,
        )
        created = await self.invite_repo.create(invitation)
        return created, token

    async def accept_invitation(
        self,
        token: str,
        user_id: UUID,
        first_name: str | None = None,
        last_name: str | None = None,
    ) -> TenantMembershipModel:
        """Accept an invitation and create membership.

        Args:
            token: Raw invitation token
            user_id: User ID who is accepting

        Returns:
            Created TenantMembershipModel
        """
        token_hash = self._hash_token(token)
        invitation = await self.invite_repo.get_by_token_hash(token_hash)

        if not invitation:
            raise ValueError("Invalid or expired invitation")

        if invitation.accepted_at:
            raise ValueError("Invitation already accepted")

        if invitation.revoked_at:
            raise ValueError("Invitation has been revoked")

        if invitation.expires_at < datetime.now(UTC):
            raise ValueError("Invitation has expired")

        membership = TenantMembershipModel(
            user_id=user_id,
            tenant_id=invitation.tenant_id,
            status=MembershipStatus.ACTIVE,
            invited_by_id=invitation.invited_by_id,
            invited_at=invitation.invited_at or datetime.now(UTC),
            joined_at=datetime.now(UTC),
            permission_version=1,
        )
        created_membership = await self.membership_repo.create(membership)

        for role_id in invitation.pre_assigned_role_ids or []:
            mr = MembershipRoleModel(
                membership_id=created_membership.id,
                role_id=role_id,
                assigned_by_id=invitation.invited_by_id,
            )
            self.session.add(mr)

        await self.invite_repo.accept(invitation.id)
        await self.session.flush()

        return created_membership

    async def revoke_invitation(
        self,
        invitation_id: UUID,
        revoked_by_id: UUID,
    ) -> bool:
        """Revoke an invitation."""
        return await self.invite_repo.revoke(invitation_id)

    async def get_invitation_url(self, token: str, tenant_subdomain: str) -> str:
        """Get invitation acceptance URL.

        Redirects to merchant hub for staff onboarding flow.
        """
        from src.config import settings

        hub_url = settings.merchant_hub_url.rstrip("/")
        return f"{hub_url}/staff/invite/accept?token={token}&tenant={tenant_subdomain}"

    async def check_invitation_valid(
        self,
        token: str,
    ) -> tuple[bool, str]:
        """Check if invitation is valid.

        Returns:
            Tuple of (is_valid, error_message)
        """
        token_hash = self._hash_token(token)
        invitation = await self.invite_repo.get_by_token_hash(token_hash)

        if not invitation:
            return False, "Invalid invitation token"

        if invitation.accepted_at:
            return False, "Invitation already accepted"

        if invitation.revoked_at:
            return False, "Invitation has been revoked"

        if invitation.expires_at < datetime.now(UTC):
            return False, "Invitation has expired"

        return True, ""
