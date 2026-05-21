"""Celery worker — funnel-event ingest with ON CONFLICT dedupe.

Step 09. The storefront ``/track`` and ``/track-event`` handlers push
funnel events onto the ``analytics`` queue and return 202 immediately;
this task is the worker side that actually writes the row.

Idempotency comes from a Postgres partial UNIQUE index on
``funnel_events.event_id`` plus ``INSERT … ON CONFLICT DO NOTHING``.
That means:

* A worker that crashes between dequeue and DB commit is requeued by
  Celery's ``acks_late`` + ``reject_on_worker_lost`` config; the
  redelivered task tries to insert the same ``event_id`` and the
  conflict-do-nothing clause makes it a no-op.
* Two workers concurrently processing the same ``event_id`` (Redis
  dedupe missed) likewise collapse to one row.
* A row that lands here without an ``event_id`` (legacy callers, or
  the kill-switch sync-fallback before the task gets queued) skips
  the UNIQUE index entirely because it's partial — no false rejects.

The task itself is intentionally tiny: validate, INSERT, commit. No
fan-out to other systems lives here; that's the ``meta_capi`` /
``analytics_dispatcher`` paths in the handler.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from uuid import UUID

from sqlalchemy.dialects.postgresql import insert as pg_insert

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _coerce_uuid(value: str | UUID | None) -> UUID | None:
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(value)
    except (TypeError, ValueError):
        return None


async def _insert_funnel_event(event: dict[str, Any]) -> bool:
    """Insert one funnel_event row with ON CONFLICT DO NOTHING.

    Returns True if a row was inserted, False if the event_id already
    existed (idempotent no-op).
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.funnel_event import (
        FunnelEventModel,
    )

    payload: dict[str, Any] = {
        "tenant_id": _coerce_uuid(event["tenant_id"]),
        "store_id": _coerce_uuid(event["store_id"]),
        "step": event["step"],
        "session_fingerprint": event.get("session_fingerprint"),
        "customer_id": _coerce_uuid(event.get("customer_id")),
        "step_data": event.get("step_data"),
        "event_id": _coerce_uuid(event.get("event_id")),
        # Feature 001 — attribution columns. Optional; legacy task
        # payloads that don't include these keys get NULL columns,
        # which is the correct fallback.
        "utm_source": event.get("utm_source"),
        "utm_medium": event.get("utm_medium"),
        "utm_campaign": event.get("utm_campaign"),
        "utm_term": event.get("utm_term"),
        "utm_content": event.get("utm_content"),
        "campaign_id": _coerce_uuid(event.get("campaign_id")),
        "referrer": event.get("referrer"),
    }

    stmt = pg_insert(FunnelEventModel).values(**payload)
    if payload["event_id"] is not None:
        # The UNIQUE index is partial (WHERE event_id IS NOT NULL), so
        # ON CONFLICT must mirror that predicate exactly — otherwise
        # Postgres rejects with "no unique or exclusion constraint
        # matching the ON CONFLICT specification". See migration
        # funnel_event_idemp_20260514.
        stmt = stmt.on_conflict_do_nothing(
            index_elements=["event_id"],
            index_where=FunnelEventModel.event_id.is_not(None),
        )

    async with AsyncSessionLocal() as session:
        result = await session.execute(stmt)
        await session.commit()
        # rowcount is 1 when inserted, 0 when ON CONFLICT skipped it.
        rowcount = getattr(result, "rowcount", None)
        return rowcount is None or rowcount > 0


@celery_app.task(
    name="tasks.ingest_funnel_event",
    queue="analytics",
    acks_late=True,
    reject_on_worker_lost=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_backoff_max=60,
    max_retries=5,
    retry_jitter=True,
)
def ingest_funnel_event(event: dict[str, Any]) -> dict[str, Any]:
    """Persist one funnel event. Idempotent on ``event_id``.

    ``event`` payload shape (all keys present, string for UUIDs / iso
    timestamps so the JSON serializer is happy):

    .. code-block:: python

        {
            "event_id":  "<uuid | null>",
            "tenant_id": "<uuid>",
            "store_id":  "<uuid>",
            "customer_id": "<uuid | null>",
            "session_fingerprint": "<str | null>",
            "step": "page_view",
            "step_data": {...},
        }
    """
    try:
        inserted = asyncio.run(_insert_funnel_event(event))
    except Exception as exc:
        logger.exception(
            "ingest_funnel_event failed for event_id=%s: %s",
            event.get("event_id"),
            exc,
        )
        raise
    return {
        "event_id": event.get("event_id"),
        "inserted": bool(inserted),
    }
