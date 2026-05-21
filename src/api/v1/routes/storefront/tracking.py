"""Storefront page view + funnel tracking with Meta CAPI fan-out.

This is the single chokepoint that receives every funnel event from the
storefront. After the existing FunnelEvent persist (untouched), we
opportunistically fan out to Meta Conversions API when the store has
``capi_enabled = true``.

The fan-out is deliberately gated **here** AND inside the Celery task —
so a merchant flipping ``capi_enabled = false`` mid-session doesn't
trigger stale fan-outs from queued jobs.
"""

import ipaddress
import json
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field, ValidationError

from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_page_view_repository,
    get_store_repository,
)
from src.application.services.attribution_sanitizer import sanitize_utm
from src.application.services.campaign_resolver import resolve_campaign_id
from src.config import settings
from src.config.logging_config import get_logger
from src.core.entities.attribution import AttributionSnapshot
from src.core.entities.store import Store
from src.infrastructure.cache.idempotency_keys import (
    IdempotencyKeys,
    get_idempotency_keys,
)
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.page_view_repository import PageViewRepository
from src.infrastructure.repositories.store_repository import StoreRepository

logger = get_logger(__name__)
router = APIRouter()


_VALID_FUNNEL_STEPS = {
    "page_view",
    "product_view",
    "add_to_cart",
    "checkout_started",
    "order_completed",
    "order_delivered",
}


async def _emit_funnel_event(
    *,
    funnel_repo: FunnelEventRepository,
    idempotency: IdempotencyKeys,
    tenant_id: UUID,
    store_id: UUID,
    step: str,
    session_fingerprint: str | None,
    customer_id: UUID | None,
    step_data: dict | None,
    event_id: UUID | None,
    # Feature 001 — attribution columns on funnel_events. All optional;
    # null for visitors who never landed via a campaign-tagged URL.
    utm_source: str | None = None,
    utm_medium: str | None = None,
    utm_campaign: str | None = None,
    utm_term: str | None = None,
    utm_content: str | None = None,
    campaign_id: UUID | None = None,
    referrer: str | None = None,
) -> None:
    """Persist a funnel event — async via Celery when the kill switch is on.

    Behaviour matrix:

    * ``analytics_async_enabled=True`` (default): client-provided
      ``event_id`` is run through the Redis SET-NX dedupe; if newly
      claimed, the event is pushed to the Celery ``analytics`` queue
      and the task does an ``INSERT … ON CONFLICT DO NOTHING`` on
      ``funnel_events``. If the event was already claimed, this is a
      no-op (the previous request already enqueued it).
    * ``analytics_async_enabled=False`` (kill switch): falls back to a
      synchronous ``funnel_repo.create(...)`` — the legacy code path.

    Failures here are always swallowed by the caller. Analytics
    outages must never break the storefront response.
    """
    if not settings.analytics_async_enabled:
        await funnel_repo.create(
            tenant_id=tenant_id,
            store_id=store_id,
            step=step,
            session_fingerprint=session_fingerprint,
            customer_id=customer_id,
            step_data=step_data,
            event_id=event_id,
            utm_source=utm_source,
            utm_medium=utm_medium,
            utm_campaign=utm_campaign,
            utm_term=utm_term,
            utm_content=utm_content,
            campaign_id=campaign_id,
            referrer=referrer,
        )
        return

    effective_event_id = event_id or uuid4()
    if not await idempotency.claim(f"funnel_event:{effective_event_id}"):
        # Already claimed by an earlier request — skip the redundant push.
        return

    from src.infrastructure.messaging.tasks.analytics_ingest_task import (
        ingest_funnel_event,
    )

    ingest_funnel_event.apply_async(
        kwargs={
            "event": {
                "event_id": str(effective_event_id),
                "tenant_id": str(tenant_id),
                "store_id": str(store_id),
                "customer_id": str(customer_id) if customer_id else None,
                "session_fingerprint": session_fingerprint,
                "step": step,
                "step_data": step_data,
                "utm_source": utm_source,
                "utm_medium": utm_medium,
                "utm_campaign": utm_campaign,
                "utm_term": utm_term,
                "utm_content": utm_content,
                "campaign_id": str(campaign_id) if campaign_id else None,
                "referrer": referrer,
            }
        },
        queue="analytics",
    )


