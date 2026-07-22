"""CL-1 wave 3 — staff / social / marketing by-id guards.

The staff sub-routes (overrides, policies, sessions, access requests,
invitations), social connections, reconciliation mismatches, and marketing
auto-match-rule deletes all operated on an id from the path/query/body without
checking it belonged to the caller's tenant/store. Verified cross-owner
2026-07-22. These pin the shared guards.
"""

from __future__ import annotations

from uuid import uuid4

import pytest


class _Result:
    def __init__(self, value) -> None:
        self._v = value

    def scalar_one_or_none(self):
        return self._v


class _Session:
    """Returns a fixed tenant_id for any select — the guard must not lean on
    the query filtering, only on its own comparison."""

    def __init__(self, tenant_id) -> None:
        self._t = tenant_id

    async def execute(self, _query):
        return _Result(self._t)


class _Caller:
    def __init__(self, tenant_id) -> None:
        self.tenant_id = tenant_id


@pytest.mark.asyncio
async def test_membership_scope_blocks_other_tenant():
    from src.api.v1.routes.staff._scope import assert_membership_in_tenant

    mine, theirs = uuid4(), uuid4()
    with pytest.raises(Exception) as exc:
        await assert_membership_in_tenant(_Session(theirs), uuid4(), _Caller(mine))
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_membership_scope_allows_own_tenant():
    from src.api.v1.routes.staff._scope import assert_membership_in_tenant

    mine = uuid4()
    await assert_membership_in_tenant(
        _Session(mine), uuid4(), _Caller(mine)
    )  # no raise


@pytest.mark.asyncio
async def test_membership_scope_missing_row_is_not_found():
    from src.api.v1.routes.staff._scope import assert_membership_in_tenant

    with pytest.raises(Exception) as exc:
        await assert_membership_in_tenant(_Session(None), uuid4(), _Caller(uuid4()))
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_row_scope_blocks_other_tenant():
    from src.api.v1.routes.staff._scope import assert_row_in_tenant
    from src.infrastructure.database.models.public.staff_invitation import (
        StaffInvitationModel,
    )

    # A real model so select(model.tenant_id).where(model.id == …) constructs;
    # the fake session ignores the query and returns a FOREIGN tenant_id.
    mine, theirs = uuid4(), uuid4()
    with pytest.raises(Exception) as exc:
        await assert_row_in_tenant(
            _Session(theirs),
            StaffInvitationModel,
            uuid4(),
            _Caller(mine),
            not_found="x",
        )
    assert getattr(exc.value, "status_code", None) == 404
    # own tenant → no raise
    await assert_row_in_tenant(
        _Session(mine), StaffInvitationModel, uuid4(), _Caller(mine), not_found="x"
    )


@pytest.mark.asyncio
async def test_social_connection_guard_blocks_foreign_store():
    from src.api.v1.routes.stores.social import _require_connection_in_store

    class _ConnRepo:
        def __init__(self, store_id) -> None:
            self._sid = store_id

        async def get_by_id(self, cid):  # noqa: ARG002
            class _C:
                store_id = self._sid

            return _C()

    class _Store:
        id = uuid4()

    store = _Store()
    with pytest.raises(Exception) as exc:  # foreign store_id
        await _require_connection_in_store(_ConnRepo(uuid4()), uuid4(), store)
    assert getattr(exc.value, "status_code", None) == 404
    # own store → no raise
    await _require_connection_in_store(_ConnRepo(store.id), uuid4(), store)


def test_delete_group_scopes_to_campaign_signature():
    """The repo delete_group must accept campaign_id so callers can scope it."""
    import inspect

    from src.infrastructure.repositories.campaign_auto_match_repository import (
        CampaignAutoMatchRepository,
    )

    sig = inspect.signature(CampaignAutoMatchRepository.delete_group)
    assert "campaign_id" in sig.parameters
