"""TikTok Events API (server-side) Celery tasks — fan-out + cron sweep.

Sibling of ``meta_capi.py``. Two tasks live here:

  * ``tiktok_capi_send_event`` — the per-event fan-out worker. Called from
    ``/track`` (browse + funnel events) and from payment webhooks
    (CompletePayment). Re-checks ``api_enabled`` at execution time so a
    merchant toggling the flag off mid-flight doesn't trigger a stale
    fan-out from a queued job.

  * ``tiktok_capi_sweep_orphaned_purchases`` — hourly Celery Beat task that
    finds paid orders missing a CompletePayment row in ``tiktok_event_log``
    and re-enqueues them. Catches webhook failures.

Dedup contract: insert a ``tiktok_event_log`` row first; ``IntegrityError``
on the ``UNIQUE (store_id, event_id)`` constraint is the **silent skip**
signal — not an error.

TikTok delta vs Meta:
  * Endpoint ``…/open_api/v1.3/event/track/`` with the token in an
    ``Access-Token`` HEADER (not a query param).
  * Body root carries ``event_source``/``event_source_id``.
  * The API answers HTTP 200 even on logical errors — success is
    ``HTTP 2xx AND body.code == 0``. ``request_id`` is TikTok's
    ``fbtrace_id`` analogue.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
import sentry_sdk

from src.core.logging import get_logger
from src.core.services.tiktok_delivery_policy import (
    classify_response as classify_tiktok_response,
)
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

# TikTok Events API endpoint (v1.3). The version lives in the path, so we
# pin it here rather than in settings.
TIKTOK_EVENTS_API_URL = "https://business-api.tiktok.com/open_api/v1.3/event/track/"

# ──────────────────────────────────────────────────────────────────────
# Funnel-step → TikTok-event mapping
# ──────────────────────────────────────────────────────────────────────
# Public so the settings test-event endpoint and tests can import it.
# NB: TikTok's purchase event is **CompletePayment** (NOT "Purchase" —
# that's Meta). "page_view" maps to ViewContent for the server rail; bare
# browser page views ("Pageview") are not worth a server round-trip.
FUNNEL_STEP_TO_TIKTOK_EVENT: dict[str, str] = {
    "product_view": "ViewContent",
    "add_to_cart": "AddToCart",
    "checkout_started": "InitiateCheckout",
    "add_payment_info": "AddPaymentInfo",
    "order_completed": "CompletePayment",
    "search": "Search",
    "complete_registration": "CompleteRegistration",
    "add_to_wishlist": "AddToWishlist",
    # TikTok's closest lead/subscribe/contact equivalents.
    "lead": "SubmitForm",
    "subscribe": "Subscribe",
    "contact": "Contact",
}


def _funnel_step_to_tiktok_event(step: str) -> str | None:
    """Return the TikTok event name for a NUMU funnel step, or None."""
    return FUNNEL_STEP_TO_TIKTOK_EVENT.get(step)


# ──────────────────────────────────────────────────────────────────────
# custom_data → TikTok properties transform
# ──────────────────────────────────────────────────────────────────────


def _to_tiktok_properties(custom_data: dict[str, Any]) -> dict[str, Any]:
    """Map NUMU's Meta-shaped ``custom_data`` to TikTok's ``properties``.

    Callers (dispatcher + /track) build a Meta-style ``custom_data`` dict.
    TikTok's ``properties`` object uses ``content_id`` (comma-joined
    string), ``contents[]`` of ``{content_id, quantity, price}``, plus
    ``value`` / ``currency`` / ``order_id`` / ``query``. This keeps the
    call sites provider-agnostic — the shape swap happens here.
    """
    props: dict[str, Any] = {}

    if "value" in custom_data:
        props["value"] = custom_data["value"]
    props["currency"] = custom_data.get("currency") or "EGP"
    props["content_type"] = custom_data.get("content_type", "product")

    raw_ids = custom_data.get("content_ids")
    content_ids = (
        [str(c).strip() for c in raw_ids if isinstance(c, str | int) and str(c).strip()]
        if isinstance(raw_ids, list)
        else []
    )
    if content_ids:
        # `content_id` is the pixel-1.x key, kept for reporting continuity;
        # `content_ids` is the 2.0 key. Neither is what the catalog / Video
        # Shopping Ads pipeline reads — that is `contents[].content_id`, below.
        props["content_id"] = ",".join(content_ids)
        props["content_ids"] = content_ids

    contents: list[dict[str, Any]] = []
    raw_contents = custom_data.get("contents")
    if isinstance(raw_contents, list):
        for c in raw_contents:
            if not isinstance(c, dict):
                continue
            cid = str(c.get("id") or c.get("content_id") or "").strip()
            if not cid:
                # TikTok counts a blank content_id as missing — drop the line
                # rather than ship a claim it will flag.
                continue
            contents.append({
                "content_id": cid,
                "quantity": int(c.get("quantity", 1)),
                "price": c.get("item_price", c.get("price", 0)),
            })
    if not contents and content_ids:
        # ViewContent / AddToCart / the confirmation-page Purchase only carry
        # `content_ids`. TikTok's "Content ID is missing" diagnostic keys on
        # `contents[].content_id`, so synthesize one line per id from what
        # the event does carry. Price and name are only trustworthy for a
        # single-product event (value == that product's price).
        single = len(content_ids) == 1
        qty = custom_data.get("num_items") if single else None
        line: dict[str, Any] = {"quantity": int(qty) if qty is not None else 1}
        if single and custom_data.get("content_name"):
            line["content_name"] = custom_data["content_name"]
        if single and line["quantity"] == 1 and custom_data.get("value") is not None:
            line["price"] = custom_data["value"]
        contents = [{"content_id": cid, **line} for cid in content_ids]
    if contents:
        props["contents"] = contents

    if custom_data.get("num_items") is not None:
        props["quantity"] = custom_data["num_items"]
    if custom_data.get("order_id"):
        props["order_id"] = custom_data["order_id"]
    # Search events carry the query term.
    if custom_data.get("query"):
        props["query"] = custom_data["query"]

    return props


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
    name="tasks.tiktok_capi_send_event",
    bind=True,
    max_retries=6,
    default_retry_delay=15,
    autoretry_for=(httpx.NetworkError, httpx.TimeoutException),
    retry_backoff=True,
    retry_backoff_max=300,
    retry_jitter=True,
    acks_late=True,
)
def tiktok_capi_send_event(
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
    action_source: str = "web",
    opt_out: bool = False,
) -> dict[str, Any]:
    """Send one Events API event with idempotency, retries and redaction.

    Returns a small status dict for observability:
        {"status": "sent" | "duplicate" | "skipped" | "failed",
         "request_id": str | None}

    Re-checks ``api_enabled`` at execution time: a queued job whose store
    flipped the flag off mid-flight returns ``{"status": "skipped"}``
    without contacting TikTok.
    """
    sentry_sdk.set_tag("tiktok_capi.event_name", event_name)
    sentry_sdk.set_tag("tiktok_capi.store_id", store_id)
    sentry_sdk.set_tag("tiktok_capi.pixel_id", pixel_id)
    sentry_sdk.set_tag("tiktok_capi.action_source", action_source)
    sentry_sdk.set_tag("tiktok_capi.test_mode", bool(test_event_code))
    sentry_sdk.set_context(
        "tiktok_capi",
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
        sentry_sdk.set_tag("tiktok_capi.status", result.get("status", "unknown"))
        return result
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        sentry_sdk.set_tag("tiktok_capi.status_class", "network")
        sentry_sdk.capture_message(
            f"tiktok_capi.network_error for store {store_id}: {type(exc).__name__}",
            level="warning",
            fingerprint=["tiktok_capi", "network", store_id, type(exc).__name__],
        )
        raise
    except Exception:  # noqa: BLE001 — last-ditch: log + bury
        logger.exception("tiktok_capi_send_event_unexpected_error")
        sentry_sdk.set_tag("tiktok_capi.status_class", "unexpected")
        sentry_sdk.capture_exception(
            fingerprint=["tiktok_capi", "unexpected", store_id, event_name],
        )
        return {"status": "failed", "request_id": None}


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

    from src.core.entities.tiktok_event_log import TikTokEventLog
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.external_services.secrets import get_secrets_manager
    from src.infrastructure.external_services.tiktok.hashing import (
        hash_tiktok_user_data,
    )
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.repositories.tiktok_event_log_repository import (
        TikTokEventLogRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    store_uuid = UUID(store_id)

    # ── 1. Look up store + tenant + freshness-check api_enabled ────────
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        store_repo = StoreRepository(session)
        store = await store_repo.get_by_id(store_uuid)
        if store is None:
            logger.warning("tiktok_capi_store_missing", store_id=store_id)
            return {"status": "skipped", "reason": "store_missing"}

        tiktok_cfg = ((store.settings or {}).get("tracking") or {}).get("tiktok") or {}
        if not tiktok_cfg.get("api_enabled"):
            return {"status": "skipped", "reason": "api_disabled"}

        # TikTok expects a page URL on web-sourced events (it lands as
        # ``page.url`` below) and, like Meta, degrades attribution without
        # it. Enqueue sites with no page context — the orphan sweep, the
        # test-event endpoint — pass None, so default to the store's public
        # origin here instead of at each call site. ``getattr`` because the
        # signature only promises "the store object", not the entity that
        # carries the ``store_url`` property.
        if not event_source_url and action_source.lower() == "web":
            event_source_url = getattr(store, "store_url", None)

        # Debug-mode auto-attaches the saved test_event_code until
        # debug_mode_expires_at passes. Caller's code (e.g. the test-event
        # endpoint) wins if set.
        if not test_event_code:
            expires_raw = tiktok_cfg.get("debug_mode_expires_at")
            if expires_raw:
                try:
                    expires_at = datetime.fromisoformat(
                        expires_raw.replace("Z", "+00:00")
                    )
                    if expires_at > datetime.now(UTC):
                        test_event_code = tiktok_cfg.get("test_event_code")
                except (ValueError, AttributeError):
                    pass

        # ── 2. Insert the log row first — UNIQUE catches dupes ────────
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = TikTokEventLogRepository(session)

        hashed_user = hash_tiktok_user_data(user_data)
        properties = _to_tiktok_properties(custom_data)

        request_payload = {
            "event": event_name,
            "event_time": event_time,
            "event_source_url": event_source_url,
            "action_source": action_source,
            "properties": properties,
            "user": hashed_user,
            "test_event_code": test_event_code,
        }
        if opt_out:
            request_payload["limited_data_use"] = True

        try:
            log_entity = await log_repo.create(
                TikTokEventLog(
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
        except IntegrityError:
            await session.rollback()
            logger.info(
                "tiktok_capi_dedup_skip",
                store_id=store_id,
                event_id=event_id,
                event_name=event_name,
            )
            return {"status": "duplicate", "request_id": None}

        # ── 3. Decrypt the access token ───────────────────────────────
        from sqlalchemy import select

        cred_query = (
            select(ServiceCredential)
            .where(ServiceCredential.tenant_id == store.tenant_id)
            .where(ServiceCredential.service_type == ServiceType.TRACKING)
            .where(ServiceCredential.service_name == ServiceName.TIKTOK_CAPI)
            .where(ServiceCredential.is_active.is_(True))
        )
        cred = (await session.execute(cred_query)).scalar_one_or_none()
        if cred is None:
            logger.warning(
                "tiktok_capi_credential_missing",
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
            logger.exception("tiktok_capi_decrypt_failed", store_id=store_id)
            sentry_sdk.set_tag("tiktok_capi.status_class", "decrypt")
            sentry_sdk.capture_message(
                f"tiktok_capi.decrypt_failed for store {store_id}",
                level="error",
                fingerprint=["tiktok_capi", "decrypt", store_id],
            )
            return {"status": "failed", "reason": "decrypt_error"}

    # ── 4. POST to TikTok — outside the DB session ───────────────────
    event_obj: dict[str, Any] = {
        "event": event_name,
        "event_time": event_time,
        "event_id": event_id,
        "user": hashed_user,
        "properties": properties,
    }
    if event_source_url:
        event_obj["page"] = {"url": event_source_url}
    # TikTok's Limited Data Use flag — attach when the visitor denied
    # marketing so the event lands as a privacy-limited signal.
    if opt_out:
        event_obj["limited_data_use"] = True

    capi_payload: dict[str, Any] = {
        "event_source": "web",
        "event_source_id": pixel_id,
        "data": [event_obj],
    }
    if test_event_code:
        capi_payload["test_event_code"] = test_event_code

    response_body: dict[str, Any] | None = None
    response_status: int | None = None
    response_code: int | None = None
    request_id: str | None = None
    last_error: str | None = None

    try:
        with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
            resp = client.post(
                TIKTOK_EVENTS_API_URL,
                headers={
                    "Access-Token": access_token,
                    "Content-Type": "application/json",
                },
                json=capi_payload,
            )
        response_status = resp.status_code
        try:
            response_body = resp.json()
            response_code = (response_body or {}).get("code")
            request_id = (response_body or {}).get("request_id")
        except Exception:  # noqa: BLE001
            response_body = {"raw": resp.text[:500]}
    except (httpx.NetworkError, httpx.TimeoutException) as exc:
        last_error = f"{type(exc).__name__}: {exc}"
        async with AsyncSessionLocal() as session:
            await enable_rls_bypass(session)
            await narrow_to_tenant(session, store.tenant_id)
            log_repo = TikTokEventLogRepository(session)
            await log_repo.update_error(
                log_entity.id,
                error=last_error,
                attempt_count=task.request.retries + 1,
            )
            await session.commit()
        raise

    # ── 5. Persist the response on the log row ───────────────────────
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, store.tenant_id)
        log_repo = TikTokEventLogRepository(session)
        await log_repo.update_response(
            log_entity.id,
            status=response_status,
            code=response_code,
            body=_redact_response(response_body),
            request_id=request_id,
            sent_at=datetime.now(UTC),
        )
        await session.commit()

    # ── 6. Decide next move ──────────────────────────────────────────
    # Success requires BOTH a 2xx HTTP status AND a zero business code —
    # TikTok answers 200 with a non-zero ``code`` on logical errors.
    #
    # Which is precisely why the retry decision cannot be made from the HTTP
    # status alone. This used to retry only on 429/5xx and call everything
    # else permanent, so a TikTok-side server error — delivered as HTTP 200
    # with a 5xxxx body code — was dropped without a single retry. See
    # `tiktok_delivery_policy` for the code table and for why 40100 defaults
    # to retryable.
    kind = classify_tiktok_response(response_status, response_code, response_body)

    if kind is None:
        return {"status": "sent", "request_id": request_id}

    if kind.retryable:
        try:
            raise task.retry(
                countdown=_backoff_from_response(resp.headers, task.request.retries),
                exc=httpx.HTTPStatusError(
                    f"Events API returned http={response_status} code={response_code}",
                    request=resp.request,
                    response=resp,
                ),
            )
        except Exception:
            raise

    # Permanent. Surface to Sentry so support can see it without digging
    # through Celery logs. `status_class` is now the failure KIND, so a dead
    # token and a malformed payload no longer share one bucket.
    status_class = kind.value
    sentry_sdk.set_tag("tiktok_capi.status_class", status_class)
    sentry_sdk.set_tag("tiktok_capi.http_status", response_status)
    sentry_sdk.set_tag("tiktok_capi.code", response_code)
    sentry_sdk.add_breadcrumb(
        category="tiktok_capi",
        level="warning",
        message=(
            f"tiktok_capi.{status_class} for store {store_id}: "
            f"http={response_status} code={response_code}"
        ),
        data={
            "store_id": store_id,
            "pixel_id": pixel_id,
            "event_name": event_name,
            "request_id": request_id,
        },
    )
    sentry_sdk.capture_message(
        f"tiktok_capi.{status_class} for store {store_id}: "
        f"http={response_status} code={response_code}",
        level="warning",
        fingerprint=["tiktok_capi", status_class, store_id, str(response_code)],
    )
    return {"status": "failed", "request_id": request_id}


def _redact_response(body: dict | None) -> dict | None:
    """Keep only the non-PII diagnostic keys from TikTok's response."""
    if body is None:
        return None
    keep = ("code", "message", "request_id")
    redacted = {k: body[k] for k in keep if k in body}
    # Preserve a bounded ``raw`` fallback captured on JSON parse failure.
    if "raw" in body:
        redacted["raw"] = body["raw"]
    return redacted or {"code": body.get("code")}


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
# Cron sweep — recover orphaned CompletePayment events
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.tiktok_capi_sweep_orphaned_purchases",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
)
def tiktok_capi_sweep_orphaned_purchases(
    self: Any, lookback_hours: int = 24
) -> dict[str, int]:
    """Find paid orders without a CompletePayment ``tiktok_event_log`` row.

    Catches payment webhooks that silently failed. Runs hourly via Beat.
    """
    try:
        result: dict[str, int] = _run_async(_sweep_orphans(lookback_hours))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("tiktok_capi_sweep_failed")
        raise self.retry(exc=exc) from exc


