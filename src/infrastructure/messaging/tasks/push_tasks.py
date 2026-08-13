"""Celery tasks for push notification delivery.

ALWAYS async, never inline. A push service that is slow or down must not be
able to slow — let alone fail — an order webhook. The order is the business
event; the notification is a courtesy on top of it.

One task fans out to BOTH providers (Web Push for the PWA, Expo for the mobile
app) because they share ``device_registrations``.
"""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

from src.core.logging import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def run_async(coro):
    """Run async code in a Celery task, reusing one loop per worker thread."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


async def _deliver(
    *,
    tenant_id: UUID,
    user_ids: list[UUID] | None,
    title: str,
    body: str,
    url: str,
    tag: str,
) -> dict[str, int]:
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.external_services.notifications.web_push_service import (
        PushOutcome,
        PushSubscription,
        build_payload,
        send_web_push,
    )
    from src.infrastructure.repositories.device_registration_repository import (
        DeviceRegistrationRepository,
    )

    stats = {"delivered": 0, "revoked": 0, "failed": 0, "skipped": 0}

    async with AsyncSessionLocal() as session:
        repo = DeviceRegistrationRepository(session)
        devices = await repo.list_active_for_users(
            tenant_id=tenant_id, user_ids=user_ids
        )

        for device in devices:
            if device.provider != "webpush":
                # Expo delivery is a separate transport; the mobile app is not
                # shipping push yet, so those rows are recorded and skipped
                # rather than silently dropped.
                stats["skipped"] += 1
                continue

            payload = build_payload(
                title=title, body=body, url=url, tag=tag, locale=device.locale
            )
            outcome = send_web_push(
                PushSubscription(
                    endpoint=device.endpoint, p256dh=device.p256dh, auth=device.auth
                ),
                payload,
            )

            if outcome is PushOutcome.DELIVERED:
                stats["delivered"] += 1
            elif outcome is PushOutcome.GONE:
                # The push service says this subscription no longer exists.
                # Revoke immediately — dead endpoints otherwise accumulate
                # forever and every future fan-out pays for them.
                await repo.revoke_endpoint(device.endpoint)
                stats["revoked"] += 1
            elif outcome is PushOutcome.RETRYABLE:
                await repo.record_failure(device.endpoint)
                stats["failed"] += 1
            else:
                stats["skipped"] += 1

        await session.commit()

    return stats


@celery_app.task(
    name="tasks.send_push_notification",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
)
def send_push_notification_task(
    self: Any,
    tenant_id: str,
    title: str,
    body: str,
    url: str,
    tag: str,
    user_ids: list[str] | None = None,
) -> dict[str, int]:
    """Fan out one notification to a tenant's registered devices.

    ``tag`` collapses duplicates at the OS level, which is what makes a retry
    safe: re-running this task replaces the existing notification instead of
    stacking a second one on the merchant's lock screen.

    Payload rules (enforced in ``build_payload``): order number and amount
    only — never customer PII — and a RELATIVE in-app ``url``.
    """
    try:
        stats = run_async(
            _deliver(
                tenant_id=UUID(tenant_id),
                user_ids=[UUID(u) for u in user_ids] if user_ids else None,
                title=title,
                body=body,
                url=url,
                tag=tag,
            )
        )
        logger.info(
            "push_fanout_complete",
            tenant_id=tenant_id,
            tag=tag,
            **stats,
        )
        return stats
    except Exception as exc:  # noqa: BLE001
        # Deliberately does NOT log the payload — body carries an order total,
        # and logs are a wider audience than a lock screen.
        logger.warning(
            "push_fanout_failed", tenant_id=tenant_id, tag=tag, error=str(exc)
        )
        raise self.retry(exc=exc) from exc
