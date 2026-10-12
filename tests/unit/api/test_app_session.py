"""BIS-K-01 and BIS-C-08: ``require_app_session`` accepts the hub's session
token for its own app on a live install, and nothing else."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from src.api.dependencies import app_session as module
from src.api.dependencies.app_session import require_app_session
from src.api.v1.routes.stores import apps as store_apps
from src.application.services import app_tokens
from src.application.services.app_tokens import session_token
from src.core.entities.app import AppStatus
from tests.unit import app_platform_rows as rows
from tests.unit.test_app_install_gate import _Session


def _request(token: str | None) -> Request:
    headers = [(b"authorization", f"Bearer {token}".encode())] if token else []
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers})


@pytest.fixture(autouse=True)
def _secret_and_rls(monkeypatch):
    async def secret(db, app_id):
        return rows.SECRET

    monkeypatch.setattr(module, "read_client_secret", secret)
    monkeypatch.setattr(module, "apply_rls_tenant", AsyncMock())


async def _setup(s, *, slug="bis-test", **install):
    merchant = await rows.user(s)
    store = await rows.store(s, merchant)
    made = await rows.app(s, slug=slug)
    await rows.install(s, store, made.app, **install)
    return merchant, store, made


def _mint(made, merchant, store, *, secret=rows.SECRET, age_seconds=0, **over):
    if age_seconds:
        then = datetime.now(UTC) - timedelta(seconds=age_seconds)
        real = app_tokens.datetime
        app_tokens.datetime = type("D", (), {"now": staticmethod(lambda tz=None: then)})
        try:
            return session_token(
                secret,
                client_id=made.client_id,
                user_id=merchant.id,
                store_id=store.id,
                locale="en",
            )
        finally:
            app_tokens.datetime = real
    claims = {
        "iss": "numueg.app",
        "aud": made.client_id,
        "sub": str(merchant.id),
        "dest": str(store.id),
        "iat": datetime.now(UTC),
        "nbf": datetime.now(UTC),
        "exp": datetime.now(UTC) + timedelta(seconds=60),
        "locale": "en",
    }
    claims.update(over)
    if claims.get("nbf") == "in 120 s":
        # At call time: a long run must not age it into validity.
        claims["nbf"] = datetime.now(UTC) + timedelta(seconds=120)
    return jwt.encode(
        {k: v for k, v in claims.items() if v is not None}, secret, algorithm="HS256"
    )


async def _call(s, token, slug="bis-test"):
    return await require_app_session(slug)(_request(token), db=s)


async def _refused(s, token, slug="bis-test", code=401):
    with pytest.raises(HTTPException) as e:
        await _call(s, token, slug)
    assert e.value.status_code == code


@pytest.mark.asyncio
async def test_a_valid_token_names_the_store_and_the_user(test_session):
    merchant, store, made = await _setup(test_session)

    got = await _call(test_session, _mint(made, merchant, store))

    assert (got.store_id, got.user_id, got.app_id, got.locale) == (
        store.id,
        merchant.id,
        made.app.id,
        "en",
    )
    module.apply_rls_tenant.assert_awaited_once()


@pytest.mark.asyncio
async def test_clock_skew_25s_is_accepted_and_35s_is_not(test_session):
    merchant, store, made = await _setup(test_session)

    # The token lives 60 s: minted 85 s ago it expired 25 s ago.
    assert await _call(test_session, _mint(made, merchant, store, age_seconds=85))
    await _refused(test_session, _mint(made, merchant, store, age_seconds=95))


@pytest.mark.asyncio
async def test_a_user_who_does_not_own_the_store_is_refused(test_session):
    _merchant, store, made = await _setup(test_session)
    stranger = await rows.user(test_session)

    await _refused(test_session, _mint(made, stranger, store), code=403)


@pytest.mark.asyncio
@pytest.mark.parametrize("install", [{"enabled": False}, {"status": "pending_auth"}])
async def test_an_install_that_is_not_live_is_refused(test_session, install):
    merchant, store, made = await _setup(test_session, **install)

    await _refused(test_session, _mint(made, merchant, store), code=403)


@pytest.mark.asyncio
async def test_another_apps_token_is_refused(test_session):
    merchant, store, made = await _setup(test_session, slug="other-app")

    await _refused(test_session, _mint(made, merchant, store), slug="bis-test")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"secret": "n" * 50},  # not the app's secret
        {"iss": "evil.example"},
        {"dest": None},  # no store in it
        {"nbf": "in 120 s"},  # not yet valid, beyond the 30 s leeway
    ],
)
async def test_tampered_or_incomplete_tokens_are_refused(test_session, bad):
    merchant, store, made = await _setup(test_session)

    await _refused(test_session, _mint(made, merchant, store, **bad))


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [None, "numu_app_" + "x" * 40, "not-a-jwt"])
async def test_no_token_an_app_token_or_garbage_is_refused(test_session, token):
    await _setup(test_session)

    await _refused(test_session, token)


@pytest.mark.asyncio
async def test_a_merchant_style_jwt_is_refused(test_session):
    """A token whose audience is not an app's client id never reaches a secret."""
    merchant, _store, _made = await _setup(test_session)
    other = jwt.encode({"sub": str(merchant.id), "aud": "numu-merchant"}, "k" * 40)

    await _refused(test_session, other)


@pytest.mark.asyncio
async def test_round_trip_from_the_hubs_session_token_route(test_session, monkeypatch):
    """BIS-C-08: what the hub's route mints, the dependency accepts. Also a
    draft app on its developer's dev store (BIS-K-03)."""
    monkeypatch.setattr(store_apps, "AsyncSessionLocal", lambda: _Session(test_session))

    async def secret(db, app_id):
        return rows.SECRET

    monkeypatch.setattr(app_tokens, "read_client_secret", secret)
    developer = await rows.user(test_session)
    store = await rows.store(test_session, developer, plan="developer")
    made = await rows.app(
        test_session, slug="bis-test", developer=developer, status=AppStatus.DRAFT
    )
    await rows.install(test_session, store, made.app)

    out = await store_apps.app_session_token(
        store.id, "bis-test", user_id=developer.id, locale="ar"
    )
    got = await _call(test_session, out.data["token"])

    assert (got.store_id, got.user_id, got.locale) == (store.id, developer.id, "ar")


@pytest.mark.asyncio
async def test_a_session_token_is_refused_on_core_routes(client, test_session):
    """BIS-S-04: the app's session token opens the app's routes only."""
    merchant, store, made = await _setup(test_session)
    token = _mint(made, merchant, store)

    for path in (
        f"/api/v1/stores/{store.id}/apps",
        "/api/v1/auth/me",
    ):
        r = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, (path, r.status_code)
