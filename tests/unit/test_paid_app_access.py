"""Paid apps (Phase 7): access rules around the subscription.

- an app token for a paid app the store isn't paying for answers 402;
- a store plan can cap how many Partner Apps it installs (the
  ``partner_apps`` entitlement; unlimited on every plan today).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import update

from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import (
    AppInstallationModel,
    AppModel,
)
from src.infrastructure.database.models.public.entitlements import (
    PlanEntitlementModel,
)
from src.infrastructure.database.models.public.tenant import TenantModel


def test_an_unpaid_install_gets_402_not_401(monkeypatch):
    from src.api.dependencies import auth
    from src.application.services import app_billing, app_tokens
    from src.infrastructure.database import connection

    principal = SimpleNamespace(
        installation=SimpleNamespace(store_id=uuid4(), tenant_id=uuid4()),
        token=SimpleNamespace(scopes=["orders:read"]),
        app=SimpleNamespace(manifest={}),
    )

    async def resolve(_session, _raw):
        return principal

    async def not_entitled(*_a, **_k):
        return False

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(app_tokens, "resolve_app_token", resolve)
    monkeypatch.setattr(app_billing, "is_entitled", not_entitled)
    monkeypatch.setattr(connection, "AsyncSessionLocal", _Session)
    request = SimpleNamespace(
        state=SimpleNamespace(),
        url=SimpleNamespace(path="/api/v1/stores/x/orders/"),
        method="GET",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth._resolve_app_principal("numu_app_x", request))
    assert exc.value.status_code == 402
    assert exc.value.detail["code"] == "subscription_inactive"


async def _store_with_partner_apps(session, installed: int):
    tenant = TenantModel(
        id=uuid4(),
        name="Store Co",
        subdomain=f"t-{uuid4().hex[:8]}",
        plan="starter",
        lifecycle_state="active",
    )
    session.add(tenant)
    store = SimpleNamespace(id=uuid4(), tenant_id=tenant.id)
    apps = []
    for i in range(installed + 1):
        app = AppModel(
            slug=f"partner-{i}-{uuid4().hex[:4]}",
            name=f"Partner {i}",
            developer_id=uuid4(),
            status=AppStatus.PUBLISHED,
            manifest={},
        )
        session.add(app)
        apps.append(app)
    await session.flush()
    for app in apps[:installed]:
        session.add(
            AppInstallationModel(
                tenant_id=tenant.id,
                store_id=store.id,
                app_id=app.id,
                is_enabled=True,
                settings={},
                status="active",
                granted_scopes=[],
            )
        )
    await session.commit()
    return store, apps


async def _cap(session, cap):
    await session.execute(
        update(PlanEntitlementModel)
        .where(
            PlanEntitlementModel.plan_key == "starter",
            PlanEntitlementModel.feature_key == "partner_apps",
        )
        .values(value=cap)
    )
    await session.commit()


@pytest.mark.asyncio
async def test_the_plan_cap_blocks_one_more_partner_app(test_session):
    from src.api.v1.routes.app_oauth import _check_app_cap

    store, apps = await _store_with_partner_apps(test_session, installed=1)
    await _cap(test_session, 1)
    with pytest.raises(HTTPException) as exc:
        await _check_app_cap(test_session, store, apps[1])
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "plan_app_limit"
    # Re-consent on the app already installed never counts twice.
    await _check_app_cap(test_session, store, apps[0])


@pytest.mark.asyncio
async def test_no_cap_by_default(test_session):
    from src.api.v1.routes.app_oauth import _check_app_cap

    store, apps = await _store_with_partner_apps(test_session, installed=3)
    await _check_app_cap(test_session, store, apps[3])


@pytest.mark.parametrize(
    ("model", "billing_live", "allowed"),
    [
        ("recurring", False, False),  # Partner Agreement § 11.1: not live yet
        ("recurring", True, True),
        ("free", False, True),
        ("external", False, True),
    ],
)
def test_recurring_partner_apps_wait_for_the_billing_switch(
    monkeypatch, model, billing_live, allowed
):
    from src.api.v1.routes import partner_apps
    from src.application.services import partner_program

    async def switch(_db):
        return billing_live

    monkeypatch.setattr(partner_program, "partner_billing_enabled", switch)
    if allowed:
        asyncio.run(partner_apps._check_pricing(None, model))
    else:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(partner_apps._check_pricing(None, model))
        assert exc.value.status_code == 422
        assert "not live" in exc.value.detail


def test_the_billing_switch_is_off_until_turned_on():
    from src.application.services.partner_program import partner_billing_enabled

    class _Db:
        async def scalar(self, _stmt):
            return None  # no platform_config row

    assert asyncio.run(partner_billing_enabled(_Db())) is False


@pytest.mark.asyncio
async def test_the_admin_catalog_shows_each_apps_price(test_session):
    """The admin App catalog must show the current price (numu-admin #92's
    pricing dialog prefills from it)."""
    from src.api.v1.routes.admin.apps import catalog

    priced = {
        "plan": "recurring",
        "price_cents": 9900,
        "cycle": "monthly",
        "currency": "EGP",
        "locales": {"en": {"label": "EGP 99 / month"}},
    }
    app = AppModel(
        slug=f"priced-{uuid4().hex[:6]}",
        name="Priced",
        developer_id=None,
        status=AppStatus.PUBLISHED,
        manifest={"pricing": priced},
    )
    test_session.add(app)
    await test_session.commit()

    rows = (await catalog(db=test_session)).data
    row = next(r for r in rows if r.slug == app.slug)
    assert row.pricing == priced
