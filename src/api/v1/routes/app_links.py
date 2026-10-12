"""Apex app links: ``GET https://numueg.app/a/<slug>/<rest>``.

WhatsApp URL buttons need a fixed base, so an app's message links use this
prefix, the way core's order links use ``/o/`` (``order_redirect.py``). Each
app registers a resolver in ``APP_LINKS`` that turns ``<rest>`` into the
absolute URL to open on the store's own host (and may record the click).
An unknown app, an unresolved link or a failure goes to the apex site, as
``/o/`` does, so the shopper never lands on an error page.

Mounted at the app root in ``src/main.py``. The apex routes ``/a/`` to this API
the same way it routes ``/o/``.
"""

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.connection import get_admin_db_session

logger = get_logger(__name__)

router = APIRouter(tags=["public"])

APEX_FALLBACK = "https://numueg.app/"

#: ``slug -> resolver(db, rest)``: the URL to open, or None.
AppLinkResolver = Callable[[AsyncSession, str], Awaitable[str | None]]
APP_LINKS: dict[str, AppLinkResolver] = {}


@router.get("/a/{slug}/{rest:path}", include_in_schema=False)
async def open_app_link(
    slug: str,
    rest: str,
    db: AsyncSession = Depends(get_admin_db_session),
) -> RedirectResponse:
    target = None
    resolver = APP_LINKS.get(slug)
    if resolver is not None:
        try:
            target = await resolver(db, rest)
        except Exception:  # noqa: BLE001 — a broken link still lands somewhere
            logger.warning("app_link_resolve_failed", app=slug)
    return RedirectResponse(target or APEX_FALLBACK, status_code=302)
