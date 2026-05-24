"""Public short-link redirector — ``GET /r/{short_code}``.

Mounted at the **app root** (not under ``/api/v1``) so the URL stays
clean: ``https://numueg.app/r/AB7K9XYZ`` rather than
``/api/v1/r/...``. The trade-off is that this router needs to be
included on ``app`` directly in ``main.py``, not on ``api_router``.

The handler is intentionally lean — one indexed DB lookup, return a
302 with the destination URL, and dispatch a fire-and-forget click
counter bump. No middleware overhead beyond the global request log /
Sentry / CORS that already wraps every route. Goal: <20ms p95
including DB roundtrip.

SEC notes:
* The destination URL is treated as trusted at read time. The trust
  boundary is enforced at creation time by
  ``short_link_service.validate_destination_host`` — by the time a
  row exists, its destination is known to belong to a NUMU store.
* No auth on this route; short codes are public sharing artifacts.
  An attacker scraping codes can only discover URLs that are
  already meant to be public.
* ``is_active=false`` and expired rows return 404 (not the
  destination, not 410) — 404 leaks the least information.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Path
from fastapi.responses import RedirectResponse

from src.application.services import short_link_service
from src.infrastructure.database.connection import AsyncSessionLocal

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Short Links"])

# Crockford codes use 8 chars from the 32-char alphabet, but we accept
# 4–12 here so future format changes (longer codes, branded vanity
# codes, mixed-case) don't immediately 404. The DB lookup handles the
# actual existence check.
_CODE_PATTERN = r"^[0-9A-Za-z]{4,12}$"


async def _bump_async(short_link_id: str) -> None:
    """Run the click-count bump in its own session off the response path.

    Opens a fresh ``AsyncSessionLocal`` because the request-scoped
    session has already been closed by the time FastAPI schedules
    background tasks. Failures are swallowed and logged — a missed
    click is a tolerable undercount, not a correctness bug.
    """
    try:
        async with AsyncSessionLocal() as session:
            await short_link_service.bump_click_count(
                session=session,
                short_link_id=short_link_id,
            )
            await session.commit()
    except Exception:  # pragma: no cover - background path
        logger.exception("short_link click-count bump failed for id=%s", short_link_id)


@router.get(
    "/api/v1/short-links/{short_code}/resolve",
    summary="JSON-resolve a short_code (used by storefront /r/{code} pages)",
    operation_id="resolve_short_link_json",
    responses={
        200: {"description": "Destination URL"},
        404: {"description": "Unknown, disabled, or expired short_code"},
    },
)
async def resolve_short_link_json(
    background_tasks: BackgroundTasks,
    short_code: str = Path(
        ...,
        min_length=4,
        max_length=12,
        pattern=_CODE_PATTERN,
    ),
):
    """JSON variant of the redirector.

    Feature 002 changed the displayed short URL to use the merchant's
    own storefront host (``<store>.numueg.app/r/{code}``) instead of
    the apex. The storefronts don't run FastAPI, so they need a way to
    look up the destination from the API. This endpoint returns the
    destination URL as JSON; the storefront's ``/r/:code`` route
    fetches it and ``window.location.replace``s.

    Public — no auth — same security profile as the 302 path: codes
    are sharing artifacts; not-found / disabled / expired all return
    404 (no info leak).
    """
    from fastapi import HTTPException

    normalized = short_code.upper()
    async with AsyncSessionLocal() as session:
        row = await short_link_service.resolve_short_code(
            session=session,
            short_code=normalized,
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Not found")

    background_tasks.add_task(_bump_async, row.id)
    return {"destination_url": row.destination_url, "short_code": normalized}


@router.get(
    "/r/{short_code}",
    summary="Redirect a short_code to its destination URL",
    operation_id="resolve_short_link",
    # The route returns a 302 redirect, not a JSON body — exclude it
    # from the OpenAPI response model machinery so FastAPI doesn't
    # try to validate the destination URL against a Pydantic model.
    response_class=RedirectResponse,
    status_code=302,
    responses={
        302: {"description": "Redirect to the destination URL"},
        404: {"description": "Unknown, disabled, or expired short_code"},
    },
)
async def resolve_short_link(
    background_tasks: BackgroundTasks,
    short_code: str = Path(
        ...,
        min_length=4,
        max_length=12,
        pattern=_CODE_PATTERN,
        description="Crockford base32 short code, case-insensitive",
    ),
):
    """Resolve a short_code and 302 to its destination URL.

    Returns 404 for unknown / disabled / expired codes — leaks no
    information about which case fired. Schedules an asynchronous
    counter bump so the response stays fast.
    """
    # Crockford is uppercase-canonical; normalize so a link typed
    # lowercase from a screenshot still resolves.
    normalized = short_code.upper()

    async with AsyncSessionLocal() as session:
        row = await short_link_service.resolve_short_code(
            session=session,
            short_code=normalized,
        )

    if row is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Not found")

    # Fire-and-forget the click counter. Even if this fails the
    # redirect already left the building.
    background_tasks.add_task(_bump_async, row.id)

    # 302 (temporary) so browsers don't aggressively cache the
    # mapping — merchants can disable a link mid-campaign and the
    # next click should hit our 404 path, not a cached 301.
    return RedirectResponse(url=row.destination_url, status_code=302)
