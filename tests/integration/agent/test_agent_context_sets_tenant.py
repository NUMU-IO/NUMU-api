"""`get_agent_context` must seed the tenant ContextVar.

Every agent repository resolves its tenant through `get_tenant_id()` and fails
closed when it is unset. The tenant middleware only sets it for requests that
carry a tenant host, which a hub call to the shared API domain does not — so
each agent route has to get it from the shared dependency.

It did not, and only `POST /agent/chat` worked, because that route re-seeds the
value itself for an unrelated reason (a StreamingResponse body runs after the
request-scoped value is torn down). Every other route — the history list, the
digest, confirm, undo, audit — raised "No tenant context for agent persistence"
and the hub, which treats a failed fetch as an empty result, showed the merchant
an empty history and no digest.

The existing agent tests all call `set_tenant_id` themselves before exercising a
repository, which is exactly why none of them caught it: the bug was one layer
above, in the HTTP dependency.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.api.v1.agent import deps as agent_deps
from src.infrastructure.database.connection import get_tenant_id, set_tenant_id


class _Result:
    """Stands in for the store-ownership SELECT."""

    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value


class _Db:
    def __init__(self, store_id):
        self._store_id = store_id

    async def execute(self, _query):
        return _Result(self._store_id)


class _Tenant:
    def __init__(self, tenant_id):
        self.id = tenant_id


class _Membership:
    is_owner = True


class _Effective:
    def has_permission(self, _code):
        return True


class _PermissionService:
    def __init__(self, *_args, **_kwargs):
        pass

    async def get_effective_permissions(self, _membership):
        return _Effective()


@pytest.fixture(autouse=True)
def _clear_tenant():
    set_tenant_id(None)
    yield
    set_tenant_id(None)


@pytest.fixture
def _stub_permissions(monkeypatch):
    monkeypatch.setattr(agent_deps, "PermissionService", _PermissionService)
    monkeypatch.setattr(agent_deps, "RedisCacheService", lambda *a, **k: None)


async def test_context_seeds_tenant_for_every_agent_route(
    monkeypatch, _stub_permissions
):
    monkeypatch.setattr(agent_deps.app_settings, "agent_enabled", True)
    tenant_id, store_id = uuid4(), uuid4()

    assert get_tenant_id() is None, "precondition: nothing has set a tenant"

    ctx = await agent_deps.get_agent_context(
        store_id=store_id,
        user_id=uuid4(),
        tenant=_Tenant(tenant_id),
        membership=_Membership(),
        db=_Db(store_id),
    )

    # Without this the repositories raise PermissionError and the routes that
    # read history or build the digest return nothing.
    assert str(get_tenant_id()) == str(tenant_id)
    assert ctx.tenant_id == tenant_id


async def test_store_from_another_tenant_is_rejected(monkeypatch, _stub_permissions):
    """The ownership check still runs before anything is seeded."""
    from fastapi import HTTPException

    monkeypatch.setattr(agent_deps.app_settings, "agent_enabled", True)

    with pytest.raises(HTTPException) as exc:
        await agent_deps.get_agent_context(
            store_id=uuid4(),
            user_id=uuid4(),
            tenant=_Tenant(uuid4()),
            membership=_Membership(),
            db=_Db(None),  # SELECT finds no store for this tenant
        )
    assert exc.value.status_code == 404
