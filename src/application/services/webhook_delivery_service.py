"""Webhook delivery service.

Handles HMAC signing, HTTP delivery, exponential backoff retry scheduling,
and delivery log persistence for outgoing merchant webhooks.
"""

import asyncio
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx

from src.core.entities.webhook import (
    WebhookDeliveryLog,
    WebhookDeliveryStatus,
    WebhookEventType,
)
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookDeliveryLogRepository,
    IWebhookSubscriptionRepository,
)
from src.core.logging import get_logger
from src.core.url_guard import UnsafeUrlError, assert_webhook_target

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

    async def _store_may_receive(self, store_id: UUID) -> bool:
        """Webhooks are part of API access, so a downgrade stops them.

        Checked at dispatch rather than only at subscribe time: a store that
        drops off the plan keeps its subscription rows, and without this it
        keeps receiving events it is no longer paying for.
        """
        from src.application.services.api_access import api_access_for_store

        session = getattr(self.subscription_repo, "session", None)
        if session is None:
            return True
        return (await api_access_for_store(session, store_id)).allowed

    @staticmethod
    def _sign(secret: str, body: bytes) -> str:
        """HMAC-SHA256 signature in GitHub webhook format: sha256=<hex>."""
        mac = hmac.new(secret.encode(), body, hashlib.sha256)
        return f"sha256={mac.hexdigest()}"

    @staticmethod
    def _sign_v1(secret: str, body: bytes, timestamp: int) -> str:
        """Stripe-style ``t=<unix>,v1=<hex>`` over ``<timestamp>.<body>``.

        The body-only signature above proves who sent a payload but not when,
        so a captured delivery stays replayable forever. Signing the timestamp
        too lets a receiver reject anything older than its own tolerance. Sent
        alongside the original header, which existing consumers still verify.
        """
        mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
        return f"t={timestamp},v1={mac.hexdigest()}"

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
        if subscriptions and not await self._store_may_receive(store_id):
            # A Partner App's subscriptions are not gated by the store's plan
            # (OD-7: the install is the grant); the merchant's own are.
            skipped = [s for s in subscriptions if s.app_installation_id is None]
            subscriptions = [s for s in subscriptions if s.app_installation_id]
            logger.info(
                "webhook_dispatch_skipped_no_api_access",
                store_id=str(store_id),
                event_type=event_type.value,
                subscriptions=len(skipped),
            )
        if not subscriptions:
            return
        session = getattr(self.subscription_repo, "session", None)

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

            secret = await signing_secret(session, sub)
            if not secret:
                continue
            asyncio.create_task(
                _attempt_delivery(created_log.id, sub.url, secret, payload),
                name=f"webhook:{event_type}:{sub.id}",
            )

        logger.info(
            "webhook_dispatched",
            store_id=str(store_id),
            event_type=event_type.value,
            event_id=str(event_id),
            subscription_count=len(subscriptions),
        )


