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
        "src.infrastructure.messaging.tasks.whatsapp_nudge_task",
        "src.infrastructure.messaging.tasks.trust_network_maintenance",
        "src.infrastructure.messaging.tasks.abandoned_cart_tasks",
        "src.infrastructure.messaging.tasks.health_score_tasks",
        "src.infrastructure.messaging.tasks.analytics_rollup_tasks",
        "src.infrastructure.messaging.tasks.social_tasks",
        # Stream 1.5 + 4.6: Demo + trial lifecycle sweepers
        "src.infrastructure.messaging.tasks.demo_cleanup_task",
        "src.infrastructure.messaging.tasks.trial_expiry_task",
        "src.infrastructure.messaging.tasks.read_only_purge_task",
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
    "daily-analytics-rollup": {
        "task": "tasks.calculate_analytics_rollups",
        "schedule": crontab(hour=3, minute=30),  # Daily at 3:30 AM UTC
    },
    "daily-health-score-calculation": {
        "task": "tasks.calculate_health_scores",
        "schedule": crontab(hour=4, minute=0),  # Daily at 4 AM UTC
    },
    "daily-ai-insights": {
        "task": "tasks.generate_ai_insights",
        "schedule": crontab(
            hour=5, minute=0
        ),  # Daily at 5 AM UTC (after rollup + health)
    },
    # Trust Network maintenance
    "retry-stuck-preliminary-scores": {
        "task": "tasks.retry_stuck_preliminary_scores",
        "schedule": 300.0,  # Every 5 minutes
        "kwargs": {"max_age_minutes": 5, "batch_size": 50},
    },
    "cleanup-expired-payment-links": {
        "task": "tasks.cleanup_expired_payment_links",
        "schedule": crontab(hour=4, minute=0),  # Daily at 04:00 UTC
        "kwargs": {"expired_hours": 48},
    },
    # Onboarding nudge emails
    "send-inactive-merchant-nudges": {
        "task": "tasks.send_inactive_merchant_nudges",
        "schedule": crontab(hour=9, minute=0),  # Daily at 09:00 UTC (11 AM Cairo)
    },
    "send-trial-expiry-warnings": {
        "task": "tasks.send_trial_expiry_warnings",
        "schedule": crontab(hour=8, minute=0),  # Daily at 08:00 UTC (10 AM Cairo)
    },
    # ─── Stream 1.5: Demo cleanup (every 2 hours) ────────────────────
    "cleanup-expired-demo-tenants": {
        "task": "tasks.cleanup_expired_demo_tenants",
        "schedule": crontab(minute=0, hour="*/2"),  # Every 2 hours
    },
    # ─── Stream 4.6: Trial expiry sweep (hourly) ─────────────────────
    "expire-trials": {
        "task": "tasks.expire_trials",
        "schedule": crontab(minute=15),  # Every hour at :15
    },
    # ─── Stream 4.6: Read-only purge (every 6 hours) ─────────────────
    "purge-read-only-tenants": {
        "task": "tasks.purge_read_only_tenants",
        "schedule": crontab(minute=30, hour="*/6"),  # Every 6 hours at :30
    },
}
