"""BIS-K-16 and BIS-K-03: one rule decides whether an app is live on a store,
and the store payload's installed_apps and the hub's session-token route agree
with it in every case, including a draft app on its developer's dev store."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from src.api.v1.routes.storefront.app_public import visible_installs
from src.api.v1.routes.stores import apps as store_apps
from src.application.services import app_tokens
from src.application.services.app_install_gate import live_installs
from src.core.entities.app import AppStatus
from src.infrastructure.database.models.public.app import AppModel
from tests.unit import app_platform_rows as rows


class _Session:
    """``AsyncSessionLocal()`` stand-in that hands the route the test session."""

    def __init__(self, s):
        self.s = s

    async def __aenter__(self):
        return self.s

    async def __aexit__(self, *exc):
        return False


@pytest.fixture
def route_session(monkeypatch, test_session):
    monkeypatch.setattr(store_apps, "AsyncSessionLocal", lambda: _Session(test_session))

    async def secret(db, app_id):
        return rows.SECRET

    monkeypatch.setattr(app_tokens, "read_client_secret", secret)


async def _live(s, store_id, app_id) -> bool:
    stmt = await live_installs(s, store_id)
    return (await s.execute(stmt.where(AppModel.id == app_id))).first() is not None


async def _visible(s, store_id, app_id) -> bool:
    stmt = await visible_installs(s, store_id)
    return (await s.execute(stmt.where(AppModel.id == app_id))).first() is not None


async def _token(store_id, slug, user_id) -> bool:
    try:
        await store_apps.app_session_token(store_id, slug, user_id=user_id, locale="en")
    except HTTPException as e:
        assert e.status_code == 404
        return False
    return True


async def _case(
    s,
    *,
    plan="starter",
    status=AppStatus.PUBLISHED,
    install_status="active",
    enabled=True,
    own_store=False,
    kill=None,
):
    developer = await rows.user(s)
    merchant = developer if own_store else await rows.user(s)
    store = await rows.store(s, merchant, plan=plan)
    made = await rows.app(s, developer=developer, status=status)
    await rows.install(s, store, made.app, status=install_status, enabled=enabled)
    if kill is not None:
        await rows.kill_switch(s, on=kill)
    return store, made.app, merchant


CASES = [
    # (kwargs, live)
    ({}, True),  # published, active, enabled
    ({"install_status": "pending_auth"}, False),  # mid-consent
    ({"enabled": False}, False),
    ({"status": AppStatus.SUSPENDED}, False),
    ({"kill": False}, False),  # Partner-apps kill switch off
    ({"status": AppStatus.DRAFT, "plan": "developer", "own_store": True}, True),
    (
        {"status": AppStatus.DRAFT, "plan": "developer"},
        False,
    ),  # someone else's dev store
    (
        {"status": AppStatus.DRAFT, "own_store": True},
        False,
    ),  # developer's non-dev store
    ({"status": AppStatus.SUSPENDED, "plan": "developer", "own_store": True}, False),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("kwargs", "live"), CASES)
async def test_one_rule_and_every_caller_agrees(
    test_session, route_session, kwargs, live
):
    store, app, merchant = await _case(test_session, **kwargs)

    assert await _live(test_session, store.id, app.id) is live
    assert await _visible(test_session, store.id, app.id) is live
    assert await _token(store.id, app.slug, merchant.id) is live


@pytest.mark.asyncio
async def test_a_numu_app_stays_live_with_the_partner_kill_switch_off(test_session):
    merchant = await rows.user(test_session)
    store = await rows.store(test_session, merchant)
    made = await rows.app(test_session, developer=None)
    await rows.install(test_session, store, made.app)
    await rows.kill_switch(test_session, on=False)

    assert await _live(test_session, store.id, made.app.id)


@pytest.mark.asyncio
async def test_hub_only_numu_apps_are_live_but_never_listed_to_shoppers(test_session):
    merchant = await rows.user(test_session)
    store = await rows.store(test_session, merchant)
    made = await rows.app(test_session, slug="inbox", developer=None)
    await rows.install(test_session, store, made.app)

    assert await _live(test_session, store.id, made.app.id)
    assert not await _visible(test_session, store.id, made.app.id)


@pytest.mark.asyncio
async def test_draft_session_token_url_and_claims(test_session, route_session):
    """BIS-K-03: the draft's token is a normal session token for that store."""
    store, app, merchant = await _case(
        test_session, status=AppStatus.DRAFT, plan="developer", own_store=True
    )
    out = (
        await store_apps.app_session_token(
            store.id, app.slug, user_id=merchant.id, locale="en"
        )
    ).data

    assert out["url"].startswith("https://app.example.com/app?store_id=")
    assert out["origin"] == "https://app.example.com"
    assert "session_token=" in out["url"]
