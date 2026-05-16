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
from src.config.logging_config import get_logger
from src.infrastructure.messaging.celery_app import celery_app

logger = get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────
# Funnel-step → Meta-event mapping (plan §5.3)
# ──────────────────────────────────────────────────────────────────────
# Public so settings.test-event endpoint and tests can import it.
FUNNEL_STEP_TO_META_EVENT: dict[str, str] = {
    "page_view": "PageView",
    "product_view": "ViewContent",
    "add_to_cart": "AddToCart",
    "checkout_started": "InitiateCheckout",
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
        except IntegrityError:
            # Dedup primitive — someone else already sent this event_id.
            await session.rollback()
            logger.info(
                "meta_capi_dedup_skip",
                store_id=store_id,
                event_id=event_id,
                event_name=event_name,
            )
            return {"status": "duplicate", "fbtrace_id": None}

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
    api_version = settings.meta_graph_api_version or "v21.0"
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
        log_repo = MetaEventLogRepository(session)
        await log_repo.update_response(
            log_entity.id,
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
    from sqlalchemy import select

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
            .where(OrderModel.paid_at.isnot(None))
            .where(OrderModel.paid_at >= cutoff)
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
        existing_query = select(MetaEventLogModel.event_id).where(
            MetaEventLogModel.event_name == "Purchase",
            MetaEventLogModel.event_id.in_(order_ids),
        )
        existing = {row[0] for row in (await session.execute(existing_query)).all()}

        for o in orders:
            stats["scanned"] += 1
            if str(o.id) in existing:
                continue
            # Re-enqueue. We don't have full order context here — pull
            # custom_data from the order at enqueue time would mean
            # another query per row. The webhook path constructs the
            # rich payload; the sweep just needs the event to land. Use
            # a minimal payload — Meta will accept it; match quality is
            # best-effort by definition for a recovery sweep.
            from src.infrastructure.database.models.tenant.order import (
                OrderModel as OM,
            )

            order_full = (
                await session.execute(select(OM).where(OM.id == o.id))
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
            pixel_id = meta_cfg.get("pixel_id")
            if not pixel_id:
                continue

            meta_capi_send_event.delay(
                store_id=str(order_full.store_id),
                pixel_id=pixel_id,
                event_name="Purchase",
                event_id=str(order_full.id),
                event_time=int((order_full.paid_at or datetime.now(UTC)).timestamp()),
                event_source_url=None,
                user_data={},
                custom_data={
                    "value": (order_full.total or 0) / 100,
                    "currency": order_full.currency or "EGP",
                    "order_id": str(order_full.id),
                },
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
        meta_cfg = ((s.settings or {}).get("tracking") or {}).get("meta") or {}
        if meta_cfg.get("capi_enabled"):
            out.append(r)
    return out
