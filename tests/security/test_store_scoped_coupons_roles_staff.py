"""CL-1 extended — coupons, roles, and staff by-id access must obey the caller's scope.

Second wave of the tenant-isolation audit (2026-07-22). Same class as the
product/category leaks: a by-id lookup that ignored the authorised scope.

  * coupons GET/PATCH/DELETE — store_id now required (get_coupon was a
    cross-OWNER read; update/delete were cross-store-same-owner writes).
  * roles GET/PATCH/PUT-permissions/DELETE/clone — scoped to the caller's
    tenant; system templates (tenant_id NULL) stay globally readable but are
    never cross-tenant mutated.
  * staff GET/PUT-roles/DELETE — scoped to the caller's membership tenant.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from src.core.exceptions import EntityNotFoundError

# ── coupons ──────────────────────────────────────────────────────────────────


class _CouponRepo:
    def __init__(self, row) -> None:
        self.row = row

    async def get_by_id(self, entity_id):  # noqa: ANN001, ARG002
        return self.row


class _Coupon:
    def __init__(self, store_id) -> None:
        self.id = uuid4()
        self.store_id = store_id
        self.code = "FOREIGN"


@pytest.mark.asyncio
async def test_get_coupon_rejects_foreign_store():
    from src.application.use_cases.coupons.get_coupon import GetCouponUseCase

    uc = GetCouponUseCase(coupon_repository=_CouponRepo(_Coupon(uuid4())))
    with pytest.raises(EntityNotFoundError):
        await uc.execute(coupon_id=uuid4(), store_id=uuid4())


@pytest.mark.asyncio
async def test_get_coupon_requires_store_id():
    from src.application.use_cases.coupons.get_coupon import GetCouponUseCase

    uc = GetCouponUseCase(coupon_repository=_CouponRepo(_Coupon(uuid4())))
    with pytest.raises(TypeError):
        await uc.execute(coupon_id=uuid4())  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_delete_coupon_rejects_foreign_store():
    from src.application.use_cases.coupons.delete_coupon import DeleteCouponUseCase

    class _StoreRepo:
        async def get_by_id(self, sid):  # noqa: ANN001, ARG002
            class _S:
                owner_id = uuid4()

            return _S()

    uc = DeleteCouponUseCase(
        coupon_repository=_CouponRepo(_Coupon(uuid4())),
        store_repository=_StoreRepo(),
    )
    with pytest.raises(EntityNotFoundError):
        await uc.execute(coupon_id=uuid4(), user_id=uuid4(), store_id=uuid4())


# ── roles (route-level helper) ───────────────────────────────────────────────


class _RoleRepo:
    def __init__(self, role) -> None:
        self.role = role

    async def get_by_id(self, rid):  # noqa: ANN001, ARG002
        return self.role


class _Role:
    def __init__(self, tenant_id) -> None:
        self.id = uuid4()
        self.tenant_id = tenant_id
        self.is_locked = False


@pytest.mark.asyncio
async def test_role_helper_blocks_other_tenant():
    from src.api.v1.routes.roles.routes import _load_role_in_tenant

    mine = uuid4()
    repo = _RoleRepo(_Role(tenant_id=uuid4()))  # different tenant
    with pytest.raises(Exception) as exc:  # HTTPException(404)
        await _load_role_in_tenant(repo, uuid4(), str(mine), allow_system=True)
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_role_helper_allows_system_template_for_read_only():
    from src.api.v1.routes.roles.routes import _load_role_in_tenant

    mine = uuid4()
    system = _RoleRepo(_Role(tenant_id=None))  # system template
    # read (allow_system=True) → returned
    role = await _load_role_in_tenant(system, uuid4(), str(mine), allow_system=True)
    assert role is not None
    # write (allow_system=False) → refused even for a system template
    with pytest.raises(Exception) as exc:
        await _load_role_in_tenant(system, uuid4(), str(mine), allow_system=False)
    assert getattr(exc.value, "status_code", None) == 404


@pytest.mark.asyncio
async def test_role_helper_allows_own_tenant():
    from src.api.v1.routes.roles.routes import _load_role_in_tenant

    mine = uuid4()
    repo = _RoleRepo(_Role(tenant_id=mine))
    role = await _load_role_in_tenant(repo, uuid4(), str(mine), allow_system=False)
    assert role is not None
