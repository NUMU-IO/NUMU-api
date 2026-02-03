"""Celery application configuration."""

from celery import Celery
from celery.schedules import crontab

from src.config import settings

# Create Celery app
celery_app = Celery(
    "numu",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

# Celery configuration
celery_app.conf.update(
    # Task settings
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    
    # Task execution
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    
    # Results
    result_expires=3600,  # 1 hour
    
    # Worker settings
    worker_prefetch_multiplier=1,
    worker_concurrency=4,
    
    # Task autodiscovery - adjust paths as needed
    imports=[
        "src.infrastructure.messaging.tasks",
    ],
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "daily-database-backup": {
        "task": "tasks.backup_database",
        "schedule": crontab(hour=3, minute=0),  # Every day at 03:00 UTC
    },
    "process-slack-alert-queue": {
        "task": "tasks.process_slack_alert_queue",
        "schedule": 5.0,  # Every 5 seconds
        "kwargs": {"max_alerts": 5},
    },
}
