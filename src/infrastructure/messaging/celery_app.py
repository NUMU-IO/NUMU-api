"""Celery application configuration."""

from celery import Celery
from celery.schedules import crontab
from kombu import Queue

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
        "src.infrastructure.messaging.tasks.fraud_tasks",
        "src.infrastructure.messaging.tasks.risk_scoring_tasks",
        "src.infrastructure.messaging.tasks.abandoned_cart_tasks",
        "src.infrastructure.messaging.tasks.health_score_tasks",
        "src.infrastructure.messaging.tasks.social_tasks",
    ],
    # Queue definitions
    task_queues=(
        Queue("default"),
        Queue("images"),
    ),
    task_default_queue="default",
    # Route image tasks to dedicated queue
    task_routes={
        "tasks.process_product_image": {"queue": "images"},
        "tasks.process_bulk_product_images": {"queue": "images"},
    },
    # Redis broker stability
    broker_transport_options={
        "visibility_timeout": 3600,
    },
    broker_connection_retry_on_startup=True,
)

# Beat schedule for periodic tasks
celery_app.conf.beat_schedule = {
    "daily-database-backup": {
        "task": "tasks.backup_database",
        "schedule": crontab(hour=3, minute=0),  # Every day at 03:00 UTC
    },
    "process-slack-alert-queue": {
        "task": "tasks.process_slack_alert_queue",
        "schedule": 30.0,  # Every 30 seconds
        "kwargs": {"max_alerts": 10},
    },
    "retry-pending-webhook-deliveries": {
        "task": "tasks.retry_pending_webhook_deliveries",
        "schedule": 15.0,  # Every 15 seconds (shortest retry delay is 10s)
    },
    "daily-payment-reconciliation": {
        "task": "tasks.daily_payment_reconciliation",
        "schedule": crontab(hour=2, minute=0),  # Every day at 02:00 UTC
    },
    "sync-shipment-statuses": {
        "task": "tasks.sync_shipment_statuses",
        "schedule": crontab(minute="*/30"),  # Every 30 minutes
    },
    "daily-cod-reconciliation": {
        "task": "tasks.daily_cod_reconciliation",
        "schedule": crontab(hour=3, minute=30),  # 03:30 UTC (after payment recon)
    },
    "detect-abandoned-carts": {
        "task": "tasks.detect_abandoned_carts",
        "schedule": crontab(minute="*/30"),  # Every 30 minutes
    },
    "daily-health-score-calculation": {
        "task": "tasks.calculate_health_scores",
        "schedule": crontab(hour=4, minute=0),  # Daily at 4 AM UTC
    },
}
