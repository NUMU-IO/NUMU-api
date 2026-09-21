"""Fixes for Sentry (Seer) review findings on the apps and Kashier PRs.

Each test names the PR the finding was raised on. Three of them were marked
"resolved" by Seer only because a rebase moved the lines (api #638, #639).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.infrastructure.external_services.kashier.payment_service import (
    KashierPaymentService,
)

# ─── api #644: a forged Kashier body is a 400/401, never a 500 ─────────────


@pytest.mark.parametrize(
    "keys", [["amount", 1], [["nested"]], [None, "status"], "amount", {"a": 1}]
)
def test_malformed_signature_keys_fail_verification_without_raising(keys):
    service = KashierPaymentService(mid="MID-1", api_key="k", mode="test")
    body = {"data": {"amount": 1, "status": "SUCCESS", "signatureKeys": keys}}
    assert service.verify_webhook_signature(json.dumps(body).encode(), "00") is None


def test_a_json_array_body_is_a_400():
    from src.api.v1.routes.webhooks import kashier as hook

    class _Request:
        async def body(self):
            return b"[1, 2, 3]"

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            hook.kashier_callback(_Request(), db=None, x_kashier_signature="00")
        )
    assert exc.value.status_code == 400


# ─── api #637: PATCH /partners/me can clear an optional field ─────────────


def _update(body, monkeypatch):
    from src.api.v1.routes import partners as routes

    account = SimpleNamespace(
        id=uuid4(),
        website_url="https://old.example.com",
        support_phone="+201000000000",
        display_name="Bosta Sync Co",
        country="EG",
        support_email="p@example.com",
        agreement_version=None,
    )

    async def lookup(_db, _uid):
        return account

    class _Db:
        async def flush(self):
            pass

        async def refresh(self, _row):
            pass

    monkeypatch.setattr(routes, "partner_for_user", lookup)
    monkeypatch.setattr(routes, "_out", lambda a: a)
    request = SimpleNamespace(headers={}, client=None)
    asyncio.run(
        routes.update_me(
            routes.UpdateProfileRequest(**body), request, user_id=uuid4(), db=_Db()
        )
    )
    return account


def test_an_explicit_null_clears_an_optional_field(monkeypatch):
    account = _update({"website_url": None, "support_phone": None}, monkeypatch)
    assert account.website_url is None
    assert account.support_phone is None


def test_a_required_field_is_never_cleared(monkeypatch):
    account = _update({"display_name": None, "support_email": None}, monkeypatch)
    assert account.display_name == "Bosta Sync Co"
    assert account.support_email == "p@example.com"


def test_country_is_still_uppercased(monkeypatch):
    assert _update({"country": "sa"}, monkeypatch).country == "SA"


# ─── api #638: the listing's developer is the app OWNER ───────────────────


@pytest.mark.parametrize(
    ("developer_id", "owner", "expected"),
    [
        (None, None, "NUMU"),
        (uuid4(), None, "NUMU"),  # e.g. created by a super admin
        (uuid4(), SimpleNamespace(display_name="Bosta Sync Co"), "Bosta Sync Co"),
    ],
)
def test_developer_name_never_reads_the_callers_account(
    monkeypatch, developer_id, owner, expected
):
    from src.api.v1.routes import partner_apps

    seen = []

    async def lookup(_db, user_id):
        seen.append(user_id)
        return owner

    monkeypatch.setattr(partner_apps, "partner_for_user", lookup)
    app = SimpleNamespace(developer_id=developer_id)
    assert asyncio.run(partner_apps._developer_name(None, app)) == expected
    assert seen == ([developer_id] if developer_id else [])


# ─── api #639: an app token acts only on its installation's tenant ────────


def test_an_app_token_on_another_tenants_host_is_refused(monkeypatch):
    from src.api.dependencies import auth
    from src.application.services import app_tokens
    from src.infrastructure.database import connection

    tenant = uuid4()
    principal = SimpleNamespace(
        installation=SimpleNamespace(store_id=uuid4(), tenant_id=tenant),
        token=SimpleNamespace(scopes=["orders:read"]),
        app=SimpleNamespace(manifest={}),
    )

    async def resolve(_session, _raw):
        return principal

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_exc):
            return False

    monkeypatch.setattr(app_tokens, "resolve_app_token", resolve)
    monkeypatch.setattr(connection, "AsyncSessionLocal", _Session)
    request = SimpleNamespace(
        state=SimpleNamespace(tenant=SimpleNamespace(id=uuid4())),
        url=SimpleNamespace(path="/api/v1/stores/x/orders/"),
        method="GET",
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(auth._resolve_app_principal("numu_app_x", request))
    assert exc.value.status_code == 403
    assert "not valid for this store" in exc.value.detail
