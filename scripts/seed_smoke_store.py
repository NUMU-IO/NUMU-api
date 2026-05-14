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

This seeder instead writes directly to Postgres via asyncpg. It only
needs three rows:

* ``public.tenants``      — for FK + row-level-security gates
* ``public.stores``       — subdomain ``smoke-store`` so the k6
                            store-by-subdomain lookup resolves
* ``public.products``     — one ACTIVE row with slug ``demo-tshirt``

The script is idempotent on every row (``ON CONFLICT DO NOTHING`` via
INSERT-WHERE-NOT-EXISTS) so re-runs are safe.

Run inside the api container (which has asyncpg + the source tree):

    docker compose -f docker/docker-compose.yml exec -T api \\
        python scripts/seed_smoke_store.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from uuid import UUID, uuid4

import asyncpg

SMOKE_SUBDOMAIN = "smoke-store"
SMOKE_PRODUCT_SLUG = "demo-tshirt"


def _build_pg_dsn() -> str:
    """Build a Postgres DSN from the container's env vars.

    Mirrors the docker-compose ``api`` service env block:
    ``POSTGRES_USER`` / ``POSTGRES_PASSWORD`` / ``POSTGRES_HOST`` /
    ``POSTGRES_PORT`` / ``POSTGRES_DB``.
    """
    user = os.getenv("POSTGRES_USER", "postgres")
    password = os.getenv("POSTGRES_PASSWORD", "postgres")
    host = os.getenv("POSTGRES_HOST", "db")
    port = os.getenv("POSTGRES_PORT", "5432")
    db = os.getenv("POSTGRES_DB", "numu")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


async def _ensure_tenant(conn: asyncpg.Connection) -> UUID:
    row = await conn.fetchrow(
        "SELECT id FROM public.tenants WHERE name = $1", "smoke-tenant"
    )
    if row is not None:
        return row["id"]
    tenant_id = uuid4()
    await conn.execute(
        """
        INSERT INTO public.tenants (id, name, status, plan, created_at, updated_at)
        VALUES ($1, $2, 'active', 'free', NOW(), NOW())
        ON CONFLICT DO NOTHING
        """,
        tenant_id,
        "smoke-tenant",
    )
    # Re-read in case ON CONFLICT skipped (race-free for a single seeder).
    row = await conn.fetchrow(
        "SELECT id FROM public.tenants WHERE name = $1", "smoke-tenant"
    )
    assert row is not None
    return row["id"]


async def _ensure_store(conn: asyncpg.Connection, tenant_id: UUID) -> UUID:
    row = await conn.fetchrow(
        "SELECT id FROM public.stores WHERE subdomain = $1", SMOKE_SUBDOMAIN
    )
    if row is not None:
        return row["id"]
    store_id = uuid4()
    await conn.execute(
        """
        INSERT INTO public.stores (
            id, tenant_id, name, slug, subdomain,
            default_currency, default_language, status,
            created_at, updated_at
        )
        VALUES ($1, $2, $3, $4, $5, 'EGP', 'en', 'ACTIVE', NOW(), NOW())
        ON CONFLICT (subdomain) DO NOTHING
        """,
        store_id,
        tenant_id,
        "Smoke Store",
        SMOKE_SUBDOMAIN,
        SMOKE_SUBDOMAIN,
    )
    row = await conn.fetchrow(
        "SELECT id FROM public.stores WHERE subdomain = $1", SMOKE_SUBDOMAIN
    )
    assert row is not None
    return row["id"]


async def _ensure_product(
    conn: asyncpg.Connection, tenant_id: UUID, store_id: UUID
) -> UUID:
    row = await conn.fetchrow(
        "SELECT id FROM public.products WHERE store_id = $1 AND slug = $2",
        store_id,
        SMOKE_PRODUCT_SLUG,
    )
    if row is not None:
        return row["id"]
    product_id = uuid4()
    # NULL `dimensions` is rejected by the storefront PLP/PDP serializer
    # (entity mapper expects a dict, not None), causing HTTP 500 on every
    # smoke request. The column is nullable in the schema so the INSERT
    # succeeds — the failure surfaces only when the row is read back.
    await conn.execute(
        """
        INSERT INTO public.products (
            id, tenant_id, store_id, name, slug, sku, description,
            product_type, status,
            price_amount, price_currency,
            quantity, low_stock_threshold,
            dimensions,
            images, tags, attributes, extra_data, options,
            created_at, updated_at
        )
        VALUES (
            $1, $2, $3, $4, $5, $6, $7,
            'PHYSICAL', 'ACTIVE',
            1000, 'EGP',
            100, 5,
            '{}'::jsonb,
            $8::text[], $9::text[],
            '{}'::jsonb, '{}'::jsonb, '[]'::jsonb,
            NOW(), NOW()
        )
        ON CONFLICT DO NOTHING
        """,
        product_id,
        tenant_id,
        store_id,
        "Demo T-shirt",
        SMOKE_PRODUCT_SLUG,
        "SKU-DEMO-001",
        "Smoke-test demo product.",
        ["https://example.test/shirt.jpg"],
        [],
    )
    row = await conn.fetchrow(
        "SELECT id FROM public.products WHERE store_id = $1 AND slug = $2",
        store_id,
        SMOKE_PRODUCT_SLUG,
    )
    assert row is not None
    return row["id"]


async def _seed() -> dict[str, str]:
    dsn = _build_pg_dsn()
    conn = await asyncpg.connect(dsn)
    try:
        tenant_id = await _ensure_tenant(conn)
        store_id = await _ensure_store(conn, tenant_id)
        product_id = await _ensure_product(conn, tenant_id, store_id)
    finally:
        await conn.close()
    return {
        "tenant_id": str(tenant_id),
        "store_id": str(store_id),
        "product_id": str(product_id),
        "subdomain": SMOKE_SUBDOMAIN,
    }


def main() -> int:
    try:
        result = asyncio.run(_seed())
    except Exception as exc:
        print(f"seed_smoke_store: failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"seed_smoke_store: OK subdomain={result['subdomain']} "
        f"store_id={result['store_id']} product_id={result['product_id']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
