"""Webhook delivery service.

Handles HMAC signing, HTTP delivery, exponential backoff retry scheduling,
and delivery log persistence for outgoing merchant webhooks.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx

from src.config.logging_config import get_logger
from src.core.entities.webhook import (
    WebhookDeliveryLog,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookDeliveryLogRepository,
    IWebhookSubscriptionRepository,
)

logger = get_logger(__name__)

# Retry schedule: attempt 1→10s, 2→30s, 3→2min, 4→10min, 5→30min
RETRY_DELAYS: list[timedelta] = [
    timedelta(seconds=10),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
]
MAX_ATTEMPTS = len(RETRY_DELAYS) + 1  # 6 total (1 initial + 5 retries)
DELIVERY_TIMEOUT = 10.0  # seconds


class WebhookDeliveryService:
    """Delivers webhook payloads to merchant-configured URLs."""

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        delivery_log_repo: IWebhookDeliveryLogRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.delivery_log_repo = delivery_log_repo

    @staticmethod
    def _sign(secret: str, body: bytes) -> str:
        """HMAC-SHA256 signature in GitHub webhook format: sha256=<hex>."""
        mac = hmac.new(secret.encode(), body, hashlib.sha256)
        return f"sha256={mac.hexdigest()}"

    @staticmethod
    def _build_envelope(event_type: WebhookEventType, event_data: dict) -> dict:
        return {
            "event": event_type.value,
            "timestamp": datetime.now(UTC).isoformat(),
            "data": event_data,
        }

    async def dispatch(
        self,
        store_id: UUID,
        event_type: WebhookEventType,
        event_id: UUID,
        event_data: dict,
    ) -> None:
        """Create delivery logs for all matching subscriptions and fire first attempt.

        Called by event handlers. Non-blocking — first attempt fires as an asyncio task.
        """
        subscriptions = await self.subscription_repo.get_active_for_event(
            store_id, event_type
        )
        if not subscriptions:
            return

        payload = self._build_envelope(event_type, event_data)

        for sub in subscriptions:
            log = WebhookDeliveryLog(
                subscription_id=sub.id,
                store_id=store_id,
                tenant_id=sub.tenant_id,
                event_type=event_type,
                event_id=event_id,
                payload=payload,
                status=WebhookDeliveryStatus.PENDING,
                next_attempt_at=datetime.now(UTC),
            )
            created_log = await self.delivery_log_repo.create(log)
            await self.delivery_log_repo.update(created_log)

            asyncio.create_task(
                _attempt_delivery(created_log.id, sub.url, sub.secret, payload),
                name=f"webhook:{event_type}:{sub.id}",
            )

        logger.info(
            "webhook_dispatched",
            store_id=str(store_id),
            event_type=event_type.value,
            event_id=str(event_id),
            subscription_count=len(subscriptions),
        )


async def _attempt_delivery(
    log_id: UUID,
    url: str,
    secret: str,
    payload: dict,
) -> None:
    """Perform one HTTP POST attempt and update the delivery log.

    Runs in its own session — safe to use as a detached asyncio task.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.webhook_delivery_log_repository import (
        WebhookDeliveryLogRepository,
    )

    async with AsyncSessionLocal() as session:
        log_repo = WebhookDeliveryLogRepository(session)
        log = await log_repo.get_by_id(log_id)
        if not log or log.status == WebhookDeliveryStatus.SUCCESS:
            return

        body = json.dumps(payload, default=str).encode()
        signature = WebhookDeliveryService._sign(secret, body)

        now = datetime.now(UTC)
        log.attempt_count += 1
        log.last_attempt_at = now

        try:
            async with httpx.AsyncClient(timeout=DELIVERY_TIMEOUT) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-NUMU-Signature": signature,
                        "X-NUMU-Event": payload.get("event", ""),
                        "X-NUMU-Delivery": str(log_id),
                    },
                )

            log.last_status_code = response.status_code
            log.last_response_body = response.text[:1000]

            if 200 <= response.status_code < 300:
                log.status = WebhookDeliveryStatus.SUCCESS
                log.next_attempt_at = None
                logger.info(
                    "webhook_delivered",
                    log_id=str(log_id),
                    url=url,
                    status_code=response.status_code,
                    attempt=log.attempt_count,
                )
            else:
                _schedule_retry(log)
                logger.warning(
                    "webhook_delivery_failed",
                    log_id=str(log_id),
                    url=url,
                    status_code=response.status_code,
                    attempt=log.attempt_count,
                    next_attempt_at=str(log.next_attempt_at),
                )

        except Exception as exc:
            log.last_error = str(exc)[:500]
            log.last_status_code = None
            _schedule_retry(log)
            logger.warning(
                "webhook_delivery_error",
                log_id=str(log_id),
                url=url,
                error=str(exc),
                attempt=log.attempt_count,
                next_attempt_at=str(log.next_attempt_at),
            )

        await log_repo.update(log)
        await session.commit()


def _schedule_retry(log: WebhookDeliveryLog) -> None:
    """Set next_attempt_at using exponential backoff, or mark exhausted."""
    # attempt_count was already incremented before this call (1-indexed)
    retry_index = log.attempt_count - 1  # 0-indexed into RETRY_DELAYS
    if retry_index < len(RETRY_DELAYS):
        log.next_attempt_at = datetime.now(UTC) + RETRY_DELAYS[retry_index]
        log.status = WebhookDeliveryStatus.PENDING
    else:
        log.status = WebhookDeliveryStatus.EXHAUSTED
        log.exhausted_at = datetime.now(UTC)
        log.next_attempt_at = None
        logger.warning("webhook_delivery_exhausted", log_id=str(log.id))


async def retry_pending_deliveries() -> int:
    """Pick up all due pending deliveries and fire them.

    Called by the Celery beat task every 15 seconds.
    Returns the number of deliveries processed.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.webhook_delivery_log_repository import (
        WebhookDeliveryLogRepository,
    )
    from src.infrastructure.repositories.webhook_subscription_repository import (
        WebhookSubscriptionRepository,
    )

    async with AsyncSessionLocal() as session:
        log_repo = WebhookDeliveryLogRepository(session)
        sub_repo = WebhookSubscriptionRepository(session)

        pending = await log_repo.get_pending_retries(datetime.now(UTC))

        for log in pending:
            if not log.subscription_id:
                continue
            sub = await sub_repo.get_by_id(log.subscription_id)
            if sub and sub.is_active:
                asyncio.create_task(
                    _attempt_delivery(log.id, sub.url, sub.secret, log.payload),
                    name=f"webhook_retry:{log.id}",
                )

        return len(pending)
