"""The staff limit is enforced when an invitation is sent (decision D4)."""

from uuid import uuid4

import pytest

from src.core.exceptions import PlanLimitExceededError
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.public.tenant_membership import (
    MembershipStatus,
    TenantMembershipModel,
)
from src.infrastructure.services.invitation_service import InvitationService


async def test_invitations_hold_seats_and_removed_staff_free_them(test_session):
    tenant = TenantModel(
        id=uuid4(),
        name="Pixel Print",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="free",
        lifecycle_state="active",
        owner_id=uuid4(),
    )
    member = TenantMembershipModel(
        user_id=uuid4(),
        tenant_id=tenant.id,
        status=MembershipStatus.ACTIVE,
        is_owner=False,
    )
    test_session.add_all([tenant, member])
    await test_session.commit()
    invites = InvitationService(test_session, invite_secret="test")

    # Free allows one staff member besides the owner, and it is taken.
    with pytest.raises(PlanLimitExceededError):
        await invites.create_invitation(tenant.id, "a@example.com", uuid4())

    member.status = MembershipStatus.REVOKED
    await test_session.commit()
    first, _ = await invites.create_invitation(tenant.id, "a@example.com", uuid4())

    # The pending invitation now holds the seat, and re-sending it needs no
    # second one.
    with pytest.raises(PlanLimitExceededError):
        await invites.create_invitation(tenant.id, "b@example.com", uuid4())
    again, _ = await invites.create_invitation(tenant.id, "a@example.com", uuid4())
    assert again.id == first.id
