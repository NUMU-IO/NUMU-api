"""Public merchant-storefront directory — no auth required.

URL: /api/v1/public/stores

Why this exists
---------------
Every merchant storefront lives on its own host (``<subdomain>.numueg.app``).
Nothing on the marketing site links to any of them, and no sitemap for those
hosts was ever submitted, so Googlebot had no path in: URL Inspection on a
perfectly indexable storefront page returned "URL is unknown to Google — no
referring sitemaps detected, no referring page".

This endpoint feeds ``https://numueg.app/stores``, which is prerendered at
build time into static HTML containing a real ``<a href>`` per storefront. That
gives every store an inbound link from an indexed page on an established
domain, which is what actually gets subdomains discovered and crawled.

What is deliberately excluded
-----------------------------
* Non-``ACTIVE`` stores, and stores with no subdomain (nothing to link to).
* Demo tenants — ephemeral, deleted after 7 days; linking to them would
  manufacture 404s, which is worse for the domain than no link at all.
* Seeded fake brands (``settings->>'demo_seed'``) — they are not real
  businesses and must never be presented publicly as merchants.
* Stores that opted out via ``settings.hide_from_directory = true``. A merchant
  running a private or wholesale-only storefront gets to say no.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_db
from src.api.responses import SuccessResponse
from src.core.entities.store import StoreStatus
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Generous, but bounded: the directory is a crawl surface, not a catalogue, and
# an unbounded page would degrade as the merchant count grows.
MAX_STORES = 500


@router.get(
    "/stores",
    summary="Public directory of live merchant storefronts",
    operation_id="get_public_store_directory",
)
async def get_public_store_directory(
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """Return live, publicly listable storefronts for the /stores crawl page."""
    settings_col = StoreModel.settings

    stmt = (
        select(
            StoreModel.name,
            StoreModel.subdomain,
            StoreModel.custom_domain,
            StoreModel.description,
            StoreModel.logo_url,
            StoreModel.country,
            StoreModel.created_at,
        )
        .join(TenantModel, TenantModel.id == StoreModel.tenant_id)
        .where(
            StoreModel.status == StoreStatus.ACTIVE,
            StoreModel.subdomain.isnot(None),
            TenantModel.lifecycle_state.in_([
                TenantLifecycleState.TRIAL,
                TenantLifecycleState.ACTIVE,
            ]),
            # JSONB ->> yields NULL when the key is absent, and `IS DISTINCT FROM`
            # keeps those rows (a plain `!=` would drop every store that has never
            # set the key — i.e. almost all of them).
            settings_col["demo_seed"].astext.is_(None),
            settings_col["hide_from_directory"].astext.is_distinct_from("true"),
        )
        .order_by(StoreModel.created_at.desc())
        .limit(MAX_STORES)
    )

    rows = (await db.execute(stmt)).all()

    stores = [
        {
            "name": r.name,
            "subdomain": r.subdomain,
            # A custom domain is the merchant's canonical home; link there so the
            # authority we pass lands on the host they actually rank with.
            "url": (
                f"https://{r.custom_domain}"
                if r.custom_domain
                else f"https://{r.subdomain}.numueg.app"
            ),
            "description": (r.description or "")[:200] or None,
            "logo_url": r.logo_url,
            "country": r.country,
        }
        for r in rows
    ]

    return SuccessResponse(
        data={"stores": stores, "count": len(stores)},
        message="Public store directory",
    )