async def signing_secret(session, sub) -> str | None:
    """The key a delivery is signed with.

    The merchant's own subscriptions: their per-subscription secret. A Partner
    App's: the app's client secret (plan 06 D11), decrypted at send time so a
    rotation takes effect at once and it is never copied into this table.
    None when an app has no readable secret yet (created before Phase 4 and
    never rotated): skip rather than sign with nothing.
    """
    if not getattr(sub, "app_installation_id", None):
        return sub.secret
    if session is None:
        return None
    from src.application.services.app_tokens import read_client_secret
    from src.infrastructure.database.models.public.app import AppInstallationModel

    installation = await session.get(AppInstallationModel, sub.app_installation_id)
    if installation is None:
        return None
    return await read_client_secret(session, installation.app_id)


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
        sent_at = int(datetime.now(UTC).timestamp())
        signature_v1 = WebhookDeliveryService._sign_v1(secret, body, sent_at)

        now = datetime.now(UTC)
        log.attempt_count += 1
        log.last_attempt_at = now

        try:
            # Re-checked per attempt: the host may resolve elsewhere than it
            # did at create time, and a retry can be days later. Redirects stay
            # off so a public URL cannot bounce us into the private network.
            assert_webhook_target(url)
            async with httpx.AsyncClient(
                timeout=DELIVERY_TIMEOUT, follow_redirects=False
            ) as client:
                response = await client.post(
                    url,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-NUMU-Signature": signature,
                        "X-NUMU-Signature-V1": signature_v1,
                        "X-NUMU-Timestamp": str(sent_at),
                        "X-NUMU-Event": payload.get("event", ""),
                        "X-NUMU-Delivery": str(log_id),
                    },
                )

            log.last_status_code = response.status_code
            log.last_response_body = response.text[:1000]

            if response.status_code == 410:
                # "Gone" is the receiver telling us to stop. Honour it.
                log.status = WebhookDeliveryStatus.EXHAUSTED
                log.exhausted_at = datetime.now(UTC)
                log.next_attempt_at = None
                logger.info("webhook_endpoint_gone", log_id=str(log_id), url=url)
            elif 200 <= response.status_code < 300:
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

        except UnsafeUrlError as exc:
            # Permanent: retrying cannot make the address public, and every
            # retry is another request at an internal host.
            log.last_error = str(exc)[:500]
            log.last_status_code = None
            log.status = WebhookDeliveryStatus.EXHAUSTED
            log.next_attempt_at = None
            log.exhausted_at = datetime.now(UTC)
            logger.warning(
                "webhook_delivery_unsafe_target",
                log_id=str(log_id),
                url=url,
                error=str(exc),
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

    if log.status == WebhookDeliveryStatus.EXHAUSTED:
        await _deactivate_and_notify(log)


async def _deactivate_and_notify(log: WebhookDeliveryLog) -> None:
    """Switch off an endpoint that has stopped answering, and say so.

    Left active, a dead endpoint burns six attempts per event forever and the
    merchant finds out when they notice the silence. Turning it off makes the
    state visible and the fix explicit: correct the URL, then re-enable.
    """
    if not log.subscription_id:
        return
    from src.application.services.notification_feed import (
        emit_notification_standalone,
    )
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.webhook_subscription_repository import (
        WebhookSubscriptionRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = WebhookSubscriptionRepository(session)
        sub = await repo.get_by_id(log.subscription_id)
        if sub is None or not sub.is_active:
            return
        sub.is_active = False
        await repo.update(sub)
        await session.commit()

    await emit_notification_standalone(
        store_id=log.store_id,
        category="system",
        kind="webhook.deactivated",
        data={
            "url": sub.url,
            "event": log.event_type.value,
            "attempts": log.attempt_count,
            "last_error": log.last_error or str(log.last_status_code or ""),
        },
        link="/settings/developers",
        entity_type="webhook_subscription",
        entity_id=sub.id,
        important=True,
        dedupe_key=f"webhook-deactivated:{sub.id}",
    )


async def send_test_delivery(subscription) -> dict:
    """POST a ``webhook.ping`` once and return what the endpoint answered.

    Signed exactly like a real event, so a receiver that passes this has a
    working signature check — which is the point of testing at all.
    """
    payload = WebhookDeliveryService._build_envelope(
        WebhookEventType.PING,
        {
            "subscription_id": str(subscription.id),
            "store_id": str(subscription.store_id),
            "message": "If you can read this, your endpoint is wired correctly.",
        },
    )
    delivery_id = uuid4()
    body = json.dumps(payload, default=str).encode()
    sent_at = int(datetime.now(UTC).timestamp())

    try:
        assert_webhook_target(subscription.url)
        async with httpx.AsyncClient(
            timeout=DELIVERY_TIMEOUT, follow_redirects=False
        ) as client:
            response = await client.post(
                subscription.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-NUMU-Signature": WebhookDeliveryService._sign(
                        subscription.secret, body
                    ),
                    "X-NUMU-Signature-V1": WebhookDeliveryService._sign_v1(
                        subscription.secret, body, sent_at
                    ),
                    "X-NUMU-Timestamp": str(sent_at),
                    "X-NUMU-Event": WebhookEventType.PING.value,
                    "X-NUMU-Delivery": str(delivery_id),
                },
            )
    except (UnsafeUrlError, httpx.HTTPError) as exc:
        return {
            "delivered": False,
            "status_code": None,
            "error": str(exc)[:500],
            "delivery_id": str(delivery_id),
        }

    return {
        "delivered": 200 <= response.status_code < 300,
        "status_code": response.status_code,
        "error": None if response.is_success else response.text[:500] or None,
        "delivery_id": str(delivery_id),
    }


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


#: How long a settled delivery log is kept. Long enough to debug last
#: month's failure, short enough that the table does not grow forever.
LOG_RETENTION = timedelta(days=30)


async def purge_old_delivery_logs() -> int:
    """Delete settled delivery logs older than :data:`LOG_RETENTION`."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.webhook_delivery_log_repository import (
        WebhookDeliveryLogRepository,
    )

    async with AsyncSessionLocal() as session:
        deleted = await WebhookDeliveryLogRepository(session).purge_before(
            datetime.now(UTC) - LOG_RETENTION
        )
        await session.commit()
        return deleted


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

        pending = await log_repo.claim_pending_retries(datetime.now(UTC))
        await session.commit()

        for log in pending:
            if not log.subscription_id:
                continue
            sub = await sub_repo.get_by_id(log.subscription_id)
            if sub and sub.is_active:
                secret = await signing_secret(session, sub)
                if not secret:
                    continue
                asyncio.create_task(
                    _attempt_delivery(log.id, sub.url, secret, log.payload),
                    name=f"webhook_retry:{log.id}",
                )

        return len(pending)
