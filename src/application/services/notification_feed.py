"""Write side of the merchant notification feed.

Every producer (EventBus handlers, Celery tasks) goes through
``emit_notification`` so the rules live in one place:

* category must be one of ``NOTIFICATION_CATEGORIES``;
* a store can mute whole categories via
  ``store.settings.notification_center.muted_categories``;
* ``dedupe_key`` makes replays idempotent.

The hub renders the bilingual title from ``kind`` + ``data`` — nothing
here is user-visible copy.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.logging import get_logger
from src.infrastructure.database.models.tenant.merchant_notification import (
    NOTIFICATION_CATEGORIES,
    MerchantNotificationModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.merchant_notification_repository import (
    MerchantNotificationRepository,
)

logger = get_logger(__name__)

SETTINGS_KEY = "notification_center"


def muted_categories(store_settings: dict | None) -> set[str]:
    prefs = (store_settings or {}).get(SETTINGS_KEY) or {}
    muted = prefs.get("muted_categories") or []
    return {c for c in muted if isinstance(c, str)}


async def emit_notification(
    session: AsyncSession,
    *,
    store_id: UUID,
    category: str,
    kind: str,
    data: dict[str, Any] | None = None,
    link: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    important: bool = False,
    dedupe_key: str | None = None,
    tenant_id: UUID | None = None,
) -> bool:
    """Insert one feed row inside the caller's transaction.

    Returns True when a row was written, False when skipped (unknown
    store, muted category, duplicate dedupe_key).
    """
    if category not in NOTIFICATION_CATEGORIES:
        raise ValueError(f"unknown notification category: {category}")

    row = await session.execute(
        select(StoreModel.tenant_id, StoreModel.settings).where(
            StoreModel.id == store_id
        )
    )
    found = row.first()
    if found is None:
        logger.warning(
            "notification_skipped_no_store", store_id=str(store_id), kind=kind
        )
        return False
    resolved_tenant, store_settings = found
    if category in muted_categories(store_settings):
        return False

    repo = MerchantNotificationRepository(session)
    return await repo.create(
        MerchantNotificationModel(
            tenant_id=tenant_id or resolved_tenant,
            store_id=store_id,
            category=category,
            kind=kind,
            data=data or {},
            link=link,
            entity_type=entity_type,
            entity_id=entity_id,
            is_important=important,
            dedupe_key=dedupe_key,
        )
    )


async def emit_notification_standalone(**kwargs: Any) -> bool:
    """``emit_notification`` in its own committed session — for Celery
    tasks and handlers that don't already hold a transaction."""
    from src.infrastructure.database.connection import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session, session.begin():
            return await emit_notification(session, **kwargs)
    except Exception:
        # The feed is best-effort: never let it break the producer.
        logger.exception("notification_emit_failed", kind=kwargs.get("kind"))
        return False
