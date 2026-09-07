"""Meta Conversions API (CAPI) Celery tasks — fan-out + cron sweep.

Two tasks live here:

  * ``meta_capi_send_event`` — the per-event fan-out worker. Called from
    ``/track`` (browse + funnel events) and from payment webhooks
    (Purchase). Re-checks ``capi_enabled`` at execution time so a
    merchant toggling the flag off mid-flight doesn't trigger a stale
    fan-out from a queued job.

  * ``meta_capi_sweep_orphaned_purchases`` — hourly Celery Beat task
    that finds paid orders missing a Purchase row in ``meta_event_log``
    and re-enqueues them. Catches webhook failures (per plan §12 risks
    table).

The dedup contract is plan §6: insert a ``meta_event_log`` row first;
``IntegrityError`` on the ``UNIQUE (store_id, event_id)`` constraint is
the **silent skip** signal — not an error.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any, NamedTuple
from uuid import UUID

import httpx
import sentry_sdk

from src.config import settings
from src.core.logging import get_logger
from src.core.services import meta_delivery_policy as policy
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Funnel-step → Meta-event mapping (plan §5.3)
# ──────────────────────────────────────────────────────────────────────
# Public so settings.test-event endpoint and tests can import it.
# Meta deduplicates a repeated (pixel_id, event_name, event_id) for 48 hours.
# A resend INSIDE that window is merged and contributes its extra match keys;
# a resend outside it is a brand-new event and double-counts the conversion.
_META_DEDUP_WINDOW_SECONDS = 48 * 60 * 60

# Hard ceiling on identity-enrichment resends per logged event. Without it a
# misbehaving client could turn `refireFunnelWithIdentity` into an outbound
# traffic amplifier against Meta's API.
_MAX_ENRICHMENT_RESENDS = 3

# The hashed match keys worth a resend. `fbp`/`fbc`/ip/ua are excluded on
# purpose: they are present from the first fire, so a change there is churn
# rather than enrichment.
_MATCH_KEY_FIELDS: tuple[str, ...] = (
    "em",
    "ph",
    "fn",
    "ln",
    "ct",
    "st",
    "zp",
    "country",
    "external_id",
)


def _sweep_order_filter(cutoff: datetime) -> Any:
    """Orders the orphan sweep should consider: paid, OR cash-on-delivery.

    COD never stamps ``paid_at`` (it is set on collection, if at all) and has no
    payment webhook, so a ``paid_at IS NOT NULL`` filter excluded COD orders
    from the recovery path entirely. In a COD-majority market that left the
    browser thank-you page as the ONLY source of a Purchase event — so any
    buyer who closed the tab on redirect, or ran an ad blocker, produced no
    conversion at all. That is a missing sale, not merely weak matching.

    ``payment_method IS NULL`` counts as COD to match checkout's own rule
    (``not request.payment_method or request.payment_method == "cod"``).

    Timing stays the merchant's decision: the per-order loop skips COD orders
    for stores that configured an explicit ``purchase_trigger``, because there
    the order-status handler owns when Purchase fires.
    """
    from sqlalchemy import and_, or_

    from src.infrastructure.database.models.tenant.order import OrderModel

    return or_(
        and_(OrderModel.paid_at.isnot(None), OrderModel.paid_at >= cutoff),
        and_(
            OrderModel.paid_at.is_(None),
            or_(
                OrderModel.payment_method == "cod",
                OrderModel.payment_method.is_(None),
            ),
            OrderModel.created_at >= cutoff,
        ),
    )


def _adds_match_keys(new_user_data: dict, stored_user_data: dict) -> bool:
    """True when ``new_user_data`` carries a match key the stored copy lacks.

    Strictly additive: a value that merely *changed* does not qualify, only one
    that goes from absent to present. That keeps the resend path tied to real
    identity discovery (the shopper typed their email) rather than to noise.
    """
    if not isinstance(stored_user_data, dict) or not isinstance(new_user_data, dict):
        return False
    return any(
        new_user_data.get(field) and not stored_user_data.get(field)
        for field in _MATCH_KEY_FIELDS
    )


FUNNEL_STEP_TO_META_EVENT: dict[str, str] = {
    "page_view": "PageView",
    # `collection_view` is what PageViewTracker emits for every
    # /collections/* route. It had no entry here, so `.get(step)` returned
    # None and the CAPI enqueue silently returned — collection pages were
    # browser-pixel-only. Measured on a live store: the server leg carried
    # only ~57% of browser PageViews while every other event paired 1:1.
    # Mapping to PageView is safe because the browser leg already fires
    # PageView for these routes under the SAME `pageViewEventId`, so the
    # pair dedupes cleanly rather than double-counting.
    "collection_view": "PageView",
    "product_view": "ViewContent",
    "add_to_cart": "AddToCart",
    "checkout_started": "InitiateCheckout",
    # The shipping step is where the address (city + governorate + postal
    # code + country) is finally complete. It is already a valid funnel step
    # and is already in `_IDENTITY_RESOLUTION_STEPS`, but had no Meta
    # mapping — so the richest identity moment in the whole funnel produced
    # no event. AddShippingInfo is not a Meta *standard* event; a custom one
    # still carries full `user_data` and still counts toward match quality.
    "add_shipping_info": "AddShippingInfo",
    # NB: order_completed is normally fired from the payment webhook,
    # NOT /track — but if the storefront posts it (browser confirmation
    # page), we still enqueue Purchase. The UNIQUE constraint dedupes
    # against the webhook fire.
    "order_completed": "Purchase",
    # The true COD conversion moment. `order_delivered` is already a valid
    # funnel step but had no Meta mapping, so a delivery confirmation was
    # invisible unless the merchant happened to set purchase_trigger
    # ="delivered". A custom event still carries full user_data, so the
    # signal exists regardless of how they configured Purchase timing.
    "order_delivered": "DeliveredOrder",
    # Phase 2 standard events — storefront fires these via fireMetaEvent
    # for search box submissions, newsletter signups, customer registration,
    # and payment-method selection. Meta uses them for audience building
    # and funnel optimization (Lead/CompleteRegistration → lookalikes;
    # Search → "people who searched for X" retargeting; AddPaymentInfo →
    # checkout-funnel optimization).
    "search": "Search",
    "lead": "Lead",
    "complete_registration": "CompleteRegistration",
    "add_payment_info": "AddPaymentInfo",
    # Wave 4 Phase 22 — additional Meta standard events. Each fires
    # from a discrete storefront action; backend just routes the
    # funnel-step name to Meta's canonical event name.
    # ``subscribe`` distinct from ``lead``: Lead fires on any email-
    # capture intent; Subscribe is the explicit recurring-newsletter
    # opt-in (the user checked "send me weekly emails").
    "subscribe": "Subscribe",
    # ``contact`` fires on Contact-form submit — Meta uses it as a
    # high-intent signal distinct from generic Lead.
    "contact": "Contact",
    # ``add_to_wishlist`` mirrors the storefront wishlist toggle when
    # the merchant has the wishlist feature enabled.
    "add_to_wishlist": "AddToWishlist",
    # ``customize_product`` fires when the customer interacts with a
    # variant configurator on multi-axis products (e.g., picks size +
    # color + engraving). Meta uses it as a checkout-intent signal.
    "customize_product": "CustomizeProduct",
}


def _with_store_phone_cc(user_data: dict, store: Any) -> dict:
    """Tag the payload with the store's dial code for national-format phones.

    A number typed as "0501234567" carries no country, and assuming Egypt
    unconditionally produced a well-formed, present, permanently unmatchable
    `ph` for every non-Egyptian store. The store's own country is the right
    signal — a store sells into its market.
    """
    from src.infrastructure.external_services.meta.hashing import (
        dial_code_for_country,
    )

    if user_data.get("default_phone_cc"):
        return user_data
    country = getattr(store, "country", None)
    if not country:
        return user_data
    return {**user_data, "default_phone_cc": dial_code_for_country(country)}


def _funnel_step_to_meta_event(step: str) -> str | None:
    """Return the Meta event name for a NUMU funnel step, or None."""
    return FUNNEL_STEP_TO_META_EVENT.get(step)


# ----------------------------------------------------------------------
# Enqueue - the one door into CAPI delivery
# ----------------------------------------------------------------------

# Celery's in-broker retry budget. Declared here rather than read back off
# the task at runtime so there is exactly one source of truth: the decorator
# below takes it, and `_celery_will_retry` compares against it. Reflecting on
# `task.max_retries` instead meant the boundary between the two retry
# mechanisms depended on whatever object Celery happened to bind as `self`.
_CELERY_MAX_RETRIES = 6

# Attempts Celery owns before the outbox sweep takes over: the initial call
# plus its retries. These are the FAST ones - seconds to five minutes,
# entirely in-broker - and they exist to ride out a blip. Everything past
# this budget is an outage, and outages are the sweep's ladder to walk.
CELERY_ATTEMPT_BUDGET = _CELERY_MAX_RETRIES + 1

# Total attempts across both mechanisms. Past this the row is dead-lettered:
# it stays visible with its full history, but nothing tries again.
MAX_TOTAL_ATTEMPTS = CELERY_ATTEMPT_BUDGET + policy.MAX_SWEEP_ATTEMPTS

# Sentinel distinguishing "no row to adopt, use the legacy insert-in-worker
# path" (None) from "this event is already delivered, send nothing".
_ALREADY_DELIVERED = object()


def _sweep_attempt_for(attempt_count: int) -> int:
    """Which rung of the sweep's backoff ladder ``attempt_count`` sits on."""
    return max(1, attempt_count - CELERY_ATTEMPT_BUDGET + 1)


