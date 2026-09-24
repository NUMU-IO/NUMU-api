"""A second active location is multi-warehouse (decision D4)."""

from uuid import uuid4

import pytest

from src.api.v1.routes.stores.locations import _require_multi_warehouse
from src.core.exceptions import FeatureNotAvailableError
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.location import LocationModel
from src.infrastructure.database.models.tenant.store import StoreModel


async def test_a_second_active_location_needs_multi_warehouse(test_session):
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
    first = LocationModel(tenant_id=tenant.id, store_id=store.id, name="Cairo")
    test_session.add_all([tenant, store, first])
    await test_session.commit()

    with pytest.raises(FeatureNotAvailableError) as exc:
        await _require_multi_warehouse(test_session, store.id, tenant.id)
    assert exc.value.available_via == ["pro", "enterprise"]
    # Re-saving the only active location is not a second one.
    await _require_multi_warehouse(
        test_session, store.id, tenant.id, activating=first.id
    )

    tenant.plan = "pro"
    await test_session.commit()
    await _require_multi_warehouse(test_session, store.id, tenant.id)
