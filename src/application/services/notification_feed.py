"""Write side of the merchant notification feed.

Every producer (EventBus handlers, Celery tasks) goes through
``emit_notification_standalone`` so the rules live in one place:

* category must be one of ``NOTIFICATION_CATEGORIES``;
* a store can mute whole categories via
  ``store.settings.notification_center.muted_categories``;
* ``dedupe_key`` makes replays idempotent;
* after COMMIT the row is fanned out: a Redis publish feeds the hub's
  SSE stream (``GET /notifications/stream``) and — for important rows —
  a web-push to the owner's devices (opt-out via
  ``store.settings.push_notifications.important``).

The hub renders the bilingual feed copy from ``kind`` + ``data``; only
the push (lock-screen) copy is rendered here, and it carries NO customer
PII — order number + amount only, same rule as ``merchant_push_handler``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.core.logging import get_logger
from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.database.models.tenant.merchant_notification import (
    NOTIFICATION_CATEGORIES,
    MerchantNotificationModel,
)
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.repositories.merchant_notification_repository import (
    MerchantNotificationRepository,
)

logger = get_logger(__name__)

SETTINGS_KEY = "notification_center"
REALTIME_PUBLISH_TIMEOUT_S = 2.0


def notification_channel(store_id: UUID | str) -> str:
    """Redis pub/sub channel the SSE stream subscribes to."""
    return f"store:{store_id}:notifications"


def muted_categories(store_settings: dict | None) -> set[str]:
    prefs = (store_settings or {}).get(SETTINGS_KEY) or {}
    muted = prefs.get("muted_categories") or []
    return {c for c in muted if isinstance(c, str)}


def push_important_enabled(store_settings: dict | None) -> bool:
    """Absent key means enabled — opt-out, like the new-order push."""
    push = (store_settings or {}).get("push_notifications") or {}
    return bool(push.get("important", True))


@dataclass
class EmitResult:
    """What ``emit_notification`` did, plus what fan-out needs."""

    written: bool
    notification_id: UUID | None = None
    store_id: UUID | None = None
    tenant_id: UUID | None = None
    owner_id: UUID | None = None
    language: str = "ar"
    store_settings: dict = field(default_factory=dict)
    category: str = ""
    kind: str = ""
    data: dict = field(default_factory=dict)
    link: str | None = None
    important: bool = False
    dedupe_key: str | None = None

    def __bool__(self) -> bool:  # keeps `if await emit_notification(...)` working
        return self.written


async def emit_notification(
    session: AsyncSession,
    *,
    store_id: UUID,
    category: str,
    kind: str,
    data: dict[str, Any] | None = None,
    link: str | None = None,
    entity_type: str | None = None,
    entity_id: UUID | None = None,
    important: bool = False,
    dedupe_key: str | None = None,
    tenant_id: UUID | None = None,
) -> EmitResult:
    """Insert one feed row inside the caller's transaction (no fan-out).

    ``written`` is False when skipped (unknown store, muted category,
    duplicate dedupe_key). Callers that own the transaction should call
    ``fanout(result)`` AFTER commit; ``emit_notification_standalone``
    does both.
    """
    if category not in NOTIFICATION_CATEGORIES:
        raise ValueError(f"unknown notification category: {category}")

    row = await session.execute(
        select(
            StoreModel.tenant_id,
            StoreModel.settings,
            StoreModel.owner_id,
            StoreModel.default_language,
        ).where(StoreModel.id == store_id)
    )
    found = row.first()
    if found is None:
        logger.warning(
            "notification_skipped_no_store", store_id=str(store_id), kind=kind
        )
        return EmitResult(written=False)
    resolved_tenant, store_settings, owner_id, language = found
    if category in muted_categories(store_settings):
        return EmitResult(written=False)

    model = MerchantNotificationModel(
        tenant_id=tenant_id or resolved_tenant,
        store_id=store_id,
        category=category,
        kind=kind,
        data=data or {},
        link=link,
        entity_type=entity_type,
        entity_id=entity_id,
        is_important=important,
        dedupe_key=dedupe_key,
    )
    repo = MerchantNotificationRepository(session)
    written = await repo.create(model)
    return EmitResult(
        written=written,
        notification_id=model.id if written else None,
        store_id=store_id,
        tenant_id=tenant_id or resolved_tenant,
        owner_id=owner_id,
        language=(language or "ar"),
        store_settings=store_settings or {},
        category=category,
        kind=kind,
        data=data or {},
        link=link,
        important=important,
        dedupe_key=dedupe_key,
    )


async def emit_notification_standalone(**kwargs: Any) -> EmitResult:
    """``emit_notification`` in its own committed session, then fan-out.

    For Celery tasks and EventBus handlers (which run post-commit in
    their own session anyway). Best-effort: never raises.
    """
    try:
        async with AsyncSessionLocal() as session, session.begin():
            result = await emit_notification(session, **kwargs)
    except Exception:
        # The feed is best-effort: never let it break the producer.
        logger.exception("notification_emit_failed", kind=kwargs.get("kind"))
        return EmitResult(written=False)
    if result.written:
        await fanout(result)
    return result


# ── Post-commit fan-out ───────────────────────────────────────────────


async def fanout(result: EmitResult) -> None:
    """Realtime publish + (important only) web-push. Never raises."""
    if not result.written or result.store_id is None:
        return
    await _publish_realtime(result)
    if result.important and push_important_enabled(result.store_settings):
        _enqueue_push(result)


async def _publish_realtime(result: EmitResult) -> None:
    """Tell open hub tabs to refetch (SSE stream relays this)."""
    if not settings.redis_host:
        return
    try:
        from src.infrastructure.realtime.redis_pubsub import RealtimePublisher

        publisher = RealtimePublisher()
        try:
            await asyncio.wait_for(
                publisher.publish(
                    notification_channel(result.store_id),
                    {
                        "type": "notification",
                        "id": str(result.notification_id),
                        "category": result.category,
                        "kind": result.kind,
                        "important": result.important,
                        "link": result.link,
                    },
                ),
                timeout=REALTIME_PUBLISH_TIMEOUT_S,
            )
        finally:
            await publisher.redis.aclose()
    except Exception as exc:  # noqa: BLE001 — best-effort
        logger.warning(
            "notification_realtime_publish_failed",
            store_id=str(result.store_id),
            error=str(exc),
        )


def _format_amount(total_cents: Any, currency: Any, is_ar: bool) -> str:
    try:
        value = int(total_cents or 0) / 100
    except (TypeError, ValueError):
        return ""
    if not total_cents:
        return ""
    formatted = f"{value:,.0f}"
    cur = str(currency or "EGP").upper()
    if is_ar and cur == "EGP":
        return f"{formatted} ج.م"
    return f"{cur} {formatted}"


def push_copy(result: EmitResult) -> tuple[str, str] | None:
    """Lock-screen title/body for important kinds. No customer PII."""
    is_ar = not result.language.lower().startswith("en")
    d = result.data or {}
    number = d.get("order_number") or ""
    amount = _format_amount(d.get("total_cents"), d.get("currency"), is_ar)
    kind = result.kind
    if kind == "order.cancelled":
        title = f"تم إلغاء الطلب #{number}" if is_ar else f"Order #{number} cancelled"
        return title, amount
    if kind == "payment.failed":
        title = (
            f"فشل الدفع للطلب #{number}" if is_ar else f"Payment failed for #{number}"
        )
        return title, amount
    if kind == "shipment.returned":
        title = f"تم إرجاع الطلب #{number}" if is_ar else f"Order #{number} returned"
        return title, amount
    if kind == "trust.kill_switch":
        rate = d.get("rate_pct")
        title = (
            "تم إيقاف الموافقة التلقائية مؤقتًا"
            if is_ar
            else "Trust auto-approve paused"
        )
        body = (
            f"نسبة المرتجعات {rate}%"
            if is_ar and rate is not None
            else (f"RTO rate {rate}%" if rate is not None else "")
        )
        return title, body
    if not result.important:
        return None
    # Generic fallback for future important kinds: kind name only.
    return kind.replace(".", " ").replace("_", " ").capitalize(), amount


def _enqueue_push(result: EmitResult) -> None:
    copy = push_copy(result)
    if copy is None or result.tenant_id is None:
        return
    title, body = copy
    try:
        from src.infrastructure.messaging.tasks.push_tasks import (
            send_push_notification_task,
        )

        send_push_notification_task.delay(
            tenant_id=str(result.tenant_id),
            title=title,
            body=body,
            url=result.link or "/notifications",
            # Same tag as the feed dedupe → a Celery retry replaces rather
            # than stacks the lock-screen notification.
            tag=result.dedupe_key or f"{result.kind}:{result.notification_id}",
            user_ids=[str(result.owner_id)] if result.owner_id else None,
        )
    except Exception as exc:  # noqa: BLE001 — never break the producer
        logger.warning(
            "notification_push_enqueue_failed",
            store_id=str(result.store_id),
            kind=result.kind,
            error=str(exc),
        )
