"""Dead-letter helper for Celery tasks (Phase 5.3).

Tasks that have exhausted their max_retries call `record_dead_letter`
in the on_failure / except branch. The helper:

  - Inserts a row into celery_dead_letters with the failing args
  - Tags the entry with tenant_id / store_id when derivable from
    the task kwargs (operators see "which store was this for")
  - Truncates last_error to bound row size

Manual retry uses the same Celery app to re-enqueue with the stored
task_name + args + kwargs. We don't try to deserialize the original
Celery message envelope; the task name is stable across deploys (it's
the @celery_app.task `name=` parameter, not the function path) so
re-enqueue is straightforward.

Why this is a service module and not a base task class:
  Many tasks already have bespoke on_failure handlers (some retry on
  certain errors, some don't). A helper they call from inside their
  own handler keeps the integration shallow — no per-task plumbing
  beyond two lines.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger(__name__)

# Cap for last_error before we write to the DB. Tracebacks past 4000
# chars almost never aid debugging — the structured log carries the
# full version.
MAX_ERROR_BYTES = 4000


async def record_dead_letter(
    *,
    task_name: str,
    args: list[Any],
    kwargs: dict[str, Any],
    error: BaseException | str,
    attempts: int,
    queue: str | None = None,
    tenant_id: UUID | None = None,
    store_id: UUID | None = None,
) -> UUID:
    """Persist a Celery task failure to the DLQ.

    Returns the new entry's UUID so the caller can include it in the
    task's failure log line for cross-reference.

    Args:
        task_name: The Celery task `name=` (e.g. "tasks.send_order_email").
                   Stable across deploys; survives function relocation.
        args / kwargs: Original call signature so manual retry replays
                      with the same inputs.
        error: The exception (or string) the task raised after
               retries exhausted.
        attempts: Total number of attempts made (initial + retries).
        queue: Optional — name of the Celery queue the task ran on.
               Replay defaults to the same queue.
        tenant_id / store_id: Pulled from kwargs when the task carries
                              them; lets the hub UI scope the DLQ list
                              to a single store.
    """
    # Lazy imports — DLQ is rare-path, no need to cost startup time.
    from src.core.entities.dead_letter import DeadLetterEntry, DeadLetterStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.dead_letter import (
        DeadLetterEntryModel,
    )

    err_text: str
    if isinstance(error, BaseException):
        err_text = f"{type(error).__name__}: {error}"
    else:
        err_text = str(error)
    if len(err_text) > MAX_ERROR_BYTES:
        err_text = err_text[:MAX_ERROR_BYTES] + "…[truncated]"

    now = datetime.now(UTC)
    new_id = uuid4()
    entry = DeadLetterEntry(
        id=new_id,
        tenant_id=tenant_id,
        store_id=store_id,
        task_name=task_name,
        args=list(args),
        kwargs=dict(kwargs),
        queue=queue,
        status=DeadLetterStatus.PENDING,
        last_error=err_text,
        attempts=attempts,
        first_failed_at=now,
        last_failed_at=now,
    )

    async with AsyncSessionLocal() as session:
        row = DeadLetterEntryModel(
            id=entry.id,
            tenant_id=entry.tenant_id,
            store_id=entry.store_id,
            task_name=entry.task_name,
            args=entry.args,
            kwargs=entry.kwargs,
            queue=entry.queue,
            status=entry.status,
            last_error=entry.last_error,
            attempts=entry.attempts,
            first_failed_at=entry.first_failed_at,
            last_failed_at=entry.last_failed_at,
        )
        session.add(row)
        try:
            await session.commit()
        except Exception:
            # We never want DLQ insert failures to mask the original
            # task failure that prompted the recording. Log + return
            # the would-be id so the caller can still log it.
            logger.exception("dlq_insert_failed", extra={"task_name": task_name})

    logger.warning(
        "celery_task_dead_lettered",
        extra={
            "task_name": task_name,
            "dead_letter_id": str(new_id),
            "attempts": attempts,
            "tenant_id": str(tenant_id) if tenant_id else None,
        },
    )
    return new_id


async def replay_dead_letter(entry_id: UUID, user_id: UUID | None = None) -> str:
    """Re-enqueue a DLQ entry's task. Returns the new Celery task id.

    Updates the row's `status` → "retried" + records who clicked
    Retry. Does NOT delete the row — operators want a complete
    history of "I retried this; here's the new task; it succeeded".

    Raises ValueError when the row doesn't exist or is already in
    a non-retryable state.
    """
    from datetime import UTC
    from datetime import datetime as _dt

    from src.core.entities.dead_letter import DeadLetterStatus
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.dead_letter import (
        DeadLetterEntryModel,
    )
    from src.infrastructure.messaging.celery_app import celery_app

    async with AsyncSessionLocal() as session:
        row = await session.get(DeadLetterEntryModel, entry_id)
        if not row:
            raise ValueError(f"Dead-letter entry {entry_id} not found")
        if row.status != DeadLetterStatus.PENDING:
            raise ValueError(
                f"Dead-letter entry {entry_id} is not pending (status={row.status})"
            )

        # Re-enqueue. send_task accepts the registered name without
        # needing to import the function — important because the
        # caller may not have access to the task module.
        async_result = celery_app.send_task(
            row.task_name,
            args=row.args or [],
            kwargs=row.kwargs or {},
            queue=row.queue,
        )
        row.status = DeadLetterStatus.RETRIED
        row.retried_at = _dt.now(UTC)
        row.retried_by_user_id = user_id
        row.retry_task_id = async_result.id
        await session.commit()

    logger.info(
        "dead_letter_replayed",
        extra={
            "dead_letter_id": str(entry_id),
            "new_task_id": async_result.id,
            "task_name": row.task_name,
        },
    )
    return async_result.id
