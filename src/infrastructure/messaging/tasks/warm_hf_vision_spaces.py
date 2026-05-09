"""Celery beat task: keep HuggingFace OCR Spaces warm.

Runs every 10 minutes. Free HF Spaces sleep on inactivity; the first
call after sleep takes 30–60s to cold-start and the customer's proof
upload would soft-fail through the OCR layer (the auto-approval rules
no-op on ``ocr_status="failed"``, so it's safe but it means the
merchant misses signal on that submission).

To keep cold starts to a minimum we ping each Space that's actually
in use somewhere across the platform. The scan is one cross-store
JSONB query — cheap, runs under the existing celery worker tenancy
context which doesn't matter for read-only public-data queries.

Failures are logged and swallowed: a sleeping Space will return its
own warm-up text and a missing Space will 4xx; neither should
escalate into Celery retries. The next 10-minute tick is its own
attempt.
"""

from __future__ import annotations

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


# Map from settings.ocr_provider value → HF Space ID. Kept here
# (not on the service classes) so the keep-warm task can iterate
# over providers without having to instantiate each service class.
_HF_SPACE_BY_PROVIDER: dict[str, str] = {
    "deepseek_hf": "merterbak/DeepSeek-OCR-Demo",
    "glm_hf": "prithivMLmods/GLM-OCR-Demo",
}


_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def _providers_in_use() -> set[str]:
    """Return the set of HF provider strings active in any store.

    Skips the JSONB scan when neither HF provider is actually used,
    avoiding a wasted ping to two Spaces that no merchant routes to.
    Empty set → no work to do this tick.
    """
    from sqlalchemy import select, text

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.store import StoreModel

    in_use: set[str] = set()
    async with AsyncSessionLocal() as session:
        # Direct JSONB path read — much faster than fetching every
        # store row and inspecting in Python. Postgres ``->>`` returns
        # a text scalar; we group by it to dedupe.
        stmt = (
            select(
                text("DISTINCT settings -> 'payment' -> 'instapay' ->> 'ocr_provider'")
            )
            .select_from(StoreModel)
            .where(
                text(
                    "settings -> 'payment' -> 'instapay' ->> 'ocr_provider' IS NOT NULL"
                )
            )
        )
        result = await session.execute(stmt)
        for (provider,) in result.all():
            if provider in _HF_SPACE_BY_PROVIDER:
                in_use.add(provider)
    return in_use


def _ping_space(space_id: str) -> None:
    """Wake up an HF Space with one tiny request.

    We don't actually call ``/predict`` because the predict endpoints
    accept files and computing the keep-warm payload + parsing the
    response is wasted effort. Hitting the Space's HTTP root is enough
    to bring it out of sleep — the Space router is a process that
    doesn't go down with the model.
    """
    import httpx

    url = f"https://huggingface.co/spaces/{space_id}"
    try:
        # Sync httpx because the Celery task isn't running async
        # workflow elsewhere; one short request per tick.
        httpx.get(url, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 — keep-warm must not retry
        logger.info("warm_hf_vision_space_failed space=%s reason=%s", space_id, exc)


async def _async_warm() -> int:
    providers = await _providers_in_use()
    if not providers:
        return 0
    for provider in providers:
        space_id = _HF_SPACE_BY_PROVIDER[provider]
        _ping_space(space_id)
    return len(providers)


@celery_app.task(
    name="tasks.warm_hf_vision_spaces",
    ignore_result=True,
)
def warm_hf_vision_spaces() -> dict[str, int]:
    pinged = _run_async(_async_warm())
    return {"pinged": pinged}