def _build_request_payload(
    *,
    event_name: str,
    event_time: int,
    event_source_url: str | None,
    action_source: str,
    custom_data: dict[str, Any],
    user_data: dict[str, Any],
    store: Any,
    test_event_code: str | None,
    opt_out: bool,
) -> dict[str, Any]:
    """The stored, PII-hashed record of what we send Meta.

    One builder for both the enqueue-time pre-persist and the worker's own
    send, so a recovered event is identical to the one that was lost.
    Duplicating this logic is how the orphan sweep used to send a weaker
    payload than the webhook did for the same order.
    """
    from src.infrastructure.external_services.meta.hashing import hash_user_data

    payload: dict[str, Any] = {
        "event_name": event_name,
        "event_time": event_time,
        "event_source_url": event_source_url,
        "action_source": action_source,
        "custom_data": custom_data,
        "user_data": hash_user_data(_with_store_phone_cc(user_data, store)),
        "test_event_code": test_event_code,
    }
    # Wave 3 Phase 18 - opt_out at the event level (Meta's spec). Only
    # attached when true, to keep payloads minimal for the majority of
    # events (most shoppers have not denied marketing).
    if opt_out:
        payload["opt_out"] = True
    return payload


async def enqueue_capi_event(
    *,
    session: Any | None = None,
    store: Any | None = None,
    tenant_id: Any | None = None,
    store_id: str,
    pixel_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    event_source_url: str | None,
    user_data: dict[str, Any],
    custom_data: dict[str, Any] | None = None,
    test_event_code: str | None = None,
    action_source: str = "website",
    opt_out: bool = False,
) -> None:
    """Hand one event to the delivery pipeline.

    Every enqueue site goes through here so the decisions that depend on
    *which* event this is - queue, priority, and whether to persist before
    enqueuing - are made once instead of at seven call sites.

    **Conversions are persisted before they are enqueued.** For those the
    broker is not allowed to be the only record: production Redis runs with
    ``maxmemory-policy allkeys-lru``, so a queued message is evictable, and
    an evicted Purchase leaves nothing anywhere to recover from. Writing the
    outbox row first makes the worst case a delayed conversion the sweep
    re-delivers, not a lost one.

    Everything else keeps the cheaper enqueue-then-persist shape. A PageView
    would cost a synchronous INSERT per pixel on the hottest path in the
    platform, it is already carried independently by the browser pixel, and
    it is not worth that. The worker still writes the row, so failures stay
    visible and retryable - only the pre-broker window is unprotected.

    Never raises: a tracking side-effect must not fail the request that
    triggered it.
    """
    queue = policy.queue_for(event_name)
    kwargs: dict[str, Any] = {
        "store_id": store_id,
        "pixel_id": pixel_id,
        "event_name": event_name,
        "event_id": event_id,
        "event_time": event_time,
        "event_source_url": event_source_url,
        "user_data": user_data,
        "custom_data": custom_data or {},
        "test_event_code": test_event_code,
        "action_source": action_source,
        "opt_out": opt_out,
    }

    if (
        policy.priority_for(event_name) == policy.PRIORITY_CONVERSION
        and session is not None
    ):
        log_id = await _persist_outbox_row(
            session=session,
            store=store,
            tenant_id=tenant_id,
            store_id=store_id,
            pixel_id=pixel_id,
            event_name=event_name,
            event_id=event_id,
            event_time=event_time,
            event_source_url=event_source_url,
            user_data=user_data,
            custom_data=custom_data or {},
            test_event_code=test_event_code,
            action_source=action_source,
            opt_out=opt_out,
        )
        if log_id is _ALREADY_DELIVERED:
            # A prior attempt already got a 2xx for this (store, pixel,
            # event). Re-sending would be a second conversion, not a merge.
            return
        if log_id is not None:
            kwargs["log_id"] = str(log_id)

    meta_capi_send_event.apply_async(kwargs=kwargs, queue=queue)


