"""Abandoned-cart recovery is a Pro feature that current stores keep (D5)."""

from uuid import uuid4

from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.messaging.tasks.abandoned_cart_tasks import (
    _recovery_included,
)
from src.infrastructure.tenancy.repository import TenantRepository


async def test_recovery_needs_pro_or_the_grandfather_override(test_session):
    tenant = TenantModel(
        id=uuid4(),
        name="Pixel Print",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state="active",
        owner_id=uuid4(),
    )
    store = StoreModel(
        id=uuid4(),
        tenant_id=tenant.id,
        owner_id=tenant.owner_id,
        name="Pixel Print",
        slug=f"s-{uuid4().hex[:8]}",
        subdomain=tenant.subdomain,
    )
    test_session.add_all([tenant, store])
    await test_session.commit()
    assert not await _recovery_included(test_session, store)

    test_session.add(
        EntitlementOverrideModel(
            tenant_id=tenant.id,
            feature_key="abandoned_cart",
            value=True,
            source="migration",
            reason="grandfathered 2026-09: abandoned-cart recovery was on every plan",
        )
    )
    await TenantRepository(test_session).bump_entitlements_version(tenant.id)
    await test_session.commit()
    await test_session.refresh(tenant)
    assert await _recovery_included(test_session, store)
