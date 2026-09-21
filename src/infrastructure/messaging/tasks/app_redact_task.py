"""``store.redact``: tell a Partner App to delete a store's data, 48 hours
after the merchant uninstalled it (PDPL 151/2020; plan 03 § 6.5).

Queued with a countdown at uninstall. If the merchant reinstalled in the
meantime the app is back in use, so nothing is sent.
"""

import asyncio

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def send_store_redact(app_id: str, store_id: str) -> int:
    from uuid import UUID

    from sqlalchemy import select

    from src.application.services.app_webhooks import deliver_app_event
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.public.app import (
        AppInstallationModel,
        AppModel,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        reinstalled = await session.scalar(
            select(AppInstallationModel.id).where(
                AppInstallationModel.app_id == UUID(app_id),
                AppInstallationModel.store_id == UUID(store_id),
            )
        )
        if reinstalled:
            return 0
        app = await session.get(AppModel, UUID(app_id))
        if app is None:
            return 0
        return await deliver_app_event(session, app, UUID(store_id), "store.redact", {})


@celery_app.task(name="tasks.app_store_redact", bind=True, max_retries=3)
def app_store_redact_task(self, app_id: str, store_id: str):
    try:
        return _run_async(send_store_redact(app_id, store_id))
    except Exception as exc:
        logger.exception("app_store_redact_failed")
        raise self.retry(exc=exc, countdown=3600)