def _read_attribution_envelope(
    body_attribution: AttributionSnapshot | None,
    request: Request,
) -> AttributionSnapshot | None:
    """Resolve the attribution envelope for a tracking request.

    Preference order (feature 001 SEC-001 + R-01):
        1. body.attribution — present when the storefront has been
           updated to send the envelope explicitly.
        2. Cookie ``numu_attribution`` — legacy clients that don't yet
           send the envelope but have the cookie set by the visitor's
           prior landing.

    On parse failure, returns None — never raises. Analytics outages
    must never break the storefront response.
    """
    if body_attribution is not None:
        return body_attribution
    raw = request.cookies.get("numu_attribution")
    if not raw:
        return None
    try:
        decoded = unquote(raw)
        parsed = json.loads(decoded)
    except (json.JSONDecodeError, ValueError):
        return None
    # Forwards-compatibility hatch: a v2 client talking to a v1 server
    # degrades silently to "no attribution" rather than mis-parsing the
    # envelope (per contracts/storefront-attribution-api.md). Check v
    # BEFORE Pydantic validation since unrelated future fields might
    # otherwise still validate against the v1 model.
    if not isinstance(parsed, dict) or parsed.get("v") != 1:
        return None
    try:
        return AttributionSnapshot.model_validate(parsed)
    except (ValidationError, ValueError):
        return None


def _anonymize_ip(raw: str | None) -> str | None:
    """Truncate IPv4 to /24 and IPv6 to /48.

    Keeps geo-bucket utility (governorate-level analytics still works
    in MENA where we route through carrier-grade NAT anyway) without
    persisting a host-identifying address. Unparseable input returns
    None rather than the raw bytes — better to lose the row than store
    something that turns out to be PII.
    """
    if not raw:
        return None
    try:
        addr = ipaddress.ip_address(raw)
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv4Address):
        net = ipaddress.ip_network(f"{addr}/24", strict=False)
        return str(net.network_address)
    net = ipaddress.ip_network(f"{addr}/48", strict=False)
    return str(net.network_address)


class TrackPageViewRequest(BaseModel):
    """Request body for ``POST /storefront/store/{id}/track``.

    The bottom block of fields (``event_id``, ``event_time``, ``page_url``,
    ``fbp``, ``fbc``, ``user_data``) was added for Meta CAPI fan-out
    (Wave 1B). All optional so old callers — including in-flight beacons
    from older bundles — keep working.
    """

    path: str = Field(max_length=500)
    fingerprint: str | None = Field(None, max_length=64)
    referrer: str | None = Field(None, max_length=500)
    # Optional explicit funnel step. When provided, overrides path-based inference.
    step: str | None = Field(None, max_length=32)
    step_data: dict | None = None

    # ── Meta CAPI fan-out fields (Wave 1B) ──────────────────────────────
    # Browser-generated event_id; the same value goes to Pixel via
    # `fbq("track", ..., {eventID})`. When omitted we generate one
    # server-side — the CAPI fire still happens but Meta-side dedup
    # against the browser fire is per-attempt only.
    event_id: str | None = Field(None, max_length=128)
    event_time: datetime | None = None
    page_url: str | None = Field(None, max_length=2000)
    fbp: str | None = Field(None, max_length=128)
    fbc: str | None = Field(None, max_length=256)
    # PII for CAPI matching — Meta hashes nothing on its end; we hash
    # in the Celery task before transmission via meta/hashing.py.
    user_data: dict | None = None
    # Wave 3 Phase 18 — when true, forwarded to Meta's CAPI `opt_out`
    # field so the event lands as a modeled conversion (attribution
    # math only, no first-party data storage on Meta's side). Set by
    # the storefront when the visitor has explicitly denied marketing
    # consent.
    opt_out: bool | None = None
    # Feature 001 — full attribution envelope (numu_attribution cookie shape).
    # When present, the route stamps utm_* + campaign_id on the funnel
    # event so per-campaign funnel reports work even when the URL has
    # lost the original UTMs after the first hop. Absent for legacy
    # clients — the route falls back to parsing the cookie from the
    # Cookie: header automatically.
    attribution: AttributionSnapshot | None = None