async def _persist_outbox_row(
    *,
    session: Any,
    store: Any,
    tenant_id: Any,
    store_id: str,
    pixel_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    event_source_url: str | None,
    user_data: dict[str, Any],
    custom_data: dict[str, Any],
    test_event_code: str | None,
    action_source: str,
    opt_out: bool,
) -> Any:
    """Write the owed-delivery row, in the CALLER'S transaction.

    Deliberately not its own session: for a Purchase the caller is a payment
    webhook that is also writing the order. Sharing the transaction means an
    order that rolls back cannot leave behind an outbox row promising Meta a
    sale that never happened.

    Returns the row id, ``_ALREADY_DELIVERED``, or ``None`` when the row
    could not be written - in which case the caller falls back to the
    worker-side insert and loses only the pre-broker guarantee.
    """
    from sqlalchemy import select as sa_select
    from sqlalchemy.exc import IntegrityError

    from src.core.entities.meta_event_log import MetaEventLog
    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )

    resolved_tenant = tenant_id or getattr(store, "tenant_id", None)
    if resolved_tenant is None:
        return None

    occurred_at = datetime.fromtimestamp(event_time, tz=UTC)
    now = datetime.now(UTC)
    try:
        async with session.begin_nested():
            entity = await MetaEventLogRepository(session).create(
                MetaEventLog(
                    tenant_id=resolved_tenant,
                    store_id=UUID(store_id),
                    event_id=event_id,
                    event_name=event_name,
                    event_time=occurred_at,
                    pixel_id=pixel_id,
                    request_payload=_build_request_payload(
                        event_name=event_name,
                        event_time=event_time,
                        event_source_url=event_source_url,
                        action_source=action_source,
                        custom_data=custom_data,
                        user_data=user_data,
                        store=store,
                        test_event_code=test_event_code,
                        opt_out=opt_out,
                    ),
                    status=policy.DeliveryStatus.PENDING,
                    priority=policy.priority_for(event_name),
                    expires_at=policy.expires_at_for(occurred_at),
                    # The lease starts the moment the row exists, not when a
                    # worker picks it up. If the broker drops the message the
                    # lease lapses and the sweep recovers the event - which
                    # is the entire reason for persisting first.
                    next_retry_at=now + policy.CLAIM_LEASE,
                )
            )
        return entity.id
    except IntegrityError:
        # Same (store, pixel, event) already recorded - a webhook redelivery,
        # or the browser leg beat the server to it.
        existing = (
            await session.execute(
                sa_select(MetaEventLogModel).where(
                    MetaEventLogModel.store_id == UUID(store_id),
                    MetaEventLogModel.pixel_id == pixel_id,
                    MetaEventLogModel.event_id == event_id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            return None
        status = existing.response_status
        if status is not None and 200 <= status < 300:
            return _ALREADY_DELIVERED
        # Recorded but not delivered - let the worker adopt and resend it.
        return existing.id
    except Exception:  # noqa: BLE001 - never break the caller's request
        logger.exception(
            "meta_capi_outbox_persist_failed",
            store_id=store_id,
            event_name=event_name,
        )
        return None


# ──────────────────────────────────────────────────────────────────────
# Async-loop bridge for sync Celery worker
# ──────────────────────────────────────────────────────────────────────
_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro: Any) -> Any:
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


# ──────────────────────────────────────────────────────────────────────
# Per-event fan-out task
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.meta_capi_send_event",
    bind=True,
    max_retries=_CELERY_MAX_RETRIES,
    default_retry_delay=15,
    autoretry_for=(httpx.NetworkError, httpx.TimeoutException),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def meta_capi_send_event(
    self: Any,
    *,
    store_id: str,
    pixel_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    event_source_url: str | None,
    user_data: dict[str, Any],
    custom_data: dict[str, Any] | None = None,
    test_event_code: str | None = None,
    action_source: str = "website",
    opt_out: bool = False,
    log_id: str | None = None,
) -> dict[str, Any]:
    """Send one CAPI event with idempotency, retries and redaction.

    Returns a small status dict for Celery beat logs / observability:
        {"status": "sent" | "duplicate" | "skipped" | "failed",
         "fbtrace_id": str | None}

    Re-checks ``capi_enabled`` at execution time (per plan §5.5):
    a queued job whose store flipped the flag off mid-flight returns
    ``{"status": "skipped"}`` without contacting Meta.
    """
    # Tag every span so Sentry alert rules can target this task by tenant
    # store / event / outcome. Alert rules to create in Sentry UI:
    #
    #   1. "Meta CAPI failure rate >1%" — Metric Alert, query:
    #          message:"meta_capi.*" AND tag:meta_capi.status_class:["4xx","5xx","network","decrypt"]
    #          rate over count(message:"meta_capi.*") > 0.01 over 5min
    #   2. "Meta CAPI failures for single store >10/min" — Metric Alert, query:
    #          message:"meta_capi.*" AND tag:meta_capi.status_class:["4xx","5xx","network","decrypt"]
    #          group by tag:meta_capi.store_id, count > 10 over 1min
    #
    # Fingerprinting in capture_message calls below collapses one issue per
    # (store, status_class) combo — prevents 10k events spawning 10k issues.
    sentry_sdk.set_tag("meta_capi.event_name", event_name)
    sentry_sdk.set_tag("meta_capi.store_id", store_id)
    sentry_sdk.set_tag("meta_capi.pixel_id", pixel_id)
    sentry_sdk.set_tag("meta_capi.action_source", action_source)
    sentry_sdk.set_tag("meta_capi.test_mode", bool(test_event_code))
    sentry_sdk.set_context(
        "meta_capi",
        {
            "event_id": event_id,
            "event_time": event_time,
            "event_source_url": event_source_url,
            "retry_attempt": getattr(self.request, "retries", 0),
        },
    )

    try:
        result: dict[str, Any] = _run_async(
            _send_event(
                task=self,
                store_id=store_id,
                pixel_id=pixel_id,
                event_name=event_name,
                event_id=event_id,
                event_time=event_time,
                event_source_url=event_source_url,
                user_data=user_data,
                custom_data=custom_data or {},
                test_event_code=test_event_code,
                action_source=action_source,
                opt_out=opt_out,
                log_id=log_id,
            )
        )
        sentry_sdk.set_tag("meta_capi.status", result.get("status", "unknown"))
        return result
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        # Re-raised so Celery's autoretry kicks in. _send_event has
        # already updated meta_event_log.last_error / attempt_count.
        sentry_sdk.set_tag("meta_capi.status_class", "network")
        sentry_sdk.capture_message(
            f"meta_capi.network_error for store {store_id}: {type(exc).__name__}",
            level="warning",
            fingerprint=[
                "meta_capi",
                "network",
                store_id,
                type(exc).__name__,
            ],
        )
        raise
    except Exception:  # noqa: BLE001 — last-ditch: log + bury
        logger.exception("meta_capi_send_event_unexpected_error")
        sentry_sdk.set_tag("meta_capi.status_class", "unexpected")
        sentry_sdk.capture_exception(
            fingerprint=["meta_capi", "unexpected", store_id, event_name],
        )
        return {"status": "failed", "fbtrace_id": None}


async def _send_event(
    *,
    task: Any,
    store_id: str,
    pixel_id: str,
    event_name: str,
    event_id: str,
    event_time: int,
    event_source_url: str | None,
    user_data: dict[str, Any],
    custom_data: dict[str, Any],
    test_event_code: str | None,
    action_source: str,
    opt_out: bool = False,
    log_id: str | None = None,
) -> dict[str, Any]:
    from sqlalchemy.exc import IntegrityError

    from src.core.entities.meta_event_log import MetaEventLog
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.meta.hashing import (
        hash_user_data,
    )
    from src.infrastructure.external_services.secrets import get_secrets_manager
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    store_uuid = UUID(store_id)

    # ── 1. Look up store + tenant + freshness-check capi_enabled ──────
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        store_repo = StoreRepository(session)
        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.warning("meta_capi_store_missing", store_id=store_id)
            return {"status": "skipped", "reason": "store_missing"}

        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        if not meta_cfg.get("capi_enabled"):
            return {"status": "skipped", "reason": "capi_disabled"}

        # Meta REQUIRES event_source_url whenever action_source is
        # "website" — without it Events Manager raises "Missing
        # event_source_url" on every conversion and drops the event's
        # match quality. Several enqueue sites have no page context at all
        # (the orphan sweep, order-status triggers, the test-event
        # endpoint) and pass None, so the fallback lives here rather than
        # being repeated at each call site. ``getattr`` because the store
        # object is whatever ``StoreRepository.get_by_id`` handed back —
        # the entity carries ``store_url``, but nothing in this signature
        # guarantees the property exists.
        if not event_source_url and action_source.lower() == "website":
            event_source_url = getattr(store, "store_url", None)

        # Debug-mode auto-attaches the saved test_event_code until
        # debug_mode_expires_at passes. Caller's test_event_code (e.g.
        # the test-event endpoint passing a one-off code) wins if set.
        if not test_event_code:
            expires_raw = meta_cfg.get("debug_mode_expires_at")
            if expires_raw:
                try:
                    expires_at = datetime.fromisoformat(
                        expires_raw.replace("Z", "+00:00")
                    )
                    if expires_at > datetime.now(UTC):
                        test_event_code = meta_cfg.get("test_event_code")
                except (ValueError, AttributeError):
                    pass

        # ── 2. Resolve the outbox row ─────────────────────────────────
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = MetaEventLogRepository(session)

        request_payload = _build_request_payload(
            event_name=event_name,
            event_time=event_time,
            event_source_url=event_source_url,
            action_source=action_source,
            custom_data=custom_data,
            user_data=user_data,
            store=store,
            test_event_code=test_event_code,
            opt_out=opt_out,
        )

        # Attempts already spent on this row. The backoff ladder is indexed
        # off the TOTAL, so a sweep-dispatched retry has to continue the
        # count rather than restart it — otherwise every handoff resets the
        # ladder to its first rung and the event retries at five-minute
        # intervals until it expires.
        row_attempts = 1
        row_id: Any = None

        if log_id is not None:
            # The row was written before this task ran, by the conversion
            # pre-persist in `enqueue_capi_event`. It is OURS — no insert, no
            # dedup race, no ambiguity about which invocation owns the send.
            # (Sweep-claimed rows take the other route: `meta_capi_send_batch`
            # replays their STORED payload rather than rebuilding it here.)
            adopted = await _adopt_owned_row(
                session=session,
                log_id=UUID(log_id),
                request_payload=request_payload,
                store_id=store_id,
                event_id=event_id,
                event_name=event_name,
            )
            if adopted.result is not None:
                return adopted.result
            row_id = UUID(log_id)
            row_attempts = adopted.attempt_count

        if row_id is None:
            try:
                occurred_at = datetime.fromtimestamp(event_time, tz=UTC)
                log_entity = await log_repo.create(
                    MetaEventLog(
                        tenant_id=store.tenant_id,
                        store_id=store_uuid,
                        event_id=event_id,
                        event_name=event_name,
                        event_time=occurred_at,
                        pixel_id=pixel_id,
                        request_payload=request_payload,
                        status=policy.DeliveryStatus.PENDING,
                        priority=policy.priority_for(event_name),
                        expires_at=policy.expires_at_for(occurred_at),
                        # The lease, not a schedule: this task is about to
                        # attempt delivery. If it dies before settling the
                        # row, the lease lapses and the sweep re-delivers.
                        next_retry_at=datetime.now(UTC) + policy.CLAIM_LEASE,
                    )
                )
                await session.commit()
                row_id = log_entity.id
            except IntegrityError:
                # UNIQUE(store_id, event_id) hit — usually the dedup-skip
                # signal. But a row whose prior attempt ended in a recorded
                # 4xx/5xx must stay retryable (the orphan sweep re-enqueues
                # failed Purchases with a rebuilt payload) — adopt that row
                # and resend instead of skipping forever.
                await session.rollback()
                # set_config(..., true) GUCs are transaction-local — the
                # rollback dropped them; re-establish before touching the row.
                await enable_rls_bypass(session)
                await narrow_to_tenant(session, store.tenant_id)

                from sqlalchemy import select as sa_select

                from src.infrastructure.database.models.tenant.meta_event_log import (
                    MetaEventLogModel,
                )

                existing = (
                    await session.execute(
                        sa_select(MetaEventLogModel).where(
                            MetaEventLogModel.store_id == store_uuid,
                            # Must match the UNIQUE key exactly. Without pixel_id
                            # this adopts whichever pixel logged first, so an
                            # enrichment resend for pixel B could overwrite
                            # pixel A's row and then skip B entirely.
                            MetaEventLogModel.pixel_id == pixel_id,
                            MetaEventLogModel.event_id == event_id,
                        )
                    )
                ).scalar_one_or_none()

                completed = (
                    existing is not None and existing.response_status is not None
                )
                prior_failed = completed and existing.response_status >= 400

                # ── Identity enrichment resend ───────────────────────────────
                # The storefront deliberately re-POSTs an earlier event under its
                # ORIGINAL event_id once the shopper types their contact details
                # (`refireFunnelWithIdentity`), so the event Meta already holds
                # picks up em/ph/fn/ln/ct/st/zp. Meta supports exactly this: the
                # same event_id inside the 48h window is deduplicated per Pixel,
                # and the later copy contributes its extra match keys.
                #
                # This branch used to return "duplicate" before any HTTP call, so
                # the entire feature was inert and InitiateCheckout permanently
                # carried zero PII for guests — measurable on the live dataset as
                # 100% fbp/fbc/external_id coverage alongside 0% em/ph.
                enrich = False
                if completed and not prior_failed:
                    stored_user_data = (existing.request_payload or {}).get(
                        "user_data"
                    ) or {}
                    if _adds_match_keys(request_payload["user_data"], stored_user_data):
                        # Age of the event META ALREADY HOLDS — read off the stored
                        # row, NOT off the incoming `event_time`.
                        #
                        # `refireFunnelWithIdentity` re-POSTs the original event_id
                        # with no `event_time`, so `/track` stamps it `now()`. Using
                        # the incoming value made `age_seconds` ~0 on every resend,
                        # so `within_window` was permanently True and this guard
                        # never fired once — the exact case it exists to stop
                        # (a resend outside 48h is a NEW conversion to Meta, not a
                        # merge) was fully open.
                        original_ts = getattr(existing, "event_time", None)
                        if isinstance(original_ts, datetime):
                            if original_ts.tzinfo is None:
                                original_ts = original_ts.replace(tzinfo=UTC)
                            reference_ts = original_ts.timestamp()
                        else:
                            reference_ts = float(event_time)
                        age_seconds = int(datetime.now(UTC).timestamp() - reference_ts)
                        within_window = age_seconds <= _META_DEDUP_WINDOW_SECONDS
                        under_cap = (
                            existing.attempt_count or 0
                        ) < _MAX_ENRICHMENT_RESENDS
                        enrich = within_window and under_cap
                        if not enrich:
                            # Outside the window a resend is NOT deduplicated —
                            # it double-counts the conversion. Never send it.
                            logger.info(
                                "meta_capi_enrichment_skipped",
                                store_id=store_id,
                                event_id=event_id,
                                event_name=event_name,
                                reason=(
                                    "outside_dedup_window"
                                    if not within_window
                                    else "resend_cap_reached"
                                ),
                                age_seconds=age_seconds,
                            )

                if existing is None or (not prior_failed and not enrich):
                    # Sent (2xx) with nothing new to add, or still in-flight
                    # (NULL) — genuine duplicate.
                    logger.info(
                        "meta_capi_dedup_skip",
                        store_id=store_id,
                        event_id=event_id,
                        event_name=event_name,
                    )
                    return {"status": "duplicate", "fbtrace_id": None}

                if enrich:
                    logger.info(
                        "meta_capi_enrichment_resend",
                        store_id=store_id,
                        event_id=event_id,
                        event_name=event_name,
                        attempt=(existing.attempt_count or 0) + 1,
                    )

                # Capture BEFORE commit — post-commit attribute access on an
                # expired instance would trigger a sync lazy refresh and blow
                # up under the async session.
                row_id = existing.id
                row_attempts = (existing.attempt_count or 0) + 1
                previous_status = existing.response_status
                existing.request_payload = request_payload
                existing.attempt_count = row_attempts
                existing.last_error = None
                # Back to in-flight, and re-leased: whatever terminal state
                # the previous attempt left behind is no longer the truth.
                existing.status = policy.DeliveryStatus.PENDING
                existing.failure_kind = None
                existing.next_retry_at = datetime.now(UTC) + policy.CLAIM_LEASE
                await session.commit()
                logger.info(
                    "meta_capi_enrichment_resend_committed"
                    if enrich
                    else "meta_capi_retry_failed_row",
                    store_id=store_id,
                    event_id=event_id,
                    event_name=event_name,
                    previous_status=previous_status,
                )

        # ── 3. Decrypt the access token ───────────────────────────────
        from sqlalchemy import select

        cred_query = (
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.TRACKING)
            .where(ServiceCredential.service_name == ServiceName.META_CAPI)
            .where(ServiceCredential.is_active.is_(True))
        )
        cred = (await session.execute(cred_query)).scalar_one_or_none()
        if cred is None:
            logger.warning(
                "meta_capi_credential_missing",
                store_id=store_id,
                tenant_id=str(store.tenant_id),
            )
            # Settle the row rather than abandoning it. Before the outbox
            # this returned with the row left at response_status NULL, i.e.
            # indistinguishable from in-flight — so a store that had CAPI
            # switched on but never finished connecting Meta accumulated
            # rows that looked pending forever and were counted as such.
            await _settle_row(
                session,
                row_id,
                status=policy.DeliveryStatus.FAILED,
                failure_kind=policy.FailureKind.INVALID_CREDENTIALS,
                error="credential_missing",
                attempt_count=row_attempts,
            )
            return {"status": "skipped", "reason": "credential_missing"}

        secrets = get_secrets_manager()
        try:
            decrypted = await secrets.decrypt(
                cred.credentials_encrypted, cred.encryption_key_id
            )
            access_token = decrypted["access_token"]
        except Exception:  # noqa: BLE001
            logger.exception("meta_capi_decrypt_failed", store_id=store_id)
            sentry_sdk.set_tag("meta_capi.status_class", "decrypt")
            sentry_sdk.capture_message(
                f"meta_capi.decrypt_failed for store {store_id}",
                level="error",
                fingerprint=["meta_capi", "decrypt", store_id],
            )
            await _settle_row(
                session,
                row_id,
                status=policy.DeliveryStatus.FAILED,
                failure_kind=policy.FailureKind.INVALID_CREDENTIALS,
                error="decrypt_error",
                attempt_count=row_attempts,
            )
            return {"status": "failed", "reason": "decrypt_error"}

    # ── 4. POST to Meta — outside the DB session to avoid holding ────
    # ── connections during the network round trip.                    ──
    api_version = settings.meta_graph_api_version
    url = f"https://graph.facebook.com/{api_version}/{pixel_id}/events"

    capi_payload: dict[str, Any] = {
        "data": [
            {
                "event_name": event_name,
                "event_time": event_time,
                "event_id": event_id,
                "action_source": action_source,
                "user_data": hash_user_data(_with_store_phone_cc(user_data, store)),
                "custom_data": custom_data,
            }
        ]
    }
    if event_source_url:
        capi_payload["data"][0]["event_source_url"] = event_source_url
    # Wave 3 Phase 18 — Meta's opt_out lives at the event level (per
    # event in the data[] array), NOT at the top level. Counts the
    # event as a modeled conversion without storing first-party data.
    if opt_out:
        capi_payload["data"][0]["opt_out"] = True
    if test_event_code:
        capi_payload["test_event_code"] = test_event_code

    response_body: dict[str, Any] | None = None
    response_status: int | None = None
    fbtrace_id: str | None = None
    last_error: str | None = None

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = client.post(
                url,
                params={"access_token": access_token},
                json=capi_payload,
            )
        response_status = resp.status_code
        try:
            response_body = resp.json()
            fbtrace_id = (response_body or {}).get("fbtrace_id")
            if not fbtrace_id:
                # Error responses nest it: {"error": {..., "fbtrace_id"}}.
                # Without this fallback every failed row shows "—" in the
                # hub's Recent-events table — exactly when support needs
                # the trace id most.
                error_obj = (response_body or {}).get("error")
                if isinstance(error_obj, dict):
                    fbtrace_id = error_obj.get("fbtrace_id")
        except Exception:  # noqa: BLE001
            response_body = {"raw": resp.text[:500]}
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        # Bubble up so Celery autoretry catches it — but record the attempt
        # first so the dashboard reflects it.
        last_error = f"{type(exc).__name__}: {exc}"
        attempts = row_attempts + task.request.retries
        async with AsyncSessionLocal() as session:
            await enable_rls_bypass(session)
            await narrow_to_tenant(session, store.tenant_id)
            await _record_retryable(
                session,
                row_id,
                kind=policy.FailureKind.TRANSPORT,
                error=last_error,
                attempts=attempts,
                celery_will_retry=_celery_will_retry(task),
                retry_after=None,
            )
            await session.commit()
        if not _celery_will_retry(task):
            # Celery is out of retries. Raising here would only reach the
            # catch-all handler and be buried; the row now carries its own
            # schedule and the delivery sweep owns it from here.
            return {"status": "retry_scheduled", "fbtrace_id": None}
        raise

    # ── 5. Classify, then persist the response with its consequence ──
    #
    # Classification is NOT "4xx is permanent". Meta returns its throttling
    # codes (4/17/32/341/613/80004) and its own transient codes (1/2) as
    # HTTP 400, so the old rule permanently discarded every event a store
    # sent while being rate-limited — the exact moment it had the most
    # traffic worth measuring. See `meta_delivery_policy`.
    kind = policy.classify_status(response_status, response_body)
    attempts = row_attempts + task.request.retries
    celery_left = _celery_will_retry(task)

    if kind is None:
        delivery_status = policy.DeliveryStatus.SENT
        next_retry_at = None
    elif kind.retryable and celery_left:
        # Celery is about to try again; keep the row in-flight under a fresh
        # lease so the sweep does not also pick it up.
        delivery_status = policy.DeliveryStatus.PENDING
        next_retry_at = datetime.now(UTC) + policy.CLAIM_LEASE
    elif kind.retryable:
        delay = policy.next_attempt_delay(
            _sweep_attempt_for(attempts), jitter_seed=row_id.int
        )
        if delay is None:
            delivery_status = policy.DeliveryStatus.DEAD_LETTER
            next_retry_at = None
        else:
            delay = policy.backoff_from_retry_after(
                resp.headers.get("retry-after"), delay
            )
            delivery_status = policy.DeliveryStatus.RETRYING
            next_retry_at = datetime.now(UTC) + timedelta(seconds=delay)
    else:
        delivery_status = policy.DeliveryStatus.FAILED
        next_retry_at = None

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = MetaEventLogRepository(session)
        await log_repo.update_response(
            row_id,
            status=response_status,
            body=_redact_response(response_body),
            fbtrace_id=fbtrace_id,
            sent_at=datetime.now(UTC),
            delivery_status=delivery_status,
            failure_kind=kind.value if kind else None,
            next_retry_at=next_retry_at,
            # Persist the running total. Celery's retries live in the broker
            # and never touch the row, so the sweep would otherwise inherit a
            # count of 1 and restart the ladder from its first rung.
            attempt_count=attempts,
        )
        await session.commit()

    # ── 6. Decide next move based on the classification ──────────────
    if kind is None:
        return {"status": "sent", "fbtrace_id": fbtrace_id}

    if kind.retryable and celery_left:
        # Surface so Celery's retry policy kicks in.
        try:
            raise task.retry(
                countdown=_backoff_from_response(resp.headers, task.request.retries),
                exc=httpx.HTTPStatusError(
                    f"CAPI returned {response_status}",
                    request=resp.request,
                    response=resp,
                ),
            )
        except Exception:
            # task.retry raises Retry — swallow so observability doesn't
            # interpret it as a failed task.
            raise

    if kind.retryable:
        # Celery is spent. The row carries its own schedule now; the sweep
        # owns it. Raising here would only be buried by the catch-all.
        logger.info(
            "meta_capi_handoff_to_sweep",
            store_id=store_id,
            event_name=event_name,
            attempts=attempts,
            failure_kind=kind.value,
            status=delivery_status.value,
        )
        return {
            "status": (
                "retry_scheduled"
                if delivery_status == policy.DeliveryStatus.RETRYING
                else "dead_letter"
            ),
            "fbtrace_id": fbtrace_id,
        }

    # Permanent. Sentry breadcrumb + capture so the merchant / support team
    # can see it without digging through Celery logs. status_class breaks
    # the failure kinds out from one another for finer alert rules.
    status_class = kind.value
    sentry_sdk.set_tag("meta_capi.status_class", status_class)
    sentry_sdk.set_tag("meta_capi.http_status", response_status)
    sentry_sdk.set_tag("meta_capi.failure_kind", kind.value)
    sentry_sdk.add_breadcrumb(
        category="meta_capi",
        level="warning",
        message=f"meta_capi.{status_class} for store {store_id}: {response_status}",
        data={
            "store_id": store_id,
            "pixel_id": pixel_id,
            "event_name": event_name,
            "fbtrace_id": fbtrace_id,
        },
    )
    sentry_sdk.capture_message(
        f"meta_capi.{status_class} for store {store_id}: {response_status}",
        level="warning",
        fingerprint=["meta_capi", status_class, store_id, str(response_status)],
    )
    return {"status": "failed", "fbtrace_id": fbtrace_id}


def _redact_response(body: dict | None) -> dict | None:
    """Strip ``error_user_msg`` and other PII-bearing keys from Meta's response."""
    if body is None:
        return None
    redacted = dict(body)
    for key in ("error_user_msg", "error_user_title", "user_msg"):
        if key in redacted:
            redacted[key] = "[redacted]"
    error = redacted.get("error")
    if isinstance(error, dict):
        cleaned_error = dict(error)
        for key in ("error_user_msg", "error_user_title"):
            if key in cleaned_error:
                cleaned_error[key] = "[redacted]"
        redacted["error"] = cleaned_error
    return redacted


def _backoff_from_response(headers: Any, retries: int) -> int:
    """Pull Retry-After if present, else exponential with cap."""
    try:
        retry_after = int(headers.get("retry-after", "") or 0)
        if retry_after > 0:
            return int(min(retry_after, 300))
    except (ValueError, TypeError):
        pass
    return int(min(2**retries, 300))


# ──────────────────────────────────────────────────────────────────────
# Outbox row lifecycle
# ──────────────────────────────────────────────────────────────────────


class _AdoptedRow(NamedTuple):
    """Outcome of taking ownership of a pre-existing outbox row.

    ``result`` short-circuits the send when the row must not be delivered;
    it is ``None`` when delivery should proceed.
    """

    result: dict[str, Any] | None
    attempt_count: int


def _celery_will_retry(task: Any) -> bool:
    """True while Celery still has in-broker retries left for this task.

    The boundary between the two retry mechanisms. Celery owns the fast
    attempts; once it is spent the outbox sweep owns the slow ones. Getting
    this wrong in either direction is a real defect: say yes when Celery is
    finished and the event is buried by the catch-all handler, say no while
    it still has budget and the event is scheduled twice.

    An unreadable retry count resolves to False — hand the event to the
    sweep, which will schedule it — rather than True, which would promise a
    retry that nothing performs.
    """
    retries = getattr(getattr(task, "request", None), "retries", 0)
    try:
        return int(retries or 0) < _CELERY_MAX_RETRIES
    except (TypeError, ValueError):
        return False


async def _settle_row(
    session: Any,
    row_id: Any,
    *,
    status: str,
    failure_kind: str | None,
    error: str | None,
    attempt_count: int,
) -> None:
    """Move a row to a terminal state. Never raises.

    Settling is bookkeeping about an event that has already had its outcome
    decided — letting it raise would turn a recorded failure into an
    unrecorded one, which is strictly worse.
    """
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )

    try:
        await MetaEventLogRepository(session).update_error(
            row_id,
            error=error or "",
            attempt_count=attempt_count,
            delivery_status=status,
            failure_kind=failure_kind,
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("meta_capi_settle_failed", row_id=str(row_id), status=status)


async def _record_retryable(
    session: Any,
    row_id: Any,
    *,
    kind: str,
    error: str,
    attempts: int,
    celery_will_retry: bool,
    retry_after: Any,
) -> None:
    """Record a retryable failure and schedule whatever comes next.

    While Celery still has budget the row stays ``pending`` under a fresh
    lease, so the sweep leaves it alone. Once Celery is spent the row moves
    to ``retrying`` with a real ``next_retry_at`` — or to ``dead_letter``
    when the ladder is exhausted.
    """
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )

    now = datetime.now(UTC)
    if celery_will_retry:
        status = policy.DeliveryStatus.PENDING
        next_retry_at = now + policy.CLAIM_LEASE
    else:
        delay = policy.next_attempt_delay(
            _sweep_attempt_for(attempts), jitter_seed=getattr(row_id, "int", 0)
        )
        if delay is None:
            status = policy.DeliveryStatus.DEAD_LETTER
            next_retry_at = None
        else:
            status = policy.DeliveryStatus.RETRYING
            next_retry_at = now + timedelta(
                seconds=policy.backoff_from_retry_after(retry_after, delay)
            )

    try:
        await MetaEventLogRepository(session).update_error(
            row_id,
            error=error,
            attempt_count=attempts,
            delivery_status=status,
            failure_kind=kind,
            next_retry_at=next_retry_at,
        )
    except Exception:  # noqa: BLE001
        logger.exception("meta_capi_record_retryable_failed", row_id=str(row_id))