async def _sweep_orphans(lookback_hours: int) -> dict[str, int]:
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel
    from src.infrastructure.database.models.tenant.tiktok_event_log import (
        TikTokEventLogModel,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    cutoff = datetime.now(UTC) - timedelta(hours=lookback_hours)
    stats = {"scanned": 0, "enqueued": 0}

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)

        order_query = (
            select(OrderModel.id, OrderModel.store_id, OrderModel.tenant_id)
            .join(StoreModel, StoreModel.id == OrderModel.store_id)
            .where(OrderModel.paid_at.isnot(None))
            .where(OrderModel.paid_at >= cutoff)
            .where(
                StoreModel.settings["tracking"]["tiktok"]["api_enabled"].as_string()
                == "true"
            )
            .limit(500)
        )

        try:
            orders = (await session.execute(order_query)).all()
        except Exception:  # noqa: BLE001
            logger.exception("tiktok_capi_sweep_jsonb_fallback")
            orders = await _orders_paid_since_python_filter(session, cutoff)

        if not orders:
            return stats

        order_ids = [str(o.id) for o in orders]
        existing_query = select(TikTokEventLogModel.event_id).where(
            TikTokEventLogModel.event_name == "CompletePayment",
            TikTokEventLogModel.event_id.in_(order_ids),
        )
        existing = {row[0] for row in (await session.execute(existing_query)).all()}

        for o in orders:
            stats["scanned"] += 1
            if str(o.id) in existing:
                continue

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
            tiktok_cfg = ((store_full.settings or {}).get("tracking") or {}).get(
                "tiktok"
            ) or {}
            pixel_id = tiktok_cfg.get("pixel_id")
            if not pixel_id:
                # Legacy single pixel absent — try the multi-pixel array.
                pixels = tiktok_cfg.get("pixels") or []
                pixel_id = pixels[0].get("pixel_id") if pixels else None
            if not pixel_id:
                continue

            tiktok_capi_send_event.delay(
                store_id=str(order_full.store_id),
                pixel_id=pixel_id,
                event_name="CompletePayment",
                event_id=str(order_full.id),
                event_time=int((order_full.paid_at or datetime.now(UTC)).timestamp()),
                event_source_url=None,
                user_data={},
                custom_data={
                    "value": (order_full.total or 0) / 100,
                    "currency": order_full.currency or "EGP",
                    "order_id": str(order_full.id),
                },
                action_source="web",
            )
            stats["enqueued"] += 1

    if stats["enqueued"]:
        logger.info("tiktok_capi_sweep_enqueued", **stats)
    return stats


async def _orders_paid_since_python_filter(session: Any, cutoff: datetime) -> list[Any]:
    """Fallback when the JSONB path operator isn't available."""
    from sqlalchemy import select

    from src.infrastructure.database.models.tenant.order import OrderModel
    from src.infrastructure.database.models.tenant.store import StoreModel

    rows = (
        await session.execute(
            select(OrderModel.id, OrderModel.store_id, OrderModel.tenant_id)
            .where(OrderModel.paid_at.isnot(None))
            .where(OrderModel.paid_at >= cutoff)
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
        tiktok_cfg = ((s.settings or {}).get("tracking") or {}).get("tiktok") or {}
        if tiktok_cfg.get("api_enabled"):
            out.append(r)
    return out
