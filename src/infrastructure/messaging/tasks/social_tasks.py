"""Celery tasks for social media import."""

import asyncio
import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.import_social_posts",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    acks_late=True,
    reject_on_worker_lost=True,
    soft_time_limit=300,
    time_limit=360,
)
def import_social_posts_task(
    self,
    connection_id: str,
    platform_post_ids: list[str],
    store_id: str,
    tenant_id: str | None = None,
) -> dict:
    """Async bulk import of social posts as draft products.

    Used for large bulk imports (>10 posts) to avoid HTTP timeout.
    """
    logger.info(
        "Starting bulk social import: connection=%s, posts=%d",
        connection_id,
        len(platform_post_ids),
    )

    try:
        result = asyncio.get_event_loop().run_until_complete(
            _run_import(connection_id, platform_post_ids, store_id, tenant_id)
        )
        return result
    except Exception as exc:
        logger.error("Bulk social import failed: %s", exc)
        if self.request.retries < self.max_retries:
            raise self.retry(exc=exc)
        return {
            "status": "failed",
            "error": str(exc),
            "imported": 0,
            "product_ids": [],
        }


async def _run_import(
    connection_id: str,
    platform_post_ids: list[str],
    store_id: str,
    tenant_id: str | None,
) -> dict:
    """Run the async import logic inside the Celery task."""
    from uuid import UUID

    from src.infrastructure.database.connection import (
        AsyncSessionLocal,
        set_tenant_id,
    )
    from src.infrastructure.repositories.product_repository import ProductRepository
    from src.infrastructure.repositories.social_connection_repository import (
        SocialConnectionRepository,
    )
    from src.infrastructure.repositories.social_post_repository import (
        SocialPostRepository,
    )

    if tenant_id:
        set_tenant_id(tenant_id)

    async with AsyncSessionLocal() as session:
        conn_repo = SocialConnectionRepository(session)
        post_repo = SocialPostRepository(session)
        product_repo = ProductRepository(session)

        from src.application.use_cases.social.import_posts import (
            ImportSocialPostsUseCase,
        )

        use_case = ImportSocialPostsUseCase(conn_repo, post_repo, product_repo)
        product_ids, errors = await use_case.execute(
            UUID(connection_id), platform_post_ids
        )

        await session.commit()

    return {
        "status": "completed",
        "imported": len(product_ids),
        "product_ids": [str(pid) for pid in product_ids],
        "errors": errors,
    }
