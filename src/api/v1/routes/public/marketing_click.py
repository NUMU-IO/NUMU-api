"""The redirect that counts a marketing click.

URL: /api/v1/public/r/{token}/{index} — no auth, by definition: the person
following it is a merchant reading their email, not a session.

Only ever redirects to a URL stored on the outreach row at send time. The
destination is looked up by position, never taken from the request, so this
cannot be turned into an open redirect by editing the link — which matters
because the domain in front of it is the one our transactional mail sends from.

A failed lookup redirects to the marketing site rather than showing an error.
Someone who clicks a link in an email we sent should land somewhere useful even
if the row was pruned; a 404 in a browser reads as "this company's link is
broken", which costs more than the lost data point.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import RedirectResponse

from src.api.dependencies.database import get_db
from src.core.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

FALLBACK_URL = "https://numueg.app"


@router.get(
    "/r/{token}/{index}",
    include_in_schema=False,
    summary="Record a marketing click and redirect",
)
async def marketing_click(
    db: Annotated[AsyncSession, Depends(get_db)],
    token: Annotated[str, Path(min_length=16, max_length=64)],
    index: Annotated[int, Path(ge=0, le=99)],
) -> RedirectResponse:
    row = (
        (
            await db.execute(
                text(
                    "SELECT id, links FROM public.marketing_outreach "
                    "WHERE click_token = :token"
                ),
                {"token": token},
            )
        )
        .mappings()
        .first()
    )

    if row is None:
        logger.info("marketing_click_unknown_token")
        return RedirectResponse(FALLBACK_URL, status_code=302)

    links = row["links"] or []
    if index >= len(links):
        logger.warning("marketing_click_index_out_of_range", index=index)
        return RedirectResponse(FALLBACK_URL, status_code=302)

    # clicked_at keeps the FIRST click; click_count counts them all. Overwriting
    # clicked_at on every open would turn "when did this land" into "when did
    # they last look at it", and the first is the one that answers whether the
    # campaign worked.
    await db.execute(
        text(
            "UPDATE public.marketing_outreach "
            "SET clicked_at = COALESCE(clicked_at, now()), "
            "    click_count = click_count + 1 "
            "WHERE id = :id"
        ),
        {"id": row["id"]},
    )
    await db.commit()

    return RedirectResponse(links[index], status_code=302)
