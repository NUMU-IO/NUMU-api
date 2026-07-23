"""Scheduled-article publisher.

Every minute, promotes `scheduled` articles whose `scheduled_at` has
passed to `published`. Runs with NO tenant context on purpose — it must
promote every store's due articles. Failure recovery is the schedule
itself: the task is idempotent (only rows still `scheduled` match), so an
article that fails one tick is retried on the next, and one bad article
never aborts the batch.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from src.core.entities.blog import ArticleStatus
from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


def _run_async(coro):
    return asyncio.run(coro)


async def _publish_due() -> dict:
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.blog_repository import (
        ArticleRepository,
        BlogRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository

    now = datetime.now(UTC)
    published = 0
    failed = 0
    revalidations: list[tuple[str, str, str, str]] = []

    async with AsyncSessionLocal() as session:
        article_repo = ArticleRepository(session)
        blog_repo = BlogRepository(session)
        store_repo = StoreRepository(session)

        due = await article_repo.due_scheduled(now)
        for article in due:
            try:
                article.status = ArticleStatus.PUBLISHED
                # The merchant's intended time, not the tick time — keeps
                # ordering stable even when the beat lags.
                article.published_at = article.scheduled_at or now
                article.scheduled_at = None
                await article_repo.update(article)
                published += 1

                blog = await blog_repo.get_by_id(article.blog_id)
                store = await store_repo.get_by_id(article.store_id)
                subdomain = getattr(store, "subdomain", None) if store else None
                if blog and subdomain:
                    revalidations.append((
                        subdomain,
                        str(article.store_id),
                        blog.handle,
                        article.handle,
                    ))
            except Exception:
                failed += 1
                logger.exception(
                    "article_scheduled_publish_failed",
                    extra={"article_id": str(article.id)},
                )
        await session.commit()

    # Cache busting AFTER commit, best-effort — a failed webhook must not
    # roll back a publish; the ISR window self-heals.
    for subdomain, store_id, blog_handle, article_handle in revalidations:
        try:
            from src.infrastructure.external_services.nextjs_revalidation import (
                revalidate_on_blog_change,
            )

            await revalidate_on_blog_change(
                subdomain=subdomain,
                store_id=store_id,
                blog_handle=blog_handle,
                article_handle=article_handle,
            )
        except Exception:
            logger.warning(
                "article_publish_revalidation_failed",
                extra={"store_id": store_id, "article": article_handle},
            )

    return {"due": len(due) if due else 0, "published": published, "failed": failed}


@celery_app.task(name="tasks.publish_due_articles")
def publish_due_articles() -> dict:
    """Promote due scheduled articles to published (beat: every 60s)."""
    result = _run_async(_publish_due())
    if result["published"] or result["failed"]:
        logger.info("scheduled_articles_published", extra=result)
    return result
