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

# Exact paths that bypass tenant resolution
PUBLIC_EXACT_PATHS = frozenset({"/", "/health", "/docs", "/redoc", "/openapi.json"})

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
                identifier = subdomain or f"id={store_id_header}"
                raise HTTPException(
                    status_code=404,
                    detail=f"Store '{identifier}' not found or inactive.",
                )

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

        # Need at least 2 parts to have a subdomain
        if len(parts) < 2:
            return None

        # Handle *.localhost (e.g., octyra.localhost)
        if parts[-1] == "localhost" and len(parts) == 2:
            subdomain = parts[0]
            if subdomain in RESERVED_HOST_SUBDOMAINS:
                return None
            return subdomain

        # Handle regular domains (e.g., store1.octyrafiy.com)
        subdomain = parts[0]

        # Skip common non-tenant subdomains (control plane hosts)
        if subdomain in RESERVED_HOST_SUBDOMAINS:
            return None

        return subdomain
