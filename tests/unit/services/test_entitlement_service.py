"""EntitlementService and its admin routes against the seeded catalog.

The rules themselves are covered in tests/unit/test_entitlements.py; this is
the glue: the cache stamp, invalidation, quotas, flags and the admin writes.
Postgres-only statements (ON CONFLICT upserts, the usage counter) were run
against real Postgres when the service was built and are not repeated here.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.v1.routes.admin.entitlements import (
    KillSwitchIn,
    OverrideIn,
    RevokeIn,
    create_override,
    explain_feature,
    revoke_override,
    set_kill_switch,
    tenant_entitlements,
)
from src.application.services.api_access import decide as decide_api_access
from src.application.services.entitlement_service import EntitlementService
from src.core.exceptions import (
    FeatureDisabledError,
    FeatureNotAvailableError,
    PlanLimitExceededError,
)
from src.infrastructure.database.models.audit import AuditLogModel
from src.infrastructure.database.models.public.entitlements import (
    EntitlementOverrideModel,
    FeatureFlagModel,
    FeatureFlagTargetModel,
    FeatureModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.tenancy.repository import TenantRepository

ADMIN = uuid4()
LATER = datetime.now(UTC) + timedelta(days=30)


async def _tenant(session, plan="starter"):
    tenant = TenantModel(
        id=uuid4(),
        name="Pixel Print",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan=plan,
        lifecycle_state="active",
        owner_id=uuid4(),
    )
    session.add(tenant)
    await session.commit()
    return tenant


async def _override(session, tenant, feature, value, **extra):
    session.add(
        EntitlementOverrideModel(
            tenant_id=tenant.id,
            feature_key=feature,
            value=value,
            source=extra.pop("source", "support"),
            reason=extra.pop("reason", "ticket 4412"),
            **extra,
        )
    )
    await TenantRepository(session).bump_entitlements_version(tenant.id)
    await session.commit()
    await session.refresh(tenant)


async def test_starter_gets_unlimited_products_from_the_plan(test_session):
    tenant = await _tenant(test_session)
    state = await EntitlementService(test_session).feature(tenant, "products")
    assert (state["value"], state["source"], state["in_plan"]) == (
        "unlimited",
        "plan",
        True,
    )


async def test_a_change_is_live_after_its_bump_and_not_before(test_session):
    tenant = await _tenant(test_session)
    assert not await EntitlementService(test_session).has(tenant, "api_access")

    test_session.add(
        EntitlementOverrideModel(
            tenant_id=tenant.id,
            feature_key="api_access",
            value=True,
            source="contract",
            reason="pilot",
        )
    )
    await test_session.commit()
    # No bump: the cached snapshot still carries a matching stamp.
    assert not await EntitlementService(test_session).has(tenant, "api_access")

    await TenantRepository(test_session).bump_entitlements_version(tenant.id)
    await test_session.commit()
    await test_session.refresh(tenant)
    assert await EntitlementService(test_session).has(tenant, "api_access")


async def test_a_plan_change_needs_no_bump(test_session):
    tenant = await _tenant(test_session)
    assert not await EntitlementService(test_session).has(tenant, "api_access")
    tenant.plan = "pro"
    await test_session.commit()
    assert await EntitlementService(test_session).has(tenant, "api_access")


async def test_not_in_plan_offers_the_cheapest_plan_first(test_session):
    tenant = await _tenant(test_session)
    with pytest.raises(FeatureNotAvailableError) as exc:
        await EntitlementService(test_session).require(tenant, "api_access")
    assert exc.value.available_via == ["pro", "enterprise"]
    assert exc.value.upgrade_required is True


async def test_a_limit_upsell_only_offers_more_than_the_merchant_has(test_session):
    ents = EntitlementService(test_session)
    staff = await ents.available_via("staff_accounts", beyond=3, exclude="starter")
    assert staff == ["pro", "enterprise"]
    products = await ents.available_via("products", beyond=100, exclude="payg")
    assert products == ["starter", "pro", "enterprise"]
    assert await ents.available_via("products", beyond="unlimited") == []


async def test_the_kill_switch_refuses_but_keeps_the_answer(test_session):
    tenant = await _tenant(test_session, plan="pro")
    feature = await test_session.get(FeatureModel, "api_access")
    feature.is_enabled = False
    await test_session.commit()
    await EntitlementService.bump_catalog()

    ents = EntitlementService(test_session)
    with pytest.raises(FeatureDisabledError):
        await ents.require(tenant, "api_access")
    state = await ents.feature(tenant, "api_access")
    assert (state["value"], state["reason"]) == (True, "disabled_globally")


async def test_an_unknown_feature_fails_closed(test_session):
    tenant = await _tenant(test_session)
    assert await EntitlementService(test_session).has(tenant, "teleport") is False


async def test_a_quota_counts_the_real_rows(test_session):
    tenant = await _tenant(test_session)
    test_session.add(
        StoreModel(
            id=uuid4(),
            tenant_id=tenant.id,
            owner_id=tenant.owner_id,
            name="Pixel Print",
            slug=f"s-{uuid4().hex[:8]}",
            subdomain=tenant.subdomain,
        )
    )
    await test_session.commit()
    await _override(test_session, tenant, "stores", 1, expires_at=LATER)

    with pytest.raises(PlanLimitExceededError) as exc:
        await EntitlementService(test_session).check_quota(tenant, "stores")
    assert (exc.value.limit, exc.value.current, exc.value.feature) == (1, 1, "stores")


async def test_a_flag_target_holds_one_tenant_back(test_session):
    tenant = await _tenant(test_session)
    test_session.add(
        FeatureFlagModel(
            key="checkout_v2",
            description="new checkout",
            enabled=True,
            rollout_percent=100,
        )
    )
    await test_session.commit()
    await EntitlementService.bump_catalog()
    assert await EntitlementService(test_session).flag(tenant, "checkout_v2")

    test_session.add(
        FeatureFlagTargetModel(
            flag_key="checkout_v2", tenant_id=tenant.id, enabled=False, reason="bug 88"
        )
    )
    await TenantRepository(test_session).bump_entitlements_version(tenant.id)
    await test_session.commit()
    await test_session.refresh(tenant)
    assert not await EntitlementService(test_session).flag(tenant, "checkout_v2")
    flags = await EntitlementService(test_session).explain_flags(tenant)
    assert [(f["key"], f["on"], f["why"]) for f in flags] == [
        ("checkout_v2", False, "targeted")
    ]


async def test_an_api_grant_is_an_override_not_the_plan(test_session):
    tenant = await _tenant(test_session)
    await _override(test_session, tenant, "api_access", True, source="contract")
    access = await decide_api_access(test_session, tenant)
    assert (access.allowed, access.source, access.in_plan, access.granted) == (
        True,
        "grant",
        False,
        True,
    )


# ─── Admin routes ─────────────────────────────────────────────────────────


async def test_admin_override_supersedes_then_revokes(test_session):
    tenant = await _tenant(test_session)
    version = tenant.entitlements_version
    body = OverrideIn(
        feature_key="multi_warehouse",
        value=True,
        source="beta",
        reason="early access #88",
        expires_at=LATER,
    )
    first = (await create_override(tenant.id, body, ADMIN, test_session)).data
    assert first["explain"]["source"] == "override"
    second = (await create_override(tenant.id, body, ADMIN, test_session)).data
    await test_session.commit()

    rows = (
        await test_session.scalars(
            select(EntitlementOverrideModel).where(
                EntitlementOverrideModel.tenant_id == tenant.id
            )
        )
    ).all()
    live = [r for r in rows if r.revoked_at is None]
    assert len(rows) == 2 and [str(r.id) for r in live] == [second["id"]]

    await revoke_override(live[0].id, RevokeIn(reason="beta over"), ADMIN, test_session)
    await test_session.commit()
    await test_session.refresh(tenant)
    assert tenant.entitlements_version == version + 3
    assert not await EntitlementService(test_session).has(tenant, "multi_warehouse")


async def test_admin_override_rules(test_session):
    tenant = await _tenant(test_session)
    forever = OverrideIn(
        feature_key="api_access", value=True, source="support", reason="agency pilot"
    )
    with pytest.raises(HTTPException) as exc:
        await create_override(tenant.id, forever, ADMIN, test_session)
    assert exc.value.status_code == 422

    wrong_kind = OverrideIn(
        feature_key="products",
        value=True,
        source="contract",
        reason="enterprise deal",
    )
    with pytest.raises(HTTPException) as exc:
        await create_override(tenant.id, wrong_kind, ADMIN, test_session)
    assert exc.value.status_code == 422

    contract = OverrideIn(
        feature_key="api_access", value=True, source="contract", reason="agency deal"
    )
    assert (await create_override(tenant.id, contract, ADMIN, test_session)).data[
        "status"
    ] == "live"


async def test_explain_shows_the_override_winning_over_the_plan(test_session):
    tenant = await _tenant(test_session)
    await _override(test_session, tenant, "api_access", True, source="contract")
    explained = (
        await explain_feature(tenant.id, "api_access", ADMIN, test_session)
    ).data
    layers = {layer["layer"]: layer for layer in explained["layers"]}
    assert layers["override"]["row"]["live"] is True
    assert layers["plan"]["shadowed"] is True and layers["plan"]["value"] is False

    detail = (await tenant_entitlements(tenant.id, ADMIN, test_session)).data
    api = next(f for f in detail["features"] if f["key"] == "api_access")
    assert (api["available"], api["source"]) == (True, "override")


async def test_the_kill_switch_route_is_audited(test_session):
    body = KillSwitchIn(enabled=False, reason="incident 42")
    feature = (await set_kill_switch("discount_codes", body, ADMIN, test_session)).data
    assert (feature["is_enabled"], feature["disabled_reason"]) == (False, "incident 42")
    entry = await test_session.scalar(
        select(AuditLogModel).where(
            AuditLogModel.event_type == "entitlement.feature.kill_switch"
        )
    )
    assert entry.user_id == ADMIN and entry.details["reason"] == "incident 42"
