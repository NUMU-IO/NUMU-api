"""Tenant middleware for request routing."""

import logging

from fastapi import HTTPException, Request
from sqlalchemy import select, text
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.database.connection import (
    AsyncSessionLocal,
    reset_tenant_schema,
    set_tenant_schema,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)

# Exact paths that bypass tenant resolution.
# `/api/v1/stores/check-subdomain` runs during the "create new store"
# flow when the user has no current-store context yet — the merchant
# hub still sends its last-viewed store's id as `x-tenant-id`, which
# can point at a deleted store and would 404 here. Subdomain
# availability checks don't need tenant context anyway; the SELECT
# against `stores.subdomain` is global.
PUBLIC_EXACT_PATHS = frozenset({
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/stores/check-subdomain",
})

# Path prefixes that bypass tenant resolution
PUBLIC_PATH_PREFIXES = (
    "/api/v1/public",
    "/api/v1/auth",
    "/api/v1/storefront",
)

# Host subdomains that are part of the control plane, not tenant storefronts.
# Requests arriving on these hosts (e.g. merchant.numueg.app) must not be
# resolved as a tenant — they will 404 because no tenant row matches.
RESERVED_HOST_SUBDOMAINS = frozenset({
    "www",
    "api",
    "admin",
    "merchant",
    "dashboard",
    "app",
    # Environment apex hosts (level-1 flat URL scheme):
    #   test.numueg.app, staging.numueg.app — env apex landing/API
    #   merchant-{test,staging}.numueg.app — merchant-hub control plane per env
    #   admin-{test,staging}.numueg.app    — admin dashboard per env
    # Per-tenant storefronts on test/staging live alongside, e.g.
    # `<store>-test.numueg.app` — those DO go through tenant resolution
    # with subdomain="<store>-test" (the env suffix is part of the saved
    # tenant subdomain; see numo-merchant-hub `withEnvSuffix`).
    "test",
    "staging",
    "merchant-test",
    "merchant-staging",
    "admin-test",
    "admin-staging",
})


class TenantMiddleware(BaseHTTPMiddleware):
    """Middleware to identify and set tenant context from subdomain."""

    async def dispatch(self, request: Request, call_next):
        """Process request and set tenant context if applicable."""
        path = request.url.path
        if path in PUBLIC_EXACT_PATHS or path.startswith(PUBLIC_PATH_PREFIXES):
            request.state.tenant = None
            return await call_next(request)

        # Extract host (remove port if present)
        host = request.headers.get("host", "").split(":")[0].lower()

        # Extract subdomain
        subdomain = self._extract_subdomain(host)

        # Header fallback for dashboards that don't run on a tenant subdomain.
        # The merchant hub sends the current store id; we resolve it to the owning tenant.
        store_id_header = request.headers.get("x-tenant-id")

        if not subdomain and not store_id_header:
            request.state.tenant = None
            return await call_next(request)

        # Get tenant from database
        try:
            async with AsyncSessionLocal() as session:
                # Explicitly set search_path to public for tenant lookup
                await session.execute(text("SET search_path TO public"))

                tenant_repo = TenantRepository(session)
                tenant = None
                if subdomain:
                    tenant = await tenant_repo.get_by_subdomain(subdomain)
                elif store_id_header:
                    # Header is a store id — resolve to its owning tenant
                    store_row = await session.execute(
                        select(StoreModel.tenant_id).where(
                            StoreModel.id == store_id_header
                        )
                    )
                    owning_tenant_id = store_row.scalar_one_or_none()
                    if owning_tenant_id:
                        tenant = await tenant_repo.get_by_id(owning_tenant_id)
                    else:
                        # Fall back to treating the header as a tenant id directly
                        tenant = await tenant_repo.get_by_id(store_id_header)

            if not tenant or not tenant.is_active:
                # Subdomain routing is the authoritative tenant signal —
                # if it doesn't resolve, the request is targeting a
                # store that doesn't exist and 404 is correct.
                #
                # The `x-tenant-id` header, by contrast, is a hub-side
                # hint sent on EVERY request from the merchant dashboard
                # (the current-store-picker value). A stale id — e.g.
                # the merchant just deleted that store, or it was
                # cleaned up server-side — would otherwise 404 every
                # endpoint including unrelated ones like
                # `/stores/check-subdomain` (used by "create new store").
                # Log + continue without tenant context; route handlers
                # that strictly require it can 401/404 themselves.
                if subdomain:
                    raise HTTPException(
                        status_code=404,
                        detail=f"Store '{subdomain}' not found or inactive.",
                    )
                logger.info(
                    "stale x-tenant-id header (no tenant matched id=%s) — "
                    "continuing without tenant context",
                    store_id_header,
                )
                request.state.tenant = None
                response = await call_next(request)
                return response

            # Set tenant context
            request.state.tenant = tenant
            set_tenant_schema(tenant.schema_name)

            response = await call_next(request)
            return response

        finally:
            # Always reset tenant context after request completes
            reset_tenant_schema()

    def _extract_subdomain(self, host: str) -> str | None:
        """Extract subdomain from host.

        Examples:
        - store1.octyrafiy.com -> store1
        - store1.localhost -> store1
        - localhost -> None
        - octyrafiy.com -> None
        - www.octyrafiy.com -> www (might want to skip 'www')
        - 0.0.0.0 -> None (IP address)
        - 127.0.0.1 -> None (IP address)
        """
        # Skip IP addresses
        if host.replace(".", "").isdigit():
            return None

        # Plain localhost with no subdomain
        if host == "localhost":
            return None

        parts = host.split(".")

        # Check if it's an IP address (all parts are numeric)
        if all(part.isdigit() for part in parts):
            return None

        # Handle *.localhost (e.g., octyra.localhost) — 2-part localhost dev URLs
        if parts[-1] == "localhost" and len(parts) == 2:
            subdomain = parts[0]
            if subdomain in RESERVED_HOST_SUBDOMAINS:
                return None
            return subdomain

        # For real internet domains we need at least 3 parts (sub.domain.tld)
        # for a real subdomain. `numueg.app` (2 parts) is the apex — there is
        # no subdomain, just the registered name. Returning the SLD here would
        # cause the tenant resolver to 404 on every apex hit (incl. the
        # staging host and the vite dev proxy after changeOrigin).
        if len(parts) < 3:
            return None

        # Handle regular domains (e.g., store1.octyrafiy.com)
        subdomain = parts[0]

        # Skip common non-tenant subdomains (control plane hosts)
        if subdomain in RESERVED_HOST_SUBDOMAINS:
            return None

        return subdomain
