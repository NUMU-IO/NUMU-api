"""Maintenance-mode middleware.

When `platform_settings.maintenance_mode` is true, this middleware responds
with 503 to everything except a small allow-list that must keep working:

- `/api/v1/health`                — so uptime monitors don't page at 3am
- `/api/v1/admin/...`             — admins need to turn maintenance off
- `/api/v1/auth/...`              — admins need to log in to reach the admin panel
- `/docs`, `/redoc`, `/openapi.json` — docs stay accessible

Everything else — storefront traffic, merchant hub API calls, webhooks —
gets a JSON 503 so clients can render a "be right back" screen.

The setting is cached in-process for a few seconds to avoid a DB round-trip
on every request. Changing the flag in the admin UI takes effect within
`CACHE_SECONDS`.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger(__name__)

# Paths that must keep working even during maintenance.
_ALLOW_PREFIXES: tuple[str, ...] = (
    "/api/v1/health",
    "/api/v1/public/health",
    "/api/v1/admin/",  # admins must be able to toggle maintenance off
    "/api/v1/auth/",  # + log in first
    "/api/v1/admin/auth/",
    "/docs",
    "/redoc",
    "/openapi.json",
)

# 5-second in-process cache — balance between quick toggle-on latency and
# avoiding a DB hit per request under load.
CACHE_SECONDS = 5
_cache: dict[str, tuple[float, bool]] = {"v": (0.0, False)}


async def _is_maintenance_on() -> bool:
    """Read the flag with a short in-process cache."""
    now = time.monotonic()
    ts, value = _cache["v"]
    if now - ts < CACHE_SECONDS:
        return value

    # Late import to avoid a circular dep at module load.
    from sqlalchemy.ext.asyncio import AsyncSession

    from src.api.v1.routes.admin.platform_settings import get_platform_settings
    from src.infrastructure.database.connection import AsyncSessionLocal

    try:
        session: AsyncSession
        async with AsyncSessionLocal() as session:
            settings = await get_platform_settings(session)
            new_value = bool(settings.get("maintenance_mode", False))
    except Exception:
        logger.exception(
            "maintenance middleware: failed to read setting; defaulting off"
        )
        new_value = False

    _cache["v"] = (now, new_value)
    return new_value


class MaintenanceModeMiddleware(BaseHTTPMiddleware):
    """Short-circuit merchant/storefront traffic while platform is in maintenance."""

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path
        if path.startswith(_ALLOW_PREFIXES):
            return await call_next(request)

        # CORS preflight must go through unconditionally — browsers don't
        # read a 503 preflight sensibly.
        if request.method == "OPTIONS":
            return await call_next(request)

        if await _is_maintenance_on():
            return JSONResponse(
                status_code=503,
                content={
                    "detail": "Platform is in maintenance mode. Please try again shortly.",
                    "maintenance": True,
                },
                headers={"Retry-After": "60"},
            )
        return await call_next(request)
