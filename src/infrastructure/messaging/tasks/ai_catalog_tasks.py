"""Lamaa (``ai-catalog``) background work: bulk runs, their apply, the sweep
that resumes a run whose worker message was lost, and the daily cleanup.

A bulk run gets two workers; each claims one queued item at a time
(``FOR UPDATE SKIP LOCKED``), writes it, and re-enqueues itself, so at most
two provider calls are in flight per run. Every task checks the install
gate first: an uninstall, a disabled install or the kill switch stops a run
at its next item. Prod Redis is ``allkeys-lru`` and can evict a message, so
the sweep re-finds queued work every minute.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from src.application.services import ai_catalog_actions as actions
from src.application.services import ai_catalog_service as rules
from src.application.services.app_install_gate import is_live
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.messaging.celery_app import celery_app
from src.infrastructure.messaging.tasks.order_tracking_tasks import _run_async
from src.infrastructure.repositories import ai_catalog_repository as repo
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.rls import enable_rls_bypass

logger = get_logger(__name__)

WORKERS = 2


async def _commit(db: Any) -> None:
    await db.commit()
    await enable_rls_bypass(db)


def start_workers(run_id: UUID, store_id: UUID) -> None:
    for _ in range(WORKERS):
        work_task.delay(str(run_id), str(store_id))


async def work(run_id: UUID, store_id: UUID) -> bool:
    """Write one queued item of the run. ``True`` when it took one, so the
    caller enqueues the next."""
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        run = await repo.run(db, store_id, run_id)
        if run is None or run.status != "running":
            return False
        if not await is_live(db, store_id, rules.SLUG):
            run.status = "cancelled"
            await _commit(db)
            return False
        item = await repo.claim(db, run_id)
        if item is None:
            counts = await repo.run_counts(db, run_id)
            if not counts.get("queued") and not counts.get("running"):
                run.status = "review"
            await _commit(db)
            return False
        await _commit(db)
        item_id = item.id
        store = await StoreRepository(db).get_by_id(store_id)
        try:
            await actions.generate(
                db,
                store,
                item.product_id,
                list(run.fields),
                commit=_commit,
                item=item,
                reserved=True,
            )
        except Exception as exc:  # noqa: BLE001 — one item never stops the run
            # A provider failure is already marked failed and refunded;
            # anything else (the product is gone, a crash) is done here.
            await db.rollback()
            await enable_rls_bypass(db)
            row = await repo.suggestion(db, store_id, item_id)
            if row is not None and row.status != "failed":
                row.status = "failed"
                row.error_code = (
                    exc.code if isinstance(exc, actions.ActionError) else "busy"
                )
                await repo.refund(db, store_id)
                await _commit(db)
            if not isinstance(exc, actions.ActionError):
                logger.exception("ai_catalog_bulk_item_crashed")
        return True


@celery_app.task(name="apps.ai_catalog.work")
def work_task(run_id: str, store_id: str) -> None:
    if _run_async(work(UUID(run_id), UUID(store_id))):
        work_task.delay(run_id, store_id)


async def apply_run(
    run_id: UUID,
    store_id: UUID,
    suggestion_ids: list[UUID] | None,
    user_id: UUID | None,
) -> dict:
    """Apply a reviewed run's ready items (or the ones picked), one product
    at a time; one product's failure never stops the run."""
    done = failed = 0
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        run = await repo.run(db, store_id, run_id)
        if run is None:
            return {"done": 0, "failed": 0}
        if not await is_live(db, store_id, rules.SLUG):
            run.status = "cancelled"  # never left "applying"
            await _commit(db)
            return {"done": 0, "failed": 0}
        store = await StoreRepository(db).get_by_id(store_id)
        picked = set(suggestion_ids) if suggestion_ids else None
        fill_empty = run.mode == "fill_empty"
        # Ids, not rows: a rollback below expires every loaded row.
        ids = [
            s.id
            for s in await repo.run_items(db, run_id)
            if s.status == "ready" and (picked is None or s.id in picked)
        ]
        try:
            for item_id in ids:
                try:
                    await actions.apply(
                        db,
                        store,
                        item_id,
                        [],
                        user_id=user_id,
                        commit=_commit,
                        fill_empty=fill_empty,
                    )
                    done += 1
                except Exception as exc:  # noqa: BLE001 — one product never stops the run
                    await db.rollback()
                    await enable_rls_bypass(db)
                    failed += 1
                    code = (
                        exc.code
                        if isinstance(exc, actions.ActionError)
                        else type(exc).__name__
                    )
                    logger.info("ai_catalog_bulk_item_skipped", code=code)
        finally:
            # Never left "applying": the run always reaches a final state.
            await db.rollback()
            await enable_rls_bypass(db)
            run = await repo.run(db, store_id, run_id)
            if run is not None:  # an uninstall purge may have removed it
                run.status = "done"
                await _commit(db)
    return {"done": done, "failed": failed}


@celery_app.task(name="apps.ai_catalog.apply")
def apply_task(
    run_id: str, store_id: str, suggestion_ids: list[str] | None, user_id: str | None
) -> dict:
    return _run_async(
        apply_run(
            UUID(run_id),
            UUID(store_id),
            [UUID(i) for i in suggestion_ids] if suggestion_ids else None,
            UUID(user_id) if user_id else None,
        )
    )


async def sweep() -> int:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        stalled = await repo.stalled_runs(db)
        await db.commit()
    for run_id, store_id in stalled:
        work_task.delay(str(run_id), str(store_id))
    return len(stalled)


@celery_app.task(name="apps.ai_catalog.sweep")
def sweep_task() -> int:
    return _run_async(sweep())


async def cleanup() -> int:
    async with AsyncSessionLocal() as db:
        await enable_rls_bypass(db)
        removed = await repo.cleanup(db)
        await db.commit()
    return removed


@celery_app.task(name="apps.ai_catalog.cleanup")
def cleanup_task() -> int:
    return _run_async(cleanup())
