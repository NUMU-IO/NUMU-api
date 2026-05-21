"""Step 15 — minimal seeder for the per-PR k6 smoke job.

Creates a single ``smoke-store`` subdomain with one ACTIVE demo product
so the storefront read paths (``store-by-subdomain``, ``products``,
``products/{slug}``) all return 200 under k6.

Why this is NOT ``seed_load_test_stores.py``:

The full seeder authenticates as a SUPER_ADMIN and goes through the
``/auth/login`` + ``POST /stores/`` + ``POST /stores/{id}/seed-demo``
HTTP flow. That flow assumes an admin user already exists. The
per-PR smoke job boots a fresh ``docker-compose.yml`` stack with an
empty database — no admin user, so the HTTP-auth flow can't bootstrap
itself.

This seeder writes via the project's SQLAlchemy ORM models (#290 —
formerly raw asyncpg INSERTs). Going through the ORM means a future
migration that adds a NOT NULL column or renames an existing one
either picks up the new model default automatically or fails loudly
at import time, instead of silently producing rows the storefront
serializer chokes on (#294 was the previous version of that bug).

Four rows minimum:

* ``public.users``        — owner_id FK target for stores
* ``public.tenants``      — for tenant_id FK + RLS gates
* ``public.stores``       — subdomain ``smoke-store`` so the k6
                            store-by-subdomain lookup resolves
* ``public.products``     — one ACTIVE row with slug ``demo-tshirt``

The script is idempotent on every row (``WHERE`` lookup before
``add()``) so re-runs are safe.

Run inside the api container:

    docker compose -f docker/docker-compose.yml exec -T api \\
        python scripts/seed_smoke_store.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from uuid import UUID

# Ensure src/ is importable when run as a standalone script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.product import ProductStatus, ProductType
from src.core.entities.store import StoreStatus
from src.core.entities.user import UserRole, UserStatus
from src.core.value_objects.money import Currency
from src.infrastructure.database import AsyncSessionLocal


# UserModel + StoreModel reference TenantMembershipModel / RoleModel /
# MembershipRoleModel via string-form relationships. None of them are
# re-exported from `src.infrastructure.database.models`, so the first
# ORM call (`select(UserModel)`) trips a mapper-resolution chain that
# bubbles back as "expression failed to locate a name". The fix Celery
# uses for the same class of bug: walk the models package once at
# import time so every class registers with the mapper. See
# `_load_all_models` in `src/infrastructure/messaging/celery_app.py`.
def _load_all_models() -> None:
    import importlib
    import pkgutil

    import src.infrastructure.database.models as _pkg

    for module_info in pkgutil.walk_packages(_pkg.__path__, prefix=_pkg.__name__ + "."):
        importlib.import_module(module_info.name)


_load_all_models()


from src.infrastructure.database.models import (  # noqa: E402
    ProductModel,
    StoreModel,
    TenantModel,
    UserModel,
)

SMOKE_SUBDOMAIN = "smoke-store"
SMOKE_TENANT_SUBDOMAIN = "smoke-tenant"
SMOKE_OWNER_EMAIL = "smoke-owner@example.test"
SMOKE_PRODUCT_SLUG = "demo-tshirt"

# Bcrypt-shaped placeholder so the column's CHECK / length constraints (if
# any) are happy; the smoke job never authenticates this user.
SMOKE_PASSWORD_HASH = (
    "$2b$12$smoke.placeholder.smoke.placeholder.smoke.placeholder.smoke.."
)


async def _ensure_owner(session: AsyncSession) -> UserModel:
    user = await session.scalar(
        select(UserModel).where(UserModel.email == SMOKE_OWNER_EMAIL)
    )
    if user is not None:
        return user
    user = UserModel(
        email=SMOKE_OWNER_EMAIL,
        hashed_password=SMOKE_PASSWORD_HASH,
        first_name="Smoke",
        last_name="Owner",
        role=UserRole.STORE_OWNER,
        status=UserStatus.ACTIVE,
    )
    session.add(user)
    await session.flush()
    return user


async def _ensure_tenant(session: AsyncSession, owner: UserModel) -> TenantModel:
    tenant = await session.scalar(
        select(TenantModel).where(TenantModel.subdomain == SMOKE_TENANT_SUBDOMAIN)
    )
    if tenant is not None:
        return tenant
    tenant = TenantModel(
        name="Smoke Tenant",
        subdomain=SMOKE_TENANT_SUBDOMAIN,
        owner_id=owner.id,
        plan="trial",
        is_active=True,
    )
    session.add(tenant)
    await session.flush()
    return tenant


async def _ensure_store(
    session: AsyncSession, tenant: TenantModel, owner: UserModel
) -> StoreModel:
    store = await session.scalar(
        select(StoreModel).where(StoreModel.subdomain == SMOKE_SUBDOMAIN)
    )
    if store is not None:
        return store
    store = StoreModel(
        tenant_id=tenant.id,
        owner_id=owner.id,
        name="Smoke Store",
        slug=SMOKE_SUBDOMAIN,
        subdomain=SMOKE_SUBDOMAIN,
        status=StoreStatus.ACTIVE,
        default_currency=Currency.EGP,
        default_language="en",
    )
    session.add(store)
    await session.flush()
    return store


async def _ensure_product(
    session: AsyncSession, tenant: TenantModel, store: StoreModel
) -> ProductModel:
    product = await session.scalar(
        select(ProductModel).where(
            ProductModel.store_id == store.id,
            ProductModel.slug == SMOKE_PRODUCT_SLUG,
        )
    )
    if product is not None:
        return product
    # Mapped[...] columns with default=dict / default=list pick up the
    # empty container at flush time when not set explicitly — that's how
    # we avoid recurrences of #294 (NULL `dimensions` 500-ing the PLP
    # serializer) without listing every container column here.
    product = ProductModel(
        tenant_id=tenant.id,
        store_id=store.id,
        name="Demo T-shirt",
        slug=SMOKE_PRODUCT_SLUG,
        sku="SKU-DEMO-001",
        description="Smoke-test demo product.",
        product_type=ProductType.PHYSICAL,
        status=ProductStatus.ACTIVE,
        price_amount=1000,
        price_currency="EGP",
        quantity=100,
        low_stock_threshold=5,
        images=["https://example.test/shirt.jpg"],
    )
    session.add(product)
    await session.flush()
    return product


async def _seed() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        # RLS bypass: tenant / store / product policies require
        # `app.current_tenant` to match `tenant_id` on INSERT. The
        # bypass flag short-circuits that check — same pattern as
        # `get_admin_db_session` in connection.py.
        await session.execute(
            sql_text("SELECT set_config('app.rls_bypass', 'true', true)")
        )
        try:
            owner = await _ensure_owner(session)
            tenant = await _ensure_tenant(session, owner)
            store = await _ensure_store(session, tenant, owner)
            product = await _ensure_product(session, tenant, store)
            await session.commit()
            tenant_id: UUID = tenant.id
            store_id: UUID = store.id
            product_id: UUID = product.id
        finally:
            await session.execute(
                sql_text("SELECT set_config('app.rls_bypass', 'false', true)")
            )

    return {
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "product_id": str(product_id),
        "subdomain": SMOKE_SUBDOMAIN,
    }


def main() -> int:
    try:
        result = asyncio.run(_seed())
    except Exception as exc:  # noqa: BLE001
        print(f"seed_smoke_store: failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"seed_smoke_store: OK subdomain={result['subdomain']} "
        f"store_id={result['store_id']} product_id={result['product_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