async def _adopt_owned_row(
    *,
    session: Any,
    log_id: Any,
    request_payload: dict[str, Any],
    store_id: str,
    event_id: str,
    event_name: str,
) -> _AdoptedRow:
    """Take ownership of a row this task was handed by id.

    The conversion pre-persist in ``enqueue_capi_event`` hands a ``log_id``
    down: the row already exists and this invocation owns it, so there is no
    insert and no dedup race — but it still has to check two things the
    caller could not know when it wrote the row:

      * the row may have been settled between claim and execution (a
        concurrent enrichment resend, an operator marking it abandoned);
      * the row may have crossed ``expires_at`` while it sat in the queue,
        after which sending it would DOUBLE-COUNT the conversion rather
        than merge with it.
    """
    from sqlalchemy import select as sa_select

    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )

    row = (
        await session.execute(
            sa_select(MetaEventLogModel).where(MetaEventLogModel.id == log_id)
        )
    ).scalar_one_or_none()

    if row is None:
        # Retention pruned it, or the store was deleted. Nothing to deliver
        # and nothing to record against.
        logger.warning(
            "meta_capi_owned_row_missing", row_id=str(log_id), store_id=store_id
        )
        return _AdoptedRow({"status": "skipped", "reason": "row_missing"}, 1)

    if row.status in policy.TERMINAL_STATUSES:
        logger.info(
            "meta_capi_owned_row_settled",
            row_id=str(log_id),
            event_id=event_id,
            event_name=event_name,
            status=row.status,
        )
        return _AdoptedRow(
            {"status": "duplicate", "reason": row.status}, row.attempt_count or 1
        )

    if policy.is_expired(row.event_time):
        logger.info(
            "meta_capi_event_expired",
            row_id=str(log_id),
            store_id=store_id,
            event_id=event_id,
            event_name=event_name,
        )
        await _settle_row(
            session,
            log_id,
            status=policy.DeliveryStatus.EXPIRED,
            failure_kind=None,
            error="expired_before_delivery",
            attempt_count=row.attempt_count or 1,
        )
        return _AdoptedRow({"status": "expired"}, row.attempt_count or 1)

    # Re-lease and refresh the payload. The payload is rebuilt from the task
    # kwargs on every attempt, so a resend always carries the identity the
    # platform knows NOW — which is the point of the enrichment path.
    attempts = row.attempt_count or 1
    row.request_payload = request_payload
    row.status = policy.DeliveryStatus.PENDING
    row.next_retry_at = datetime.now(UTC) + policy.CLAIM_LEASE
    if row.expires_at is None:
        row.expires_at = policy.expires_at_for(row.event_time)
    await session.commit()
    return _AdoptedRow(None, attempts)