@router.post("/track", status_code=204)
async def track_page_view(
    body: TrackPageViewRequest,
    request: Request,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    idempotency: Annotated[IdempotencyKeys, Depends(get_idempotency_keys)],
) -> Response:
    """Record a storefront page view or funnel event. Fire-and-forget, no auth required."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        return Response(status_code=204)  # Silently ignore invalid store

    raw_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    # `ip` (anonymized to /24) is what we PERSIST — keeps page_views
    # rows GDPR-compliant while still bucketing analytics by region.
    # `raw_ip` is what we FORWARD to Meta CAPI further down: Meta's
    # match-quality model needs the full client IP and refuses to
    # match on a /24 network address.
    ip = _anonymize_ip(raw_ip)
    ua = request.headers.get("user-agent", "")[:500]

    # Resolve funnel step: explicit > path-based inference. The storefront
    # routes `/product/:id` (singular) for detail pages, so check for that
    # specifically. `/products` (plural) is the listing page → page_view.
    if body.step and body.step in _VALID_FUNNEL_STEPS:
        step = body.step
    else:
        path = body.path or ""
        is_product_detail = "/product/" in path and "/products/" not in path
        step = "product_view" if is_product_detail else "page_view"

    # Only persist a page_view row when this is actually a navigation event.
    # Pure funnel events (add_to_cart, etc.) shouldn't pollute the page_views
    # table — they have no meaningful URL path of their own.
    if step in ("page_view", "product_view"):
        await pv_repo.create(
            store_id=store.id,
            tenant_id=store.tenant_id,
            path=body.path,
            session_fingerprint=body.fingerprint,
            ip_address=ip,
            user_agent=ua,
            referrer=body.referrer,
        )

        # Update real-time counters
        try:
            from src.infrastructure.cache.realtime_counters import record_page_view

            await record_page_view(store.id, body.fingerprint, body.path)
        except Exception:
            pass

    # Emit funnel event — async via Celery when the kill switch is on
    # (default), otherwise falls back to the legacy synchronous write.
    try:
        step_data = body.step_data or {"path": body.path, "referrer": body.referrer}
        funnel_event_id: UUID | None = None
        if body.event_id:
            try:
                funnel_event_id = UUID(body.event_id)
            except ValueError:
                funnel_event_id = None
        # Feature 001 — resolve attribution for the funnel row. Prefer the
        # body envelope; fall back to the cookie. Then sanitize each UTM
        # (SEC-005 — visitors can craft arbitrary URLs) and look up the
        # campaign_id via the Crockford short_code (SEC-006 — scoped by
        # store_id; cross-tenant resolution is impossible).
        attribution = _read_attribution_envelope(body.attribution, request)
        last_touch = attribution.last_touch if attribution else None
        f_utm_source = sanitize_utm(last_touch.utm_source if last_touch else None)
        f_utm_medium = sanitize_utm(last_touch.utm_medium if last_touch else None)
        f_utm_campaign = sanitize_utm(last_touch.utm_campaign if last_touch else None)
        f_utm_term = sanitize_utm(last_touch.utm_term if last_touch else None)
        f_utm_content = sanitize_utm(last_touch.utm_content if last_touch else None)
        f_referrer = (
            last_touch.referrer if last_touch and last_touch.referrer else body.referrer
        )
        f_campaign_id = await resolve_campaign_id(
            session=funnel_repo.session,
            store_id=store.id,
            utm_campaign=f_utm_campaign,
        )
        await _emit_funnel_event(
            funnel_repo=funnel_repo,
            idempotency=idempotency,
            tenant_id=store.tenant_id,
            store_id=store.id,
            step=step,
            session_fingerprint=body.fingerprint,
            customer_id=None,
            step_data=step_data,
            event_id=funnel_event_id,
            utm_source=f_utm_source,
            utm_medium=f_utm_medium,
            utm_campaign=f_utm_campaign,
            utm_term=f_utm_term,
            utm_content=f_utm_content,
            campaign_id=f_campaign_id,
            referrer=f_referrer,
        )
    except Exception:
        pass  # Non-critical — don't break page tracking

    # ── Meta CAPI fan-out (Wave 1B) ─────────────────────────────────────
    # Gated on per-store activation booleans. The Celery task re-checks
    # capi_enabled at execution time (plan §5.1) so a mid-flight toggle
    # off doesn't fire stale events.
    # Forward `raw_ip` (NOT the anonymized form): Meta's match-quality
    # model keys on full client IP and refuses a /24 network address.
    try:
        _maybe_enqueue_meta_capi(
            store=store,
            step=step,
            body=body,
            ip=raw_ip,
            user_agent=ua,
        )
    except Exception:
        # Never let a CAPI enqueue error break the main tracking call.
        logger.exception("meta_capi_enqueue_failed", extra={"store_id": str(store.id)})

    # Phase 4.3 — fan out to merchant-configured analytics providers
    # (GA4 / TikTok). Meta CAPI is handled by the Celery enqueue above.
    # Fire-and-forget; pixel failures must never block the storefront
    # response.
    try:
        from src.application.services.analytics_dispatcher import (
            AnalyticsDispatcher,
            AnalyticsEvent,
        )

        dispatcher = AnalyticsDispatcher(getattr(store, "settings", {}) or {})
        if dispatcher.enabled_providers:
            await dispatcher.dispatch(
                AnalyticsEvent(
                    event_name=step,
                    payload=body.step_data or {},
                    client_id=body.fingerprint or "",
                    source_ip=ip,
                    user_agent=ua,
                )
            )
    except Exception:
        pass

    return Response(status_code=204)


# ─── Phase 4.3 — generic analytics event endpoint ─────────────────


class TrackAnalyticsEventRequest(BaseModel):
    """SDK's `useAnalytics().track()` POSTs this shape.

    Distinct from the page-view tracker above because the event names
    are arbitrary (custom events) and there's no path/referrer
    obligation. The funnel-event row is recorded for reporting AND
    the merchant's GA4/Meta/TikTok pixels are fanned out.
    """

    event: str = Field(..., max_length=64)
    payload: dict | None = None
    fingerprint: str | None = Field(None, max_length=64)
    ts: int | None = None  # client-side ms timestamp; informational
    # Feature 001 — full attribution envelope. Same behavior as
    # TrackPageViewRequest: when present, stamps utm_* + campaign_id on
    # the funnel row; when absent, the route falls back to parsing the
    # numu_attribution cookie from the Cookie: header.
    attribution: AttributionSnapshot | None = None


@router.post(
    "/track-event",
    status_code=204,
    summary="Generic analytics event (fans to provider pixels)",
    operation_id="track_analytics_event",
)
async def track_analytics_event(
    body: TrackAnalyticsEventRequest,
    request: Request,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
    idempotency: Annotated[IdempotencyKeys, Depends(get_idempotency_keys)],
):
    """Phase 4.3 — receive a generic analytics event from the SDK.

    Records the event in the funnel-events table for the merchant's
    own dashboards AND fans out to GA4 / Meta CAPI / TikTok per the
    store's `settings.analytics` config. All failures are swallowed —
    analytics outages must never surface to the customer.
    """
    store = await store_repo.get_by_id(store_id)
    if not store:
        return Response(status_code=204)

    raw_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
    ip = _anonymize_ip(raw_ip)
    ua = request.headers.get("user-agent", "")[:500]

    # Funnel row: keeps a per-store audit log of every event the
    # storefront fired, queryable by step name. Useful for "how many
    # add_to_wishlist events did we get last week" without depending
    # on a third-party pixel.
    try:
        # Feature 001 — same attribution resolution as track_page_view:
        # prefer body.attribution, fall back to cookie, sanitize, resolve
        # campaign_id (SEC-006 store-scoped).
        attribution = _read_attribution_envelope(body.attribution, request)
        last_touch = attribution.last_touch if attribution else None
        e_utm_source = sanitize_utm(last_touch.utm_source if last_touch else None)
        e_utm_medium = sanitize_utm(last_touch.utm_medium if last_touch else None)
        e_utm_campaign = sanitize_utm(last_touch.utm_campaign if last_touch else None)
        e_utm_term = sanitize_utm(last_touch.utm_term if last_touch else None)
        e_utm_content = sanitize_utm(last_touch.utm_content if last_touch else None)
        e_referrer = last_touch.referrer if last_touch else None
        e_campaign_id = await resolve_campaign_id(
            session=funnel_repo.session,
            store_id=store.id,
            utm_campaign=e_utm_campaign,
        )
        await _emit_funnel_event(
            funnel_repo=funnel_repo,
            idempotency=idempotency,
            tenant_id=store.tenant_id,
            store_id=store.id,
            step=body.event,
            session_fingerprint=body.fingerprint,
            customer_id=None,
            step_data=body.payload or {},
            event_id=None,
            utm_source=e_utm_source,
            utm_medium=e_utm_medium,
            utm_campaign=e_utm_campaign,
            utm_term=e_utm_term,
            utm_content=e_utm_content,
            campaign_id=e_campaign_id,
            referrer=e_referrer,
        )
    except Exception:
        pass

    try:
        from src.application.services.analytics_dispatcher import (
            AnalyticsDispatcher,
            AnalyticsEvent,
        )

        dispatcher = AnalyticsDispatcher(getattr(store, "settings", {}) or {})
        if dispatcher.enabled_providers:
            await dispatcher.dispatch(
                AnalyticsEvent(
                    event_name=body.event,
                    payload=body.payload or {},
                    client_id=body.fingerprint or "",
                    source_ip=ip,
                    user_agent=ua,
                )
            )
    except Exception:
        pass

    return Response(status_code=204)


def _maybe_enqueue_meta_capi(
    *,
    store: Store,
    step: str,
    body: TrackPageViewRequest,
    ip: str | None,
    user_agent: str,
) -> None:
    """Enqueue ``meta_capi_send_event`` when this store has CAPI configured.

    Mapping is plan §5.3:
        page_view        → PageView
        product_view     → ViewContent
        add_to_cart      → AddToCart
        checkout_started → InitiateCheckout
        order_completed  → Purchase  (defensive — the webhook also fires)

    Wave 2 Phase 13: when the store has multiple pixels configured,
    fans out one task per capi-enabled pixel. Each pixel is a separate
    Meta dedup namespace, so the same browser-issued ``event_id`` works
    across all of them (no per-pixel namespacing needed).
    """
    from src.application.services.meta_pixel_resolver import resolve_pixels
    from src.infrastructure.messaging.tasks.meta_capi import (
        FUNNEL_STEP_TO_META_EVENT,
        meta_capi_send_event,
    )

    meta_event_name = FUNNEL_STEP_TO_META_EVENT.get(step)
    if not meta_event_name:
        return

    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixels = resolve_pixels(meta_cfg, mode="capi")
    if not pixels:
        return

    # Don't re-fire Purchase from /track when the webhook is the source
    # of truth — but if the storefront has explicitly fired
    # order_completed (browser confirmation page), let it through. The
    # UNIQUE constraint on (store_id, event_id) dedupes anyway, so this
    # is just a quota optimization. We do let it through here so dedup
    # works correctly when Purchase arrives from /track first.

    event_id = body.event_id or str(uuid4())
    event_time = body.event_time or datetime.now(UTC)
    page_url = body.page_url

    # Compose user_data from request signals + explicit body.user_data.
    user_data = dict(body.user_data or {})
    if "fbp" not in user_data and body.fbp:
        user_data["fbp"] = body.fbp
    if "fbc" not in user_data and body.fbc:
        user_data["fbc"] = body.fbc
    if "ip" not in user_data and ip:
        user_data["ip"] = ip
    if "user_agent" not in user_data and user_agent:
        user_data["user_agent"] = user_agent

    custom_data = dict(body.step_data or {})
    event_time_int = int(event_time.timestamp())

    # Fan out — one task per capi-enabled pixel.
    for pixel in pixels:
        meta_capi_send_event.delay(
            store_id=str(store.id),
            pixel_id=pixel.pixel_id,
            event_name=meta_event_name,
            event_id=event_id,
            event_time=event_time_int,
            event_source_url=page_url,
            user_data=user_data,
            custom_data=custom_data,
            # The settings-PUT debug_mode ladder auto-attaches a code in the
            # task itself; passing None here means "let the task decide".
            test_event_code=None,
            action_source="website",
            # Wave 3 Phase 18 — opt_out flows through to Meta so denied-
            # marketing events count as modeled conversions only.
            opt_out=bool(body.opt_out) if body.opt_out is not None else False,
        )
