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
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, Field

from src.api.dependencies.repositories import (
    get_funnel_event_repository,
    get_page_view_repository,
    get_store_repository,
)
from src.config.logging_config import get_logger
from src.core.entities.store import Store
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


@router.post("/track", status_code=204)
async def track_page_view(
    body: TrackPageViewRequest,
    request: Request,
    store_id: Annotated[UUID, Path()],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    pv_repo: Annotated[PageViewRepository, Depends(get_page_view_repository)],
    funnel_repo: Annotated[FunnelEventRepository, Depends(get_funnel_event_repository)],
) -> Response:
    """Record a storefront page view or funnel event. Fire-and-forget, no auth required."""
    store = await store_repo.get_by_id(store_id)
    if not store:
        return Response(status_code=204)  # Silently ignore invalid store

    raw_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (
        request.client.host if request.client else None
    )
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

    # Emit funnel event
    try:
        step_data = body.step_data or {"path": body.path, "referrer": body.referrer}
        await funnel_repo.create(
            tenant_id=store.tenant_id,
            store_id=store.id,
            step=step,
            session_fingerprint=body.fingerprint,
            step_data=step_data,
        )
    except Exception:
        pass  # Non-critical — don't break page tracking

    # ── Meta CAPI fan-out (Wave 1B) ─────────────────────────────────────
    # Gated on per-store activation booleans. The Celery task re-checks
    # capi_enabled at execution time (plan §5.1) so a mid-flight toggle
    # off doesn't fire stale events.
    try:
        _maybe_enqueue_meta_capi(
            store=store,
            step=step,
            body=body,
            ip=ip,
            user_agent=ua,
        )
    except Exception:
        # Never let a CAPI enqueue error break the main tracking call.
        logger.exception("meta_capi_enqueue_failed", extra={"store_id": str(store.id)})

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
    """
    from src.infrastructure.messaging.tasks.meta_capi import (
        FUNNEL_STEP_TO_META_EVENT,
        meta_capi_send_event,
    )

    meta_event_name = FUNNEL_STEP_TO_META_EVENT.get(step)
    if not meta_event_name:
        return

    meta_cfg = ((store.settings or {}).get("tracking") or {}).get("meta") or {}
    pixel_id = meta_cfg.get("pixel_id")
    if not (meta_cfg.get("capi_enabled") and pixel_id):
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

    meta_capi_send_event.delay(
        store_id=str(store.id),
        pixel_id=pixel_id,
        event_name=meta_event_name,
        event_id=event_id,
        event_time=int(event_time.timestamp()),
        event_source_url=page_url,
        user_data=user_data,
        custom_data=custom_data,
        # The settings-PUT debug_mode ladder auto-attaches a code in the
        # task itself; passing None here means "let the task decide".
        test_event_code=None,
        action_source="website",
    )