# ──────────────────────────────────────────────────────────────────────
# Delivery sweep — the outbox's own retry loop
# ──────────────────────────────────────────────────────────────────────

# Rows claimed per pass. 200 x every-2-minutes = 6k events/hour of recovery
# capacity, which comfortably outpaces any backlog the platform can build at
# its current volume, while keeping one pass short enough that the claim
# lease is never the binding constraint.
_CLAIM_BATCH = 200

# Events per outbound CAPI request. Meta permits up to 1000; we send 100.
#
# The reason is not payload size, it is blast radius. Meta rejects a batch
# as a whole, so one malformed event fails its 99 neighbours, and the
# recovery is to re-send them individually. At 100 that costs at most 100
# extra requests; at 1000 it costs 1000 — and a poisoned batch is exactly
# the situation where you are already in trouble. 100 still collapses a
# thousand-event backlog into ten requests instead of a thousand.
_SEND_BATCH = 100

# Below this, batching buys nothing worth the extra task hop.
_BATCH_MIN = 2


@celery_app.task(
    name="tasks.meta_capi_deliver_due",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def meta_capi_deliver_due(self: Any) -> dict[str, int]:
    """Expire what may no longer be sent, then re-deliver what is due.

    The outbox's own loop, and the half of the retry story Celery cannot
    cover: Celery retries live in the broker and last minutes, so a Meta
    outage measured in hours used to end with every event buried by the
    catch-all handler. Rows that fall out of Celery's budget land here with
    a schedule attached, and this walks the ladder.

    Runs every two minutes. That is not a latency target — the live path
    delivers in seconds — it is a recovery cadence: the first ladder rung is
    five minutes, so polling faster only spends queries.
    """
    try:
        result: dict[str, int] = _run_async(_deliver_due())
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta_capi_deliver_due_failed")
        raise self.retry(exc=exc) from exc


async def _deliver_due() -> dict[str, int]:
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.meta_event_log_repository import (
        MetaEventLogRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    stats = {"expired": 0, "claimed": 0, "batches": 0}
    now = datetime.now(UTC)

    async with AsyncSessionLocal() as session:
        # Cross-tenant by design, exactly like the orphan sweep. Isolation
        # is not weakened: each claimed row carries its own tenant_id, and
        # the sender re-narrows to it before touching anything.
        await enable_rls_bypass(session)
        repo = MetaEventLogRepository(session)

        # Expire FIRST. A row past its window must never be claimable — a
        # resend outside Meta's 48h dedup window is a second conversion, not
        # a merge, so delivering it corrupts the merchant's revenue figures
        # in the direction that looks like success.
        stats["expired"] = await repo.expire_overdue(now=now)
        await session.commit()

        claimed = await repo.claim_due(
            now=now,
            lease_until=now + policy.CLAIM_LEASE,
            limit=_CLAIM_BATCH,
        )
        await session.commit()

    if not claimed:
        return stats
    stats["claimed"] = len(claimed)

    # Group by everything that must be identical inside one CAPI request:
    # the pixel it goes to, the store whose token signs it, and the test
    # code (top-level in Meta's envelope, so rows with different codes
    # cannot share a request).
    groups: dict[tuple[str, str, str | None], list[Any]] = {}
    for row in claimed:
        key = (
            str(row.store_id),
            row.pixel_id,
            (row.request_payload or {}).get("test_event_code"),
        )
        groups.setdefault(key, []).append(row)

    for (store_id, pixel_id, test_code), rows in groups.items():
        for chunk in (
            rows[i : i + _SEND_BATCH] for i in range(0, len(rows), _SEND_BATCH)
        ):
            stats["batches"] += 1
            meta_capi_send_batch.apply_async(
                kwargs={
                    "store_id": store_id,
                    "pixel_id": pixel_id,
                    "row_ids": [str(r.id) for r in chunk],
                    "test_event_code": test_code,
                },
                # Priority follows the most urgent event in the chunk: a
                # batch containing a Purchase is a Purchase batch.
                queue=(
                    policy.QUEUE_CONVERSION
                    if any(r.priority == policy.PRIORITY_CONVERSION for r in chunk)
                    else policy.QUEUE_STANDARD
                ),
            )

    logger.info("meta_capi_deliver_due_done", **stats)
    return stats


@celery_app.task(
    name="tasks.meta_capi_send_batch",
    bind=True,
    max_retries=0,
    acks_late=True,
)
def meta_capi_send_batch(
    self: Any,
    *,
    store_id: str,
    pixel_id: str,
    row_ids: list[str],
    test_event_code: str | None = None,
) -> dict[str, Any]:
    """Re-deliver already-persisted events in one request.

    Sends the payload the outbox RECORDED, verbatim — it does not rebuild
    it. That is the whole difference between a replay and a re-derivation:
    the stored ``user_data`` is already hashed, the stored ``event_time`` is
    when the conversion actually happened, and the stored ``event_id`` is
    the one Meta already knows. Re-deriving any of those would turn a merge
    into a duplicate.

    ``max_retries=0`` on purpose: retry scheduling belongs to the outbox
    rows, which already carry a ladder and a deadline. A second, invisible
    retry policy layered on top is how you get an event delivered six times.
    """
    try:
        result: dict[str, Any] = _run_async(
            _send_batch(
                store_id=store_id,
                pixel_id=pixel_id,
                row_ids=row_ids,
                test_event_code=test_event_code,
            )
        )
        return result
    except Exception:  # noqa: BLE001 — a buried batch must not bury the row
        logger.exception(
            "meta_capi_send_batch_unexpected_error",
            store_id=store_id,
            pixel_id=pixel_id,
            count=len(row_ids),
        )
        # Rows keep their lease; it lapses and the next sweep re-claims them.
        return {"status": "failed", "sent": 0}


async def _send_batch(
    *,
    store_id: str,
    pixel_id: str,
    row_ids: list[str],
    test_event_code: str | None,
) -> dict[str, Any]:
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    if not row_ids:
        return {"status": "skipped", "sent": 0}

    ids = [UUID(r) for r in row_ids]

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        store = await StoreRepository(session).get_by_id(UUID(store_id))
        if store is None:
            return {"status": "skipped", "reason": "store_missing", "sent": 0}

        meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
        if not meta_cfg.get("capi_enabled"):
            # Re-checked at execution time, same as the single-event path: a
            # merchant who switched CAPI off while these sat in the queue
            # must not have them delivered anyway.
            await narrow_to_tenant(session, store.tenant_id)
            await _settle_many(
                session,
                ids,
                status=policy.DeliveryStatus.SKIPPED,
                failure_kind=None,
                error="capi_disabled",
            )
            return {"status": "skipped", "reason": "capi_disabled", "sent": 0}

        await narrow_to_tenant(session, store.tenant_id)
        rows = list(
            (
                await session.execute(
                    select(MetaEventLogModel).where(MetaEventLogModel.id.in_(ids))
                )
            )
            .scalars()
            .all()
        )

        # Drop anything that settled or expired between claim and execution.
        now = datetime.now(UTC)
        live: list[Any] = []
        expired: list[Any] = []
        for row in rows:
            if row.status in policy.TERMINAL_STATUSES:
                continue
            if policy.is_expired(row.event_time, now=now):
                expired.append(row.id)
            else:
                live.append(row)
        if expired:
            await _settle_many(
                session,
                expired,
                status=policy.DeliveryStatus.EXPIRED,
                failure_kind=None,
                error="expired_before_delivery",
            )
        if not live:
            return {"status": "skipped", "reason": "nothing_live", "sent": 0}

        access_token = await _decrypt_capi_token(session, store)
        if not access_token:
            await _settle_many(
                session,
                [r.id for r in live],
                status=policy.DeliveryStatus.FAILED,
                failure_kind=policy.FailureKind.INVALID_CREDENTIALS,
                error="credential_missing",
            )
            return {"status": "failed", "reason": "credential_missing", "sent": 0}

        payloads = [(row.id, _capi_entry(row)) for row in live]
        tenant_id = store.tenant_id

    status, body, fbtrace_id, retry_after, transport_error = await _post_capi_batch(
        pixel_id=pixel_id,
        access_token=access_token,
        entries=[entry for _, entry in payloads],
        test_event_code=test_event_code,
    )

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, tenant_id)

        if transport_error is not None:
            await _reschedule_many(
                session,
                [rid for rid, _ in payloads],
                kind=policy.FailureKind.TRANSPORT,
                error=transport_error,
                retry_after=None,
            )
            return {"status": "failed", "reason": "transport", "sent": 0}

        kind = policy.classify_status(status, body)
        if kind is None:
            await _settle_many(
                session,
                [rid for rid, _ in payloads],
                status=policy.DeliveryStatus.SENT,
                failure_kind=None,
                error=None,
                response_status=status,
                response_body=_redact_response(body),
                fbtrace_id=fbtrace_id,
            )
            logger.info(
                "meta_capi_batch_sent",
                store_id=store_id,
                pixel_id=pixel_id,
                count=len(payloads),
            )
            return {"status": "sent", "sent": len(payloads)}

        if kind.retryable:
            await _reschedule_many(
                session,
                [rid for rid, _ in payloads],
                kind=kind,
                error=f"HTTP {status}",
                retry_after=retry_after,
                response_status=status,
                response_body=_redact_response(body),
            )
            return {"status": "retry_scheduled", "sent": 0}

        # Permanent rejection. Meta rejects a batch as a unit, so ONE bad
        # event fails the whole request — failing all of them here would
        # discard good conversions because of a neighbour. Re-send them one
        # at a time so the poison is isolated to its own row.
        if len(payloads) > 1:
            logger.warning(
                "meta_capi_batch_rejected_splitting",
                store_id=store_id,
                pixel_id=pixel_id,
                count=len(payloads),
                http_status=status,
            )
            await _reschedule_many(
                session,
                [rid for rid, _ in payloads],
                kind=policy.FailureKind.SERVER_ERROR,
                error=f"batch rejected HTTP {status}, resending individually",
                retry_after=None,
                due_now=True,
            )
            for rid, _ in payloads:
                meta_capi_send_batch.apply_async(
                    kwargs={
                        "store_id": store_id,
                        "pixel_id": pixel_id,
                        "row_ids": [str(rid)],
                        "test_event_code": test_event_code,
                    },
                    queue=policy.QUEUE_STANDARD,
                )
            return {"status": "split", "sent": 0}

        await _settle_many(
            session,
            [rid for rid, _ in payloads],
            status=policy.DeliveryStatus.FAILED,
            failure_kind=kind,
            error=f"HTTP {status}",
            response_status=status,
            response_body=_redact_response(body),
            fbtrace_id=fbtrace_id,
        )
        sentry_sdk.set_tag("meta_capi.failure_kind", kind.value)
        sentry_sdk.capture_message(
            f"meta_capi.{kind.value} for store {store_id}: {status}",
            level="warning",
            fingerprint=["meta_capi", kind.value, store_id, str(status)],
        )
        return {"status": "failed", "sent": 0}


def _capi_entry(row: Any) -> dict[str, Any]:
    """One ``data[]` entry rebuilt from a stored outbox row.

    ``event_id`` and ``event_time`` come off the ROW, never off the clock:
    the id is what Meta deduplicates against and the time is when the
    conversion happened. Stamping either with "now" on a retry would turn a
    merge into a brand-new conversion at the wrong moment.
    """
    stored = row.request_payload or {}
    entry: dict[str, Any] = {
        "event_name": row.event_name,
        "event_time": stored.get("event_time")
        or int(_as_utc(row.event_time).timestamp()),
        "event_id": row.event_id,
        "action_source": stored.get("action_source") or "website",
        # Already hashed when the row was written — hashing again would
        # produce a match key for a shopper who does not exist.
        "user_data": stored.get("user_data") or {},
        "custom_data": stored.get("custom_data") or {},
    }
    if stored.get("event_source_url"):
        entry["event_source_url"] = stored["event_source_url"]
    if stored.get("opt_out"):
        entry["opt_out"] = True
    return entry


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=UTC)


