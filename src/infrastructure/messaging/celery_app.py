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
        # backend-030 / US3 — scheduled-send dispatcher (every 60s).
        "src.infrastructure.messaging.tasks.whatsapp_scheduled_send_dispatcher",
        # backend-030 / US5 — PENDING template polling sync (every 15 min).
        "src.infrastructure.messaging.tasks.whatsapp_template_poll_task",
        "src.infrastructure.messaging.tasks.trust_network_maintenance",
        "src.infrastructure.messaging.tasks.abandoned_cart_tasks",
        "src.infrastructure.messaging.tasks.health_score_tasks",
        "src.infrastructure.messaging.tasks.analytics_rollup_tasks",
        "src.infrastructure.messaging.tasks.social_tasks",
        # Stream 7.1: Onboarding abandoned nudges
        "src.infrastructure.messaging.tasks.onboarding_nudge_task",
        # Stream 1.5 + 4.6: Demo + trial lifecycle sweepers
        "src.infrastructure.messaging.tasks.demo_cleanup_task",
        "src.infrastructure.messaging.tasks.trial_expiry_task",
        "src.infrastructure.messaging.tasks.read_only_purge_task",
        # Staff permission system
        "src.infrastructure.messaging.tasks.detect_suspicious_activity",
        "src.infrastructure.messaging.tasks.expire_temporary_grants",
        # Feature 001: Omnichannel inbox
        "src.infrastructure.messaging.tasks.omnichannel_tasks",
        # InstaPay — auto-cancel expired pending-payment orders
        "src.infrastructure.messaging.tasks.instapay_expiry_task",
        # COD deposit — auto-cancel orders past deposit_expires_at
        "src.infrastructure.messaging.tasks.deposit_expiry_task",
        # COD trust network — auto-flag stale SHIPPED orders as RETURNED
        # so manual-ship merchants feed RTO signals into the network.
        "src.infrastructure.messaging.tasks.cod_auto_rto_task",
        # Phase C — keep HF OCR Spaces warm for stores that opt in.
        "src.infrastructure.messaging.tasks.warm_hf_vision_spaces",
        # Analytics retention — drops funnel/page-view rows past TTL.
        "src.infrastructure.messaging.tasks.analytics_retention_task",
        # Theme builds + marketplace
        "src.infrastructure.messaging.tasks.theme_build_tasks",
        "src.infrastructure.messaging.tasks.theme_upload_tasks",
        "src.infrastructure.messaging.tasks.theme_marketplace_tasks",
        # Phase 3.5 — back-in-stock subscription sweep + email dispatch.
        "src.infrastructure.messaging.tasks.back_in_stock_tasks",
        # Phase 4.4 — smart-collection membership sweep.
        "src.infrastructure.messaging.tasks.smart_collection_tasks",
        # Phase 5.8 — beat scheduler heartbeat for /health/detailed.
        "src.infrastructure.messaging.tasks.beat_heartbeat",
        # backend-005 — Paymob recurring subscription renewals.
        "src.infrastructure.messaging.tasks.subscription_renewal_task",
        # backend-017 — daily Shopify-side verification overage relay.
        "src.infrastructure.messaging.tasks.usage_overage_task",
        # backend-021 — RecoveryFlow cadence Celery worker + Shopify outbox.
        "src.infrastructure.messaging.tasks.recovery_tasks",
        # backend-020 — Shopify Flow trigger emitter (idempotent per
        # (store, dedup_key, trigger_handle)).
        "src.infrastructure.messaging.tasks.flow_trigger_tasks",
        # backend-022 / spec 010 — daily kill-switch evaluator for the
        # trust-driven auto-approve toggle.
        "src.infrastructure.messaging.tasks.trust_kill_switch_tasks",
        # backend-023 — nightly per-store courier stats rollup.
        "src.infrastructure.messaging.tasks.courier_stats_tasks",
        # Meta Conversions — per-event fan-out + orphan-purchase sweep
        "src.infrastructure.messaging.tasks.meta_capi",
        # offers-v2 — promotion lifecycle + analytics maintenance.
        "src.infrastructure.messaging.tasks.promotion_tasks",
        # Step 09 — async funnel-event ingest.
        "src.infrastructure.messaging.tasks.analytics_ingest_task",
        # Phase 8.6 — marketing campaign runner (Send-Now + scheduled sweep).
        "src.infrastructure.messaging.tasks.marketing_campaign_tasks",
        # Feature 002 — marketing campaign attribution backfill.
        "src.infrastructure.messaging.tasks.marketing_tasks",
    ],
    # Queue definitions
    task_queues=(
        Queue("default"),
        Queue("images"),
        Queue("messaging"),
        Queue("catalog"),
        # Step 09 — funnel-event ingest off the request path. Dedicated
        # queue so a worker pool can be scaled independently and a
        # backlog here doesn't starve transactional tasks.
        Queue("analytics"),
    ),
    task_default_queue="default",
    # Route image tasks to dedicated queue
    task_routes={
        "tasks.process_product_image": {"queue": "images"},
        "tasks.process_bulk_product_images": {"queue": "images"},
        "tasks.ingest_funnel_event": {"queue": "analytics"},
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
    # Phase 8.6 — marketing campaign sweep. Promotes SCHEDULED→SENDING
    # for due campaigns + rescues orphaned SENDING (Send-Now invocations
    # that failed to enqueue). 60s cadence is fine because the
    # send-now endpoint enqueues directly; the sweep is the backstop.
    "process-scheduled-marketing-campaigns": {
        "task": "marketing.campaign.process_scheduled",
        "schedule": 60.0,
    },
    # Phase 3.5 — sweep pending back-in-stock subscriptions and notify
    # subscribers of products that came back in stock since the last
    # sweep. Hourly cadence matches Shopify's documented behavior;
    # smaller intervals risk emailing during transient stock flips
    # caused by refund-window decrements.
    "back-in-stock-sweep": {
        "task": "tasks.product_subscription_sweep",
        "schedule": crontab(minute=15),  # Hourly at :15 past the hour
    },
    # Phase 5.8 — beat heartbeat. Every minute, write the current
    # unix timestamp to Redis so /health/detailed can flag stale beat
    # processes (workers up but scheduler stuck). The task is tiny
    # and idempotent.
    "beat-heartbeat": {
        "task": "tasks.beat_heartbeat",
        "schedule": 60.0,
    },
    # Phase 4.4 — smart-collection membership recompute. Hourly at :30
    # so it doesn't pile onto the back-in-stock sweep at :15. Inline
    # invalidation (on every product save) is too expensive at scale;
    # hourly batch matches Shopify's documented cadence.
    "smart-collection-sweep": {
        "task": "tasks.smart_collection_sweep",
        "schedule": crontab(minute=30),
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
    # Marketplace theme build watchdog — fail orphan builds whose worker
    # died mid-build (R2 outage, OOM, etc.) so versions don't sit in
    # `building` forever. The task itself is idempotent.
    "theme-marketplace-watchdog": {
        "task": "theme_marketplace_watchdog",
        "schedule": 300.0,  # Every 5 minutes
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
    # ─── Stream 7.1: Onboarding abandoned nudges (every 6 hours) ─────
    "send-onboarding-nudges": {
        "task": "tasks.send_onboarding_nudges",
        "schedule": crontab(minute=45, hour="*/6"),  # Every 6 hours at :45
    },
    # ─── Staff permission system ────────────────────────────────────────────
    "expire-temporary-grants": {
        "task": "tasks.expire_temporary_grants",
        "schedule": crontab(minute="*/15"),  # Every 15 minutes
    },
    "expire-access-requests": {
        "task": "tasks.expire_access_requests",
        "schedule": crontab(hour=1, minute=0),  # Daily at 01:00 UTC
    },
    "cleanup-staff-sessions": {
        "task": "tasks.cleanup_staff_sessions",
        "schedule": crontab(hour=2, minute=0),  # Daily at 02:00 UTC
    },
    "detect-suspicious-activity": {
        "task": "tasks.detect_suspicious_activity",
        "schedule": crontab(minute="*/30"),  # Every 30 minutes
    },
    "compute-staff-risk-scores": {
        "task": "tasks.compute_staff_risk_scores",
        "schedule": crontab(hour=4, minute=30),  # Daily at 04:30 UTC
    },
    # ─── InstaPay: cancel stale pending-payment orders every minute ──
    "expire-instapay-orders": {
        "task": "tasks.expire_instapay_orders",
        "schedule": 60.0,  # Every 60s — matches 30-min window granularity
    },
    # ─── COD deposit: cancel orders past deposit_expires_at ──
    "expire-pending-deposit-orders": {
        "task": "tasks.expire_pending_deposit_orders",
        "schedule": 60.0,  # Every 60s — finest granularity the merchant can set is 5 min
    },
    # ─── COD trust: auto-flag stale SHIPPED orders as RETURNED ─────────
    # Daily at 03:00 UTC (~05:00 Cairo). Manual-ship merchants who
    # forget to mark outcomes still feed RTO signals into the network.
    "auto-rto-stale-shipped-orders": {
        "task": "tasks.auto_rto_stale_shipped_orders",
        "schedule": crontab(hour=3, minute=0),
    },
    # ─── backend-023: Courier delivery stats nightly rollup ──────────
    # Aggregates the trailing 30-day shipment outcomes per (store,
    # carrier) for the Courier Intelligence dashboard (spec 013).
    # Daily at 03:15 UTC — after the auto-RTO sweeper, so terminal
    # statuses are settled before we compute rates.
    "refresh-courier-stats-all-stores": {
        "task": "tasks.courier_stats.refresh_all",
        "schedule": crontab(hour=3, minute=15),
    },
    # ─── backend-022 / spec 010 CL-002: trust auto-approve kill-switch ─
    # Evaluates every store with `auto_approve_on_trust_enabled=true`
    # against the trailing 30-day RTO rate; disables the toggle if the
    # cohort meets the ≥20-sample minimum AND >5% RTO threshold.
    # Daily at 04:15 UTC (after courier_stats so the same shipment
    # rows are seen consistently).
    "evaluate-trust-kill-switch": {
        "task": "tasks.trust_kill_switch.evaluate_all_stores",
        "schedule": crontab(hour=4, minute=15),
    },
    # ─── Phase C: keep HF OCR Spaces warm for opted-in stores ──────────
    # Free HF Spaces sleep on inactivity → 30–60s cold-start. Pinging
    # every 10 minutes is well under HF's idle threshold.
    "warm-hf-vision-spaces": {
        "task": "tasks.warm_hf_vision_spaces",
        "schedule": 600.0,  # 10 minutes
    },
    # ─── Analytics retention — drop funnel/page-view rows older than
    # the configured window so the tables don't grow unboundedly. Daily
    # at 02:30 UTC, before the 03:30 rollup so the rollup never sees
    # rows that are about to be deleted.
    "purge-analytics-events": {
        "task": "tasks.purge_analytics_events",
        "schedule": crontab(hour=2, minute=30),
    },
    # ─── backend-005: Paymob recurring renewals (hourly) ─────────────
    # Walks tenants whose ``next_renewal_at`` has passed and re-charges
    # the stored card token. Hourly cadence is fine: failures push
    # ``next_renewal_at`` +24h so a tenant in dunning isn't re-tried
    # every minute.
    "process-due-renewals": {
        "task": "tasks.process_due_renewals",
        "schedule": crontab(minute=20),  # Hourly at :20
    },
    # ─── backend-017: Shopify verification-overage relay (daily) ─────
    # Daily at 04:00 UTC (~06:00 Cairo) — after the 03:30 reconciliation
    # so message counts are settled. Idempotency on the
    # store_id+period key means re-running the same period within the
    # day never double-charges.
    "report-verification-overages": {
        "task": "tasks.report_verification_overages",
        "schedule": crontab(hour=4, minute=0),
    },
    # ─── Meta Conversions: catch orphaned Purchase events ─────────────
    # Hourly sweep finds paid orders without a Purchase row in the
    # event log and re-enqueues them. Recovers from webhook failures
    # (Paymob/Fawry blip, worker crash mid-fanout, Meta down).
    "meta-capi-sweep-orphaned-purchases": {
        "task": "tasks.meta_capi_sweep_orphaned_purchases",
        "schedule": crontab(minute=10),  # hourly at :10
    },
    # ─── offers-v2: promotion lifecycle ─────────────────────────────────
    # Sweeping the promotion table every 5 min keeps the storefront and
    # the merchant list in sync with `starts_at` / `ends_at` without
    # waiting for the 60s cache TTL on every active surface.
    "expire-promotions": {
        "task": "tasks.expire_promotions",
        "schedule": crontab(minute="*/5"),
    },
    # Daily prune of raw promotion_events past the 90-day retention.
    "prune-promotion-events": {
        "task": "tasks.prune_promotion_events",
        "schedule": crontab(hour=2, minute=45),
    },
    # Daily rollup target — the aggregate table itself ships in step 13.
    "rollup-promotion-events-daily": {
        "task": "tasks.rollup_promotion_events_daily",
        "schedule": crontab(hour=2, minute=15),
    },
    # backend-030 / US3 — dispatch due whatsapp_scheduled_sends rows.
    # Fires every 60s; per-row guard re-evaluated at dispatch time
    # (FR-014, FR-015, FR-017).
    "dispatch-whatsapp-scheduled-sends": {
        "task": "numu_api.whatsapp.dispatch_scheduled_sends",
        "schedule": 60.0,
    },
    # backend-030 / US5 — poll Meta for PENDING template statuses
    # (FR-028 / FR-028a). 15-minute cadence; only PENDING templates
    # older than 5 minutes are polled per FR-028a.
    "poll-whatsapp-pending-templates": {
        "task": "numu_api.whatsapp.poll_pending_templates",
        "schedule": 15 * 60.0,
    },
}


# ─── Eager model loading ───────────────────────────────────────────────
# SQLAlchemy resolves string-based relationships (e.g. RoleModel ↔
# membership_roles) lazily, the first time a query against either side is
# compiled. The Celery worker reaches the ORM through tasks → services →
# models, but that import chain doesn't transitively touch every model
# file — anything not on the chain stays unregistered. The first ORM call
# in an unlucky task then fails with "InvalidRequestError: One or more
# mappers failed to initialize" and every subsequent ORM call in the
# worker fails the same way until the process is recycled.
#
# Walking the models package once at startup forces every class to
# register, so the mapper graph is fully resolvable before any task runs.
def _load_all_models() -> None:
    import importlib
    import pkgutil

    import src.infrastructure.database.models as _pkg

    for module_info in pkgutil.walk_packages(_pkg.__path__, prefix=_pkg.__name__ + "."):
        importlib.import_module(module_info.name)


_load_all_models()
