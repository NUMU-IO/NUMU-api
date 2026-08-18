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
from typing import Any
from uuid import UUID

import httpx
import sentry_sdk

from src.config import settings
from src.core.logging import get_logger
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


def _funnel_step_to_meta_event(step: str) -> str | None:
    """Return the Meta event name for a NUMU funnel step, or None."""
    return FUNNEL_STEP_TO_META_EVENT.get(step)


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
    max_retries=6,
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
            fingerprints=[
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

        # ── 2. Insert the log row first — UNIQUE catches dupes ────────
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = MetaEventLogRepository(session)

        request_payload = {
            "event_name": event_name,
            "event_time": event_time,
            "event_source_url": event_source_url,
            "action_source": action_source,
            "custom_data": custom_data,
            "user_data": hash_user_data(user_data),
            "test_event_code": test_event_code,
        }
        # Wave 3 Phase 18 — opt_out at the event level (Meta's spec).
        # Only attach when true to keep payloads minimal for the
        # majority of events (most users haven't denied marketing).
        if opt_out:
            request_payload["opt_out"] = True

        try:
            log_entity = await log_repo.create(
                MetaEventLog(
                    tenant_id=store.tenant_id,
                    store_id=store_uuid,
                    event_id=event_id,
                    event_name=event_name,
                    event_time=datetime.fromtimestamp(event_time, tz=UTC),
                    pixel_id=pixel_id,
                    request_payload=request_payload,
                )
            )
            await session.commit()
            log_id = log_entity.id
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

            completed = existing is not None and existing.response_status is not None
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
                    under_cap = (existing.attempt_count or 0) < _MAX_ENRICHMENT_RESENDS
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
            log_id = existing.id
            previous_status = existing.response_status
            existing.request_payload = request_payload
            existing.attempt_count = (existing.attempt_count or 0) + 1
            existing.last_error = None
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
                fingerprints=["meta_capi", "decrypt", store_id],
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
                "user_data": hash_user_data(user_data),
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
        # Bubble up so Celery autoretry catches it — but record the
        # attempt first so the dashboard reflects it.
        last_error = f"{type(exc).__name__}: {exc}"
        async with AsyncSessionLocal() as session:
            await enable_rls_bypass(session)
            await narrow_to_tenant(session, store.tenant_id)
            log_repo = MetaEventLogRepository(session)
            await log_repo.update_error(
                log_id,
                error=last_error,
                attempt_count=task.request.retries + 1,
            )
            await session.commit()
        raise

    # ── 5. Persist the response on the log row ───────────────────────
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = MetaEventLogRepository(session)
        await log_repo.update_response(
            log_id,
            status=response_status,
            body=_redact_response(response_body),
            fbtrace_id=fbtrace_id,
            sent_at=datetime.now(UTC),
        )
        await session.commit()

    # ── 6. Decide next move based on HTTP status ─────────────────────
    if 200 <= response_status < 300:
        return {"status": "sent", "fbtrace_id": fbtrace_id}

    if response_status == 429 or response_status >= 500:
        # Retry transient — surface so Celery retry policy kicks in.
        # We've already updated the row; bump attempt_count next pass.
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

    # 4xx → permanent failure. Sentry breadcrumb + capture so the merchant /
    # support team can see it without having to dig through Celery logs.
    # status_class breaks 4xx out from network/decrypt for finer alert rules.
    status_class = "4xx" if response_status < 500 else "5xx_giveup"
    sentry_sdk.set_tag("meta_capi.status_class", status_class)
    sentry_sdk.set_tag("meta_capi.http_status", response_status)
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
        fingerprints=["meta_capi", status_class, store_id, str(response_status)],
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
                meta_capi_send_event.delay(
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