async def _post_capi_batch(
    *,
    pixel_id: str,
    access_token: str,
    entries: list[dict[str, Any]],
    test_event_code: str | None,
) -> tuple[int, dict[str, Any] | None, str | None, Any, str | None]:
    """POST one CAPI request. Returns (status, body, fbtrace, retry_after, err)."""
    url = (
        f"https://graph.facebook.com/{settings.meta_graph_api_version}"
        f"/{pixel_id}/events"
    )
    payload: dict[str, Any] = {"data": entries}
    if test_event_code:
        payload["test_event_code"] = test_event_code

    try:
        with httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0)) as client:
            resp = client.post(url, params={"access_token": access_token}, json=payload)
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        return 0, None, None, None, f"{type(exc).__name__}: {exc}"

    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {"raw": resp.text[:500]}

    fbtrace_id = (body or {}).get("fbtrace_id")
    if not fbtrace_id:
        error_obj = (body or {}).get("error")
        if isinstance(error_obj, dict):
            fbtrace_id = error_obj.get("fbtrace_id")

    return resp.status_code, body, fbtrace_id, resp.headers.get("retry-after"), None


async def _settle_many(
    session: Any,
    row_ids: list[Any],
    *,
    status: str,
    failure_kind: Any,
    error: str | None,
    response_status: int | None = None,
    response_body: dict | None = None,
    fbtrace_id: str | None = None,
) -> None:
    """Move a set of rows to one terminal state, in a single statement."""
    from sqlalchemy import update

    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )

    if not row_ids:
        return
    values: dict[str, Any] = {
        "status": str(status),
        "failure_kind": str(failure_kind) if failure_kind else None,
        "next_retry_at": None,
    }
    if error is not None:
        values["last_error"] = error[:500]
    if response_status is not None:
        values["response_status"] = response_status
        values["response_body"] = response_body
        values["fbtrace_id"] = fbtrace_id
        values["sent_at"] = datetime.now(UTC)
    try:
        await session.execute(
            update(MetaEventLogModel)
            .where(MetaEventLogModel.id.in_(row_ids))
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("meta_capi_settle_many_failed", count=len(row_ids))


async def _reschedule_many(
    session: Any,
    row_ids: list[Any],
    *,
    kind: Any,
    error: str,
    retry_after: Any,
    response_status: int | None = None,
    response_body: dict | None = None,
    due_now: bool = False,
) -> None:
    """Put a set of rows back on the ladder, or dead-letter the spent ones.

    Per-row rather than one bulk UPDATE because the next delay depends on
    each row's own attempt count, and collapsing that to a single value is
    how a retry storm turns into a synchronised one.
    """
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )

    if not row_ids:
        return
    now = datetime.now(UTC)
    try:
        rows = (
            (
                await session.execute(
                    select(MetaEventLogModel).where(MetaEventLogModel.id.in_(row_ids))
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            attempts = row.attempt_count or 1
            if due_now:
                row.status = policy.DeliveryStatus.RETRYING
                row.next_retry_at = now
            else:
                delay = policy.next_attempt_delay(
                    _sweep_attempt_for(attempts), jitter_seed=row.id.int
                )
                if delay is None:
                    row.status = policy.DeliveryStatus.DEAD_LETTER
                    row.next_retry_at = None
                else:
                    row.status = policy.DeliveryStatus.RETRYING
                    row.next_retry_at = now + timedelta(
                        seconds=policy.backoff_from_retry_after(retry_after, delay)
                    )
            row.failure_kind = str(kind) if kind else None
            row.last_error = error[:500]
            if response_status is not None:
                row.response_status = response_status
                row.response_body = response_body
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        logger.exception("meta_capi_reschedule_many_failed", count=len(row_ids))


# ──────────────────────────────────────────────────────────────────────
# Cron sweep — recover orphaned Purchases (plan §12 risks table)
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.meta_capi_sweep_orphaned_purchases",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def meta_capi_sweep_orphaned_purchases(
    self: Any, lookback_hours: int = 24
) -> dict[str, int]:
    """Find paid orders without a Purchase ``meta_event_log`` row → enqueue.

    Catches the case where a payment webhook silently failed (network
    blip during the funnel-event side-effect, worker crash mid-fanout,
    Meta's API was down, etc). Runs hourly via Celery Beat.
    """
    try:
        result: dict[str, int] = _run_async(_sweep_orphans(lookback_hours))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta_capi_sweep_failed")
        raise self.retry(exc=exc) from exc


async def _sweep_orphans(lookback_hours: int) -> dict[str, int]:
    from types import SimpleNamespace

    from sqlalchemy import or_, select

    from src.application.services.meta_capi_purchase_dispatcher import (
        _build_custom_data_from_order,
        _build_user_data_from_order,
        _guard_conversion_payload,
        fill_identity_from_customer,
        resolve_catalog_ids,
    )
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    stats = {"scanned": 0, "enqueued": 0}

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        # Find paid orders within the lookback window. We deliberately
        # don't restrict to a payment_status enum string here because
        # different gateways use different terminal states — instead
        # we look at orders with a non-null paid_at.
        # Note: leaving the heavy join to Postgres rather than building
        # a NOT EXISTS in Python — index `idx_meta_event_log_store_event`
        # supports the lookup efficiently.
        order_query = (
            select(OrderModel.id, OrderModel.store_id, OrderModel.tenant_id)
            .join(
                StoreModel,
                StoreModel.id == OrderModel.store_id,
            )
            .where(_sweep_order_filter(cutoff))
            # Filter to stores that have CAPI enabled — settings is JSONB.
            .where(
                StoreModel.settings["tracking"]["meta"]["capi_enabled"].as_string()
                == "true"
            )
            .limit(500)
        )

        try:
            orders = (await session.execute(order_query)).all()
        except Exception:  # noqa: BLE001
            # JSONB path operator differs across SQLAlchemy/PG versions.
            # Fall back to a Python-side filter — slower but correct.
            logger.exception("meta_capi_sweep_jsonb_fallback")
            orders = await _orders_paid_since_python_filter(session, cutoff)

        if not orders:
            return stats

        order_ids = [str(o.id) for o in orders]
        existing_query = select(
            MetaEventLogModel.event_id, MetaEventLogModel.pixel_id
        ).where(
            MetaEventLogModel.event_name == "Purchase",
            MetaEventLogModel.event_id.in_(order_ids),
            # A recorded 4xx/5xx does NOT count as "sent" — the send task
            # adopts + retries failed rows on re-enqueue. NULL status DOES
            # count (in-flight or pending; Celery owns its own retries).
            or_(
                MetaEventLogModel.response_status.is_(None),
                MetaEventLogModel.response_status < 400,
            ),
        )
        # (event_id, pixel_id) pairs — NOT event_id alone. The log is keyed
        # per pixel now, so an order fanned out to three pixels has three
        # rows. Collapsing to event_id would let ONE pixel's success mask the
        # other two failing, and the sweep would skip the order forever —
        # exactly the orphan it exists to recover.
        existing = {
            (row[0], row[1]) for row in (await session.execute(existing_query)).all()
        }

        for o in orders:
            stats["scanned"] += 1
            order_full = (
                await session.execute(select(OrderModel).where(OrderModel.id == o.id))
            ).scalar_one_or_none()
            if order_full is None:
                continue
            store_full = (
                await session.execute(
                    select(StoreModel).where(StoreModel.id == order_full.store_id)
                )
            ).scalar_one_or_none()
            if store_full is None:
                continue
            meta_cfg = ((store_full.settings or {}).get("tracking") or {}).get(
                "meta"
            ) or {}
            pixels = resolve_pixels(meta_cfg, mode="capi")
            if not pixels:
                continue

            # Recover only the pixels that are actually missing this Purchase.
            #
            # This check moved AFTER the store load, because which pixels an
            # order owes can only be known once the store's config is
            # resolved. The cost is an order+store fetch for orders that turn
            # out to be fully sent; acceptable at 500 rows/hour, and worth
            # revisiting with a batched store load if the sweep ever widens.
            pixels = [
                p for p in pixels if (str(order_full.id), p.pixel_id) not in existing
            ]
            if not pixels:
                continue

            # COD orders are now in scope (see `_sweep_order_filter`), but only
            # as the backstop for stores that have NOT chosen a Purchase
            # trigger. When `purchase_trigger` is configured the merchant has
            # said when a COD sale counts — on confirmation, on delivery — and
            # `meta_capi_status_event_handler` owns that moment. Firing here
            # too would report unpaid orders as revenue and pre-empt their
            # choice.
            # Gate on a trigger the status handler can ACTUALLY act on, not on
            # truthiness. `meta_capi_status_event_handler` only fires when the
            # trigger is in `_VALID_TRIGGER_STATUSES`; a value outside that set
            # — `store.settings` is JSONB and is also written by SQLAdmin, the
            # MCP and seed scripts, none of which go through the hub's Literal
            # type — would make the sweep stand down for a handler that never
            # fires, and the COD Purchase would be lost entirely.
            if getattr(order_full, "paid_at", None) is None:
                from src.infrastructure.events.handlers.meta_capi_status_event_handler import (  # noqa: E501
                    _VALID_TRIGGER_STATUSES,
                )

                if meta_cfg.get("purchase_trigger") in _VALID_TRIGGER_STATUSES:
                    continue

            # Build the SAME rich payload the webhook path sends. The
            # sweep used to fire ``user_data={}`` as "best-effort" — but
            # Meta hard-rejects events with zero customer information
            # parameters (400, error_subcode 2804050), so a minimal
            # payload isn't degraded match quality, it's a guaranteed
            # failure. OrderModel stores the entity's ``metadata`` under
            # ``extra_data`` — adapt before handing to the shared
            # builders (their ``getattr(order, "metadata")`` on an ORM
            # model would resolve to SQLAlchemy's MetaData registry).
            order_view = SimpleNamespace(
                id=order_full.id,
                store_id=order_full.store_id,
                customer_id=order_full.customer_id,
                shipping_address=order_full.shipping_address,
                metadata=order_full.extra_data or {},
                line_items=order_full.line_items,
                total=order_full.total,
                currency=order_full.currency,
                utm_source=order_full.utm_source,
                utm_medium=order_full.utm_medium,
                utm_campaign=order_full.utm_campaign,
                utm_term=order_full.utm_term,
                utm_content=order_full.utm_content,
                campaign_id=getattr(order_full, "campaign_id", None),
                # A swept Purchase must be identical to the one the webhook
                # would have sent — same match keys, same catalog ids. These
                # three were missing, so recovered conversions silently
                # carried a weaker identity than the ones that worked:
                #   session_fingerprint → external_id (guest session stitch)
                #   attribution         → fbc rebuilt from the stored fbclid
                #   store_id            → scopes the customer/email lookup
                session_fingerprint=getattr(order_full, "session_fingerprint", None),
                attribution=getattr(order_full, "attribution", None),
            )
            user_data = _build_user_data_from_order(order_view)
            await fill_identity_from_customer(session, user_data, order_view)
            # Same catalog-id resolution as the webhook path — otherwise a
            # swept Purchase would carry different content_ids from the one
            # the browser/webhook sent for the same order.
            custom_data = _build_custom_data_from_order(
                order_view, await resolve_catalog_ids(session, order_view)
            )

            # Same value/currency contract as the webhook path — a recovered
            # conversion Meta cannot value is not worth recovering.
            if not _guard_conversion_payload(custom_data, "Purchase", order_view):
                continue

            if not any(user_data.values()):
                # No match key at all (no phone/email/name/ip/fbp/…) —
                # Meta will 400 it deterministically; skip instead of
                # burning an attempt and poisoning the dedup row.
                logger.warning(
                    "meta_capi_sweep_no_match_keys",
                    order_id=str(order_full.id),
                    store_id=str(order_full.store_id),
                )
                continue

            for pixel in pixels:
                # `session=None`: no pre-persist here. This sweep IS the
                # durable loop for Purchase — it rebuilds the event from the
                # `orders` row every hour — so a dropped broker message is
                # already recovered by the next pass, and writing an outbox
                # row first would only add a second recovery path over the
                # top of a working one. The call still routes through the
                # shared door for queue and priority.
                await enqueue_capi_event(
                    session=None,
                    store=store_full,
                    tenant_id=getattr(store_full, "tenant_id", None),
                    store_id=str(order_full.store_id),
                    pixel_id=pixel.pixel_id,
                    event_name="Purchase",
                    event_id=str(order_full.id),
                    event_time=int(
                        (order_full.paid_at or datetime.now(UTC)).timestamp()
                    ),
                    event_source_url=None,
                    user_data=user_data,
                    custom_data=custom_data,
                    action_source="website",
                )
            stats["enqueued"] += 1

    if stats["enqueued"]:
        logger.info("meta_capi_sweep_enqueued", **stats)
    return stats


async def _orders_paid_since_python_filter(session: Any, cutoff: datetime) -> list[Any]:
    """Fallback when the JSONB path operator isn't available (e.g. SQLite)."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    rows = (
        await session.execute(
            select(OrderModel.id, OrderModel.store_id, OrderModel.tenant_id)
            .where(_sweep_order_filter(cutoff))
            .limit(500)
        )
    ).all()
    if not rows:
        return []

    store_ids = list({r.store_id for r in rows})
    stores = {
        s.id: s
        for s in (
            await session.execute(
                select(StoreModel).where(StoreModel.id.in_(store_ids))
            )
        )
        .scalars()
        .all()
    }
    out = []
    for r in rows:
        s = stores.get(r.store_id)
        if not s:
            continue
        meta_cfg = ((s.settings or {}).get("tracking") or {}).get("meta") or {}
        if meta_cfg.get("capi_enabled"):
            out.append(r)
    return out


# ──────────────────────────────────────────────────────────────────────
# Event Match Quality poll — the measurement loop
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.meta_match_quality_poll",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def meta_match_quality_poll(self: Any, lookback_hours: int = 24) -> dict[str, int]:
    """Snapshot every connected store's EMQ from Meta's Dataset Quality API.

    This is the measurement loop the platform never had. Without it
    ``MetaMatchQualityService.get_snapshots`` has nothing to read, the hub
    renders an empty state, and no signal-quality change can be shown to have
    worked — Meta scores over a rolling window, so proving an improvement
    means comparing the same event across polls.

    **Every 6 hours, not hourly.** The Marketing API rate-limits per app, and
    this runs once per capi-enabled store per pixel; EMQ moves on a rolling
    multi-day window, so hourly polling would spend quota to re-read a number
    that has barely changed.

    Only polls stores that actually fired an event recently — a dormant store
    has no new data and would burn quota for a repeated snapshot.

    Never raises per-store: one store's expired token must not stop the sweep.
    """
    try:
        result: dict[str, int] = _run_async(_poll_match_quality(lookback_hours))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta_match_quality_poll_failed")
        raise self.retry(exc=exc) from exc


async def _poll_match_quality(lookback_hours: int) -> dict[str, int]:
    from sqlalchemy import select

    from src.application.services.meta_match_quality_service import (
        poll_match_quality,
    )
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.repositories.meta_match_quality_repository import (
        MetaMatchQualityRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    stats = {"stores": 0, "polled": 0, "snapshots": 0, "skipped": 0}
    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        # Stores that sent Meta an event inside the window — a store with no
        # recent traffic has no new score to read.
        active_store_ids = (
            (
                await session.execute(
                    select(MetaEventLogModel.store_id)
                    .where(MetaEventLogModel.created_at >= cutoff)
                    .distinct()
                    .limit(500)
                )
            )
            .scalars()
            .all()
        )
        if not active_store_ids:
            return stats

        stores = (
            (
                await session.execute(
                    select(StoreModel).where(StoreModel.id.in_(active_store_ids))
                )
            )
            .scalars()
            .all()
        )

        for store in stores:
            stats["stores"] += 1
            meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
            pixels = resolve_pixels(meta_cfg, mode="capi")
            if not pixels:
                stats["skipped"] += 1
                continue

            access_token = await _decrypt_capi_token(session, store)
            if not access_token:
                stats["skipped"] += 1
                continue

            repo = MetaMatchQualityRepository(session)
            for pixel in pixels:
                snapshots = await poll_match_quality(
                    store_id=store.id,
                    pixel_id=pixel.pixel_id,
                    access_token=access_token,
                )
                if not snapshots:
                    continue
                stats["polled"] += 1
                try:
                    await narrow_to_tenant(session, store.tenant_id)
                    stats["snapshots"] += await repo.record(
                        tenant_id=store.tenant_id,
                        store_id=store.id,
                        snapshots=snapshots,
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001 — one store must not stop the sweep
                    await session.rollback()
                    logger.exception(
                        "meta_match_quality_record_failed",
                        store_id=str(store.id),
                        pixel_id=pixel.pixel_id,
                    )
                await enable_rls_bypass(session)

    logger.info("meta_match_quality_poll_done", **stats)
    return stats


async def _decrypt_capi_token(session: Any, store: Any) -> str | None:
    """Fetch + decrypt this store's CAPI access token, or None."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets import get_secrets_manager

    try:
        cred = (
            await session.execute(
                select(ServiceCredential)
                .where(
                    ServiceCredential.tenant_id == store.tenant_id,
                    ServiceCredential.service_type == ServiceType.TRACKING,
                    ServiceCredential.service_name == ServiceName.META_CAPI,
                    ServiceCredential.is_active.is_(True),
                )
                .order_by(ServiceCredential.updated_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if cred is None:
            return None
        secrets = get_secrets_manager()
        decrypted = await secrets.decrypt(
            cred.credentials_encrypted, cred.encryption_key_id
        )
        token = decrypted.get("access_token")
        return str(token) if token else None
    except Exception:  # noqa: BLE001
        logger.warning("meta_match_quality_token_unavailable", store_id=str(store.id))
        return None


# ──────────────────────────────────────────────────────────────────────
# Retention
# ──────────────────────────────────────────────────────────────────────


# `meta_event_log` is append-mostly and had NO retention policy: one row per
# event per pixel, kept forever. It is a delivery/debug log, not a business
# record — the conversions themselves live in `orders` — so an unbounded
# table only makes the dedup lookups and the failure sweeps slower over time.
#
# 90 days keeps a full quarter for support and for the orphan sweep (which
# only looks back 24h anyway), while bounding growth.
#
# Now read from `settings.ad_event_log_retention_days`, shared with the TikTok
# rail: the two logs hold the same class of data and used to disagree (90 here
# vs 180 there) for no reason anyone recorded. The period is a legal decision
# — see the setting's comment.
def _event_log_retention_days() -> int:
    from src.config.settings import get_settings

    return get_settings().ad_event_log_retention_days


# EMQ snapshots are much smaller (a handful of rows per store per poll) and
# their value IS the history — proving a change moved the score needs a long
# baseline. 180 days keeps a season-over-season comparison.
_MATCH_QUALITY_RETENTION_DAYS = 180


@celery_app.task(
    name="tasks.meta_tracking_prune",
    bind=True,
    max_retries=1,
    default_retry_delay=600,
)
def meta_tracking_prune(self: Any) -> dict[str, int]:
    """Delete Meta tracking rows past their retention window.

    Deletes in bounded batches rather than one statement: a single unbounded
    DELETE on a table this size takes a long-held lock and a large WAL burst,
    and this is housekeeping — it can take as long as it likes.
    """
    try:
        result: dict[str, int] = _run_async(_prune_tracking_rows())
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("meta_tracking_prune_failed")
        raise self.retry(exc=exc) from exc


async def _prune_tracking_rows() -> dict[str, int]:
    from sqlalchemy import delete, func, select

    from src.core.services.meta_delivery_policy import TERMINAL_STATUSES
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.meta_event_log import (
        MetaEventLogModel,
    )
    from src.infrastructure.database.models.tenant.meta_match_quality_snapshot import (
        MetaMatchQualitySnapshotModel,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    stats = {
        "event_log_deleted": 0,
        "match_quality_deleted": 0,
        "event_log_open_past_window": 0,
    }
    batch = 5_000

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        # SQLAlchemy Core rather than an f-string DELETE. Every value here is
        # a module constant, so string interpolation would have been safe in
        # fact — but it reads as dynamic SQL to a scanner and to the next
        # person, and expressing it through the ORM costs nothing and makes
        # the bound parameters real.
        # `extra` is the terminal-state guard. `meta_event_log` is an OUTBOX:
        # a row in `pending` or `retrying` is a delivery the platform still
        # owes, and age alone does not make it ours to throw away. Deleting one
        # mid-flight would drop a conversion silently and leave nothing behind
        # to explain the gap. The snapshot table has no lifecycle, so it prunes
        # on age alone.
        for model, days, key, extra in (
            (
                MetaEventLogModel,
                _event_log_retention_days(),
                "event_log_deleted",
                MetaEventLogModel.status.in_(TERMINAL_STATUSES),
            ),
            (
                MetaMatchQualitySnapshotModel,
                _MATCH_QUALITY_RETENTION_DAYS,
                "match_quality_deleted",
                None,
            ),
        ):
            cutoff = datetime.now(UTC) - timedelta(days=days)
            # Bounded loop, not `while True` — a runaway here would hold the
            # worker forever. 200 batches x 5k = 1M rows per run, and the
            # next scheduled run picks up any remainder.
            for _ in range(200):
                try:
                    # Delete by PK from a LIMITed subquery: one bounded lock
                    # per batch instead of one long lock over the whole scan.
                    doomed = select(model.id).where(model.created_at < cutoff)
                    if extra is not None:
                        doomed = doomed.where(extra)
                    doomed = doomed.limit(batch)
                    result = await session.execute(
                        delete(model).where(model.id.in_(doomed))
                    )
                    await session.commit()
                except Exception:  # noqa: BLE001 — housekeeping must not page
                    await session.rollback()
                    logger.exception(
                        "meta_tracking_prune_batch_failed",
                        table=model.__tablename__,
                    )
                    break
                deleted = int(result.rowcount or 0)
                stats[key] += deleted
                if deleted < batch:
                    break

        # Rows the guard above refused to touch. A non-zero count here is not
        # an error, it is a signal: the outbox is holding deliveries older than
        # the whole retention window, which means something stopped draining
        # it. Silently leaving them out of the delete would have made that
        # invisible AND let the table grow without bound.
        open_cutoff = datetime.now(UTC) - timedelta(days=_event_log_retention_days())
        try:
            stats["event_log_open_past_window"] = int(
                (
                    await session.execute(
                        select(func.count(MetaEventLogModel.id)).where(
                            MetaEventLogModel.created_at < open_cutoff,
                            MetaEventLogModel.status.notin_(TERMINAL_STATUSES),
                        )
                    )
                ).scalar()
                or 0
            )
        except Exception:  # noqa: BLE001 — a counter must not fail housekeeping
            logger.exception("meta_tracking_prune_open_count_failed")

    if stats["event_log_open_past_window"]:
        logger.warning("meta_tracking_prune_open_rows_past_window", **stats)
    else:
        logger.info("meta_tracking_prune_done", **stats)
    return stats
