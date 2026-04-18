"""Tenant middleware for request routing."""

import logging

from fastapi import HTTPException, Request
from sqlalchemy import text
from starlette.middleware.base import BaseHTTPMiddleware

from src.infrastructure.database.connection import (
    AsyncSessionLocal,
    reset_tenant_context,
    set_tenant_id,
    set_tenant_schema,
)
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)

# Routes that don't require tenant context
PUBLIC_PATHS = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/public",
    "/api/v1/auth",
    "/admin",
)


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Skip for public routes, docs, and health checks
        if request.url.path.startswith(PUBLIC_PATHS):
            request.state.tenant = None
            return await call_next(request)

        # Extract host (remove port if present)
        host = request.headers.get("host", "").split(":")[0].lower()

        # Extract subdomain
        subdomain = self._extract_subdomain(host)

        if not subdomain:
            # No subdomain found - allow request without tenant context
            # This handles cases like "localhost" or "octyrafiy.com"
            request.state.tenant = None
            return await call_next(request)

        # Get tenant from database
        try:
            async with AsyncSessionLocal() as session:
                # Explicitly set search_path to public for tenant lookup
                await session.execute(text("SET search_path TO public"))

                tenant_repo = TenantRepository(session)
                tenant = await tenant_repo.get_by_subdomain(subdomain)

            if not tenant or not tenant.is_active:
                raise HTTPException(
                    status_code=404,
                    detail=f"Store '{subdomain}' not found or inactive.",
                )

            # Set tenant context (both schema and ID for RLS)
            request.state.tenant = tenant
            set_tenant_schema(tenant.schema_name)
            set_tenant_id(tenant.id)

            response = await call_next(request)
            return response

        finally:
            # Always reset tenant context after request completes
            reset_tenant_context()

    def _extract_subdomain(self, host: str) -> str | None:
        """
        Extract subdomain from host.

        Examples:
        - store1.octyrafiy.com -> store1
        - store1.localhost -> store1
        - localhost -> None
        - octyrafiy.com -> None
        - www.octyrafiy.com -> www (might want to skip 'www')
        """
        parts = host.split(".")

        # Handle *.localhost dev URLs (store1.localhost)
        if parts[-1] == "localhost" and len(parts) == 2:
            subdomain = parts[0]
            if subdomain in ("www", "api", "admin", "merchant", "dashboard", "app"):
                return None
            return subdomain

        # For internet domains we need at least 3 parts (sub.domain.tld) for a
        # real subdomain. `numueg.app` (2 parts) is the apex — no subdomain.
        if len(parts) < 3:
            return None

        subdomain = parts[0]

        # Skip common non-tenant subdomains (control plane hosts:
        # the merchant hub, admin backoffice, marketing site, etc.)
        if subdomain in ("www", "api", "admin", "merchant", "dashboard", "app"):
            return None

        return subdomain
