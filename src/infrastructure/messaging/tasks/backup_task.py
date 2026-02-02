"""Celery task for automated database backups."""

from __future__ import annotations

import logging

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="tasks.backup_database",
    bind=True,
    max_retries=2,
    default_retry_delay=300,  # 5 min between retries
    acks_late=True,
)
def backup_database(self) -> dict:
    """Run pg_dump and upload the compressed dump to Cloudflare R2.

    Automatically prunes backups older than the configured retention period.
    Retries up to 2 times on transient failures (network, R2 unavailability).
    """
    # Import inside the task to avoid circular imports at worker boot and to
    # ensure settings are read fresh each time (no stale lru_cache).
    from scripts.backup_db import create_backup  # noqa: WPS433

    try:
        logger.info("Starting scheduled database backup …")
        object_key = create_backup(prune=True)
        logger.info("Scheduled backup complete: %s", object_key)
        return {"status": "ok", "key": object_key}
    except Exception as exc:
        logger.exception("Backup task failed, retrying …")
        raise self.retry(exc=exc)
