"""TikTok Events API (server-side) Celery tasks — fan-out + cron sweep.

Sibling of ``meta_capi.py``. Two tasks live here:

  * ``tiktok_capi_send_event`` — the per-event fan-out worker. Called from
    ``/track`` (browse + funnel events) and from payment webhooks
    (Purchase). Re-checks ``api_enabled`` at execution time so a
    merchant toggling the flag off mid-flight doesn't trigger a stale
    fan-out from a queued job.

  * ``tiktok_capi_sweep_orphaned_purchases`` — hourly Celery Beat task that
    finds paid orders missing a Purchase row in ``tiktok_event_log``
    and re-enqueues them. Catches webhook failures.

Dedup contract: insert a ``tiktok_event_log`` row first; ``IntegrityError``
on the ``UNIQUE (store_id, pixel_id, event_id)`` constraint is the **silent
skip** signal — not an error. ``pixel_id`` is in the key because TikTok's
own dedup window is per Pixel Code, so one event_id fanned out to a store's
several pixels is correct, not a repeat.

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
#
# TikTok renamed two of these effective 2025-05-01 (Web/Offline/CRM):
# ``CompletePayment`` → ``Purchase`` and ``SubmitForm`` → ``Lead``. The old
# names still work — TikTok auto-converts them on its backend and reports the
# new ones — but new integrations are expected to send the current codes, so
# these are the current codes. ``ClickButton`` and ``PlaceAnOrder`` are
# soft-deprecated until 2027 and NUMU never sent either.
#
# The storefront's ``FUNNEL_STEP_TO_TIKTOK`` map MUST stay identical:
# deduplication keys on the event NAME, so renaming one leg and not the other
# turns every conversion into two events.
#
# "page_view" is absent on purpose — bare browser page views ride
# ``ttq.page()`` and are not worth a server round-trip.
FUNNEL_STEP_TO_TIKTOK_EVENT: dict[str, str] = {
    "product_view": "ViewContent",
    "add_to_cart": "AddToCart",
    "checkout_started": "InitiateCheckout",
    "add_payment_info": "AddPaymentInfo",
    "order_completed": "Purchase",
    "search": "Search",
    "complete_registration": "CompleteRegistration",
    "add_to_wishlist": "AddToWishlist",
    # TikTok's closest lead/subscribe/contact equivalents.
    "lead": "Lead",
    "subscribe": "Subscribe",
    "contact": "Contact",
}

# Every name a purchase row may carry in `tiktok_event_log`. Rows written
# before the 2026-09-08 rename say "CompletePayment"; anything reading the log
# back must accept BOTH, or it will decide those historical orders were never
# sent and re-send every one of them.
PURCHASE_EVENT_NAMES: tuple[str, ...] = ("Purchase", "CompletePayment")


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
        # `value` is a unit price only on browse events. A purchase-shaped
        # payload (it carries `order_id`) totals shipping, tax and fees too,
        # so passing it off as the product's price would inflate every
        # single-item order's price in TikTok's catalog reporting.
        if (
            single
            and line["quantity"] == 1
            and "order_id" not in custom_data
            and custom_data.get("value") is not None
        ):
            line["price"] = custom_data["value"]
        contents = [{"content_id": cid, **line} for cid in content_ids]
    if contents:
        props["contents"] = contents

    if custom_data.get("num_items") is not None:
        props["quantity"] = custom_data["num_items"]
    if custom_data.get("order_id"):
        props["order_id"] = custom_data["order_id"]
    # Search events carry the query term, under BOTH keys on purpose.
    # TikTok's own docs disagree with themselves: the Events API `properties`
    # reference names `query`, while the Pixel standard-events table lists
    # `search_string` for Search. The storefront funnel builds Meta-shaped
    # custom_data, so the term arrives as `search_string` and TikTok was
    # getting a Search event with no term at all on either leg. Emitting both
    # costs one key and removes the bet — TikTok ignores properties it does
    # not recognize.
    search_term = custom_data.get("query") or custom_data.get("search_string")
    if search_term:
        props["query"] = search_term
        props["search_string"] = search_term

    return props


def build_capi_payload(
    *,
    pixel_id: str,
    event_name: str,
    event_time: int,
    event_id: str,
    hashed_user: dict[str, Any],
    properties: dict[str, Any],
    event_source_url: str | None = None,
    test_event_code: str | None = None,
    opt_out: bool = False,
) -> dict[str, Any]:
    """Build the exact body POSTed to ``v1.3/event/track/``.

    Pure and public so a contract test can pin the wire shape without a DB,
    an HTTP client or a Celery worker. ``hashed_user`` is already hashed by
    ``hash_tiktok_user_data`` and ``properties`` already mapped by
    ``_to_tiktok_properties`` — this function only assembles the envelope.

    Note ``test_event_code`` sits at the ENVELOPE root, not inside the event.
    """
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

    payload: dict[str, Any] = {
        "event_source": "web",
        "event_source_id": pixel_id,
        "data": [event_obj],
    }
    if test_event_code:
        payload["test_event_code"] = test_event_code
    return payload


def should_adopt_existing_row(existing: Any, retries: int) -> bool:
    """Is a UNIQUE-violating row ours to finish, or another producer's event?

    The log row is written BEFORE the POST, so a collision is ambiguous, and
    guessing wrong loses events in one direction and double-sends in the
    other. Two cases are ours:

      * ``retries > 0`` — this task inserted the row, POSTed, got a retryable
        answer and raised. Unambiguous.
      * the row records no answer at all — no ``sent_at``, no
        ``response_status``. Something inserted it and died before TikTok
        replied. Under ``acks_late`` that redelivery arrives with
        ``retries == 0``, so keying only on ``retries`` stranded the event
        forever: written, never sent, and indistinguishable in the log from
        one that was.

    Anything else is a genuine second producer for an event TikTok has
    already answered — the real dedup case.

    Deliberately NOT a delivery-status column like ``meta_event_log`` carries.
    "No answer recorded" is already exactly the pending state, so the column
    would be a second source of truth for something two existing columns
    already say, plus a migration on a live table.
    """
    if existing is None:
        return False
    if retries:
        return True
    return existing.sent_at is None and existing.response_status is None


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
            # A UNIQUE violation means SOMEONE already logged this
            # (store, pixel, event). On the first attempt that someone is
            # another producer and the skip is correct. On a RETRY it is this
            # task's own earlier attempt — and returning "duplicate" there
            # made every retry a no-op: the row this task wrote before its
            # failed POST blocked the re-send, so `max_retries`, the backoff
            # and the whole retryable/permanent split in
            # `tiktok_delivery_policy` never actually re-sent anything, and
            # `attempt_count` could not exceed 1. A TikTok 5xx therefore lost
            # the event outright — the exact failure that policy exists to
            # prevent. `retries > 0` implies we own the row, because a retry
            # only happens after this task inserted it and then raised.
            #
            # `set_config(..., true)` is TRANSACTION-scoped, so the rollback
            # above discarded both the RLS bypass and the tenant GUC. Without
            # re-applying it the SELECT below is filtered to nothing by RLS,
            # `adopted` is always None, and this whole branch silently
            # degrades back to "every retry is a no-op".
            await narrow_to_tenant(session, store.tenant_id)
            adopted = await log_repo.get_for_event(store_uuid, pixel_id, event_id)
            if not should_adopt_existing_row(
                adopted, getattr(task.request, "retries", 0)
            ):
                adopted = None
            if adopted is None:
                logger.info(
                    "tiktok_capi_dedup_skip",
                    store_id=store_id,
                    event_id=event_id,
                    event_name=event_name,
                )
                return {"status": "duplicate", "request_id": None}
            logger.info(
                "tiktok_capi_adopted_unfinished_row",
                store_id=store_id,
                event_id=event_id,
                event_name=event_name,
                attempt=getattr(task.request, "retries", 0) + 1,
                reason=("retry" if getattr(task.request, "retries", 0) else "crashed"),
            )
            log_entity = adopted

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
            cred_id = cred.id
            cred_validated_at = cred.last_validated_at
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
    capi_payload = build_capi_payload(
        pixel_id=pixel_id,
        event_name=event_name,
        event_time=event_time,
        event_id=event_id,
        hashed_user=hashed_user,
        properties=properties,
        event_source_url=event_source_url,
        test_event_code=test_event_code,
        opt_out=opt_out,
    )

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

    # ── 5. Classify, then persist ─────────────────────────────────────
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

    # A delivered event is the only proof the token still works, so stamp the
    # credential with it. Without this, `last_validated_at` only ever moved
    # when a merchant re-saved the panel, so the hub's "last validated" line
    # said nothing about whether the token is alive TODAY — a token revoked
    # in Events Manager looked identical to a healthy one until five
    # consecutive sends had failed. Throttled to once an hour so a busy store
    # does not pay a credential write per event.
    stamp_validated = kind is None and (
        cred_validated_at is None
        or (datetime.now(UTC) - cred_validated_at) > timedelta(hours=1)
    )

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
            attempt_count=getattr(task.request, "retries", 0) + 1,
        )
        if stamp_validated:
            from sqlalchemy import update

            await session.execute(
                update(ServiceCredential)
                .where(ServiceCredential.id == cred_id)
                .values(last_validated_at=datetime.now(UTC))
            )
        await session.commit()

    # ── 6. Decide next move ──────────────────────────────────────────
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
# Cron sweep — recover orphaned Purchase events
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
    """Find paid orders without a Purchase ``tiktok_event_log`` row.

    Catches payment webhooks that silently failed. Runs hourly via Beat.
    Re-sends through ``enqueue_tiktok_capi_purchase`` so the recovered event
    carries the same order-based payload every other Purchase path sends.
    """
    try:
        result: dict[str, int] = _run_async(_sweep_orphans(lookback_hours))
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("tiktok_capi_sweep_failed")
        raise self.retry(exc=exc) from exc


async def _sweep_orphans(lookback_hours: int) -> dict[str, int]:
    from sqlalchemy import select

    from src.application.services.tiktok_capi_purchase_dispatcher import (
        enqueue_tiktok_capi_purchase,
    )
    from src.application.services.tiktok_pixel_resolver import resolve_tiktok_pixels
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

        # No fallback path. The previous `except Exception` swallowed any
        # failure here and re-ran the scan in Python with no store filter — a
        # worse query, guarding a condition (no JSONB path operator) that
        # Postgres has supported since 9.3. A real DB error should retry
        # visibly, not silently degrade into a full scan.
        orders = (await session.execute(order_query)).all()

        if not orders:
            return stats

        order_ids = [str(o.id) for o in orders]
        # (event_id, pixel_id), not event_id alone: an order whose FIRST pixel
        # logged a Purchase is not covered for its second and third.
        existing_query = select(
            TikTokEventLogModel.event_id, TikTokEventLogModel.pixel_id
        ).where(
            TikTokEventLogModel.event_name.in_(PURCHASE_EVENT_NAMES),
            TikTokEventLogModel.event_id.in_(order_ids),
        )
        existing = {
            (row[0], row[1]) for row in (await session.execute(existing_query)).all()
        }

        # One store query for the whole batch — orders cluster on few stores,
        # and the settings are what decide which pixels an order still owes.
        # Resolving here also keeps the "nothing missing" orders from loading
        # their order row at all, which is the common case for a recovery net.
        stores = (
            (
                await session.execute(
                    select(StoreModel).where(
                        StoreModel.id.in_({o.store_id for o in orders})
                    )
                )
            )
            .scalars()
            .all()
        )
        # Same resolver the two live fan-out paths use, so the sweep heals every
        # api-enabled pixel instead of only the first. The old code took
        # `pixels[0]` unconditionally, which also ignored that entry's own
        # `api_enabled` flag.
        pixels_by_store = {
            s.id: resolve_tiktok_pixels(
                ((s.settings or {}).get("tracking") or {}).get("tiktok") or {},
                mode="api",
            )
            for s in stores
        }

        for o in orders:
            stats["scanned"] += 1
            missing = [
                p
                for p in pixels_by_store.get(o.store_id) or []
                if (str(o.id), p.pixel_id) not in existing
            ]
            if not missing:
                continue

            order_full = (
                await session.execute(select(OrderModel).where(OrderModel.id == o.id))
            ).scalar_one_or_none()
            if order_full is None:
                continue

            # Hand off to the dispatcher instead of hand-building a payload.
            # This path used to send `user_data={}` and a bare value/currency —
            # no contents[], no phone, no email, no ttclid — which is the
            # weakest event the platform produces, fired in exactly the case
            # where the good one already failed. The dispatcher builds the
            # order-based payload every other Purchase path uses, and it
            # re-resolves the pixels itself; a pixel that already has a row
            # short-circuits on the UNIQUE constraint, so `missing` above only
            # decides *whether* to bother.
            await enqueue_tiktok_capi_purchase(session, order_full)
            stats["enqueued"] += 1

    if stats["enqueued"]:
        logger.info("tiktok_capi_sweep_enqueued", **stats)
    return stats


# ──────────────────────────────────────────────────────────────────────
# Replay — re-send a row TikTok never accepted
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.tiktok_capi_replay_event",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
    autoretry_for=(httpx.NetworkError, httpx.TimeoutException),
    retry_backoff=True,
    acks_late=True,
)
def tiktok_capi_replay_event(self: Any, *, log_id: str) -> dict[str, Any]:
    """Re-POST one ``tiktok_event_log`` row that TikTok never accepted.

    Replays the **stored** payload rather than rebuilding it: the row already
    holds the hashed ``user`` object and the mapped ``properties``, so
    re-deriving them would re-hash already-hashed values and could drift from
    what the browser leg sent, breaking deduplication.

    The original ``event_time`` is preserved. Replaying under a falsified
    "now" would misstate when the conversion happened; if TikTok rejects it as
    stale that is the correct outcome, and the answer lands on the row like any
    other. (TikTok publishes no ``event_time`` acceptance window — open
    question Q7 in the design doc.)

    Refuses a row that already succeeded, so calling this twice is safe.
    """
    try:
        return _run_async(_replay_event(UUID(log_id)))
    except (httpx.NetworkError, httpx.TimeoutException):
        raise
    except Exception:  # noqa: BLE001
        logger.exception("tiktok_capi_replay_error", log_id=log_id)
        return {"status": "failed", "log_id": log_id}


async def _replay_event(log_id: UUID) -> dict[str, Any]:
    from sqlalchemy import select

    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.configuration import (
        ServiceCredential,
        ServiceName,
        ServiceType,
    )
    from src.infrastructure.database.models.tenant.tiktok_event_log import (
        TikTokEventLogModel,
    )
    from src.infrastructure.external_services.secrets import get_secrets_manager
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.repositories.tiktok_event_log_repository import (
        TikTokEventLogRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        row = (
            await session.execute(
                select(TikTokEventLogModel).where(TikTokEventLogModel.id == log_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return {"status": "skipped", "reason": "row_missing"}
        delivered = (
            row.response_status is not None
            and 200 <= row.response_status < 300
            and row.response_code == 0
        )
        if delivered:
            return {"status": "skipped", "reason": "already_delivered"}

        store = await StoreRepository(session).get_by_id(row.store_id)
        if store is None:
            return {"status": "skipped", "reason": "store_missing"}
        tiktok_cfg = ((store.settings or {}).get("tracking") or {}).get("tiktok") or {}
        if not tiktok_cfg.get("api_enabled"):
            # A merchant who switched the Events API off does not want a
            # backlog of replays landing the moment someone clicks Replay.
            return {"status": "skipped", "reason": "api_disabled"}

        cred = (
            await session.execute(
                select(ServiceCredential)
                .where(ServiceCredential.tenant_id == store.tenant_id)
                .where(ServiceCredential.service_type == ServiceType.TRACKING)
                .where(ServiceCredential.service_name == ServiceName.TIKTOK_CAPI)
                .where(ServiceCredential.is_active.is_(True))
            )
        ).scalar_one_or_none()
        if cred is None:
            return {"status": "skipped", "reason": "credential_missing"}
        decrypted = await get_secrets_manager().decrypt(
            cred.credentials_encrypted, cred.encryption_key_id
        )
        access_token = decrypted["access_token"]

        stored = row.request_payload or {}
        tenant_id = store.tenant_id
        attempt = (row.attempt_count or 1) + 1
        payload = build_capi_payload(
            pixel_id=row.pixel_id,
            event_name=stored.get("event") or row.event_name,
            event_time=stored.get("event_time") or int(row.event_time.timestamp()),
            event_id=row.event_id,
            hashed_user=stored.get("user") or {},
            properties=stored.get("properties") or {},
            event_source_url=stored.get("event_source_url"),
            test_event_code=stored.get("test_event_code"),
            opt_out=bool(stored.get("limited_data_use")),
        )

    with httpx.Client(timeout=httpx.Timeout(15.0, connect=5.0)) as client:
        resp = client.post(
            TIKTOK_EVENTS_API_URL,
            headers={
                "Access-Token": access_token,
                "Content-Type": "application/json",
            },
            json=payload,
        )
    try:
        body = resp.json()
    except Exception:  # noqa: BLE001
        body = {"raw": resp.text[:500]}
    code = (body or {}).get("code")
    request_id = (body or {}).get("request_id")

    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        await narrow_to_tenant(session, tenant_id)
        await TikTokEventLogRepository(session).update_response(
            log_id,
            status=resp.status_code,
            code=code,
            body=_redact_response(body),
            request_id=request_id,
            sent_at=datetime.now(UTC),
            attempt_count=attempt,
        )
        await session.commit()

    kind = classify_tiktok_response(resp.status_code, code, body)
    logger.info(
        "tiktok_capi_replayed",
        log_id=str(log_id),
        outcome="sent" if kind is None else kind.value,
        attempt=attempt,
    )
    return {
        "status": "sent" if kind is None else "failed",
        "request_id": request_id,
        "code": code,
    }


@celery_app.task(
    name="tasks.tiktok_capi_replay_failed",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    acks_late=True,
)
def tiktok_capi_replay_failed(
    self: Any, *, store_id: str, hours: int = 24, limit: int = 100
) -> dict[str, int]:
    """Fan ``tiktok_capi_replay_event`` out over one store's failed rows.

    Bounded by ``limit`` on purpose: replay is a human-triggered action, and
    an unbounded one on a store that has been failing for a week would
    stampede the Events API the moment somebody clicks it.
    """
    try:
        return _run_async(_replay_failed(UUID(store_id), hours, limit))
    except Exception as exc:  # noqa: BLE001
        logger.exception("tiktok_capi_replay_sweep_error")
        raise self.retry(exc=exc) from exc


async def _replay_failed(store_id: UUID, hours: int, limit: int) -> dict[str, int]:
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.store_repository import StoreRepository
    from src.infrastructure.repositories.tiktok_event_log_repository import (
        TikTokEventLogRepository,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass, narrow_to_tenant

    since = datetime.now(UTC) - timedelta(hours=hours)
    async with AsyncSessionLocal() as session:
        await enable_rls_bypass(session)
        store = await StoreRepository(session).get_by_id(store_id)
        if store is None:
            return {"queued": 0}
        await narrow_to_tenant(session, store.tenant_id)
        rows = await TikTokEventLogRepository(session).failed_for_store(
            store_id, since, limit
        )

    for row in rows:
        tiktok_capi_replay_event.delay(log_id=str(row.id))
    if rows:
        logger.info(
            "tiktok_capi_replay_queued", store_id=str(store_id), count=len(rows)
        )
    return {"queued": len(rows)}


# ──────────────────────────────────────────────────────────────────────
# Retention — the log is append-mostly and nothing ever removed a row
# ──────────────────────────────────────────────────────────────────────


@celery_app.task(
    name="tasks.tiktok_capi_purge_event_log",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    acks_late=True,
)
def tiktok_capi_purge_event_log(
    self: Any, retention_days: int | None = None, limit: int = 50_000
) -> dict[str, int]:
    """Delete `tiktok_event_log` rows older than the retention period.

    The rows hold hashed identifiers, never raw PII, but hashed is not
    anonymous and the table had no expiry at all. Period comes from
    ``settings.ad_event_log_retention_days`` — shared with the Meta rail,
    because both logs hold the same class of data.

    Bounded by ``limit`` so one run can never take a long lock on the table;
    whatever is left is picked up by the next daily run.
    """
    try:
        return _run_async(_purge_event_log(retention_days, limit))
    except Exception as exc:  # noqa: BLE001
        logger.exception("tiktok_capi_purge_failed")
        raise self.retry(exc=exc) from exc


async def _purge_event_log(retention_days: int | None, limit: int) -> dict[str, int]:
    from sqlalchemy import delete, select

    from src.config.settings import get_settings
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.database.models.tenant.tiktok_event_log import (
        TikTokEventLogModel,
    )
    from src.infrastructure.tenancy.rls import enable_rls_bypass

    days = retention_days or get_settings().ad_event_log_retention_days
    cutoff = datetime.now(UTC) - timedelta(days=days)

    async with AsyncSessionLocal() as session:
        # Cross-tenant by design: retention is a platform obligation, not a
        # per-store one, so this runs with RLS bypassed like the sweep.
        await enable_rls_bypass(session)
        # ponytail: plain `created_at` scan, no index and no partitioning. The
        # table takes ~100 rows/day/store; at two live stores a daily seq scan
        # is far cheaper than the write cost of another index. Add one (or
        # partition by month) if this ever exceeds a few million rows.
        doomed = (
            select(TikTokEventLogModel.id)
            .where(TikTokEventLogModel.created_at < cutoff)
            .limit(limit)
            .scalar_subquery()
        )
        result = await session.execute(
            delete(TikTokEventLogModel).where(TikTokEventLogModel.id.in_(doomed))
        )
        await session.commit()

    deleted = int(result.rowcount or 0)
    if deleted:
        logger.info(
            "tiktok_capi_purged_event_log", deleted=deleted, retention_days=days
        )
    return {"deleted": deleted, "retention_days": days}
