"""Public order-link redirector — ``GET /o/{order_id}``.

Mounted at the *root* of the app (no ``/api/v1/`` prefix) because Meta
URL CTA buttons on our system templates use ``https://numueg.app/o/{{1}}``
as a stable, brand-recognizable short link the customer clicks straight
from WhatsApp.

The route is bookmarkable, anonymous, and side-effect-free:

1. Look up the order by id.
2. Resolve the owning store.
3. Build the destination — either the store's custom domain or its
   ``<subdomain>.numueg.app`` storefront — then 302 to ``/track/<order_id>``
   on that host.
4. Fall back to the apex marketing site when anything fails (unknown
   order id, deleted store, etc.) so the customer always lands on
   something other than a 404.

The route deliberately does NOT enforce auth — the order id itself is
the secret. UUIDv4 (122 bits of entropy) is effectively unguessable, so
publishing it in a WhatsApp template is acceptable. The destination
``/track/{order_id}`` page on each storefront performs its own access
checks (cookie / OTP / phone-number-on-file gate) before rendering
sensitive details.

Sibling nginx update (``docker/nginx/nginx.conf``) routes ``/o/`` →
this FastAPI container, matching the existing ``/api/`` proxy_pass
pattern.
"""

import re
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.logging_config import get_logger
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.store import StoreModel

logger = get_logger(__name__)

# Mounted at the root in src/main.py with prefix="" — keeps the public
# URL clean (``numueg.app/o/...``).
router = APIRouter(tags=["public"])

# Brand fallback when the order can't be resolved. Keeps the customer
# inside the NUMU surface rather than dumping them on a stack trace.
_APEX_FALLBACK = "https://numueg.app/"
_PLATFORM_DOMAIN = "numueg.app"

# Whitelist for the subdomain segment in the new self-describing format
# (``<subdomain>/<order_id>``). Same shape DNS + the merchant-hub
# subdomain validator (config/settings.py) constrains the column to.
_SUBDOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _looks_like_uuid(s: str) -> bool:
    try:
        UUID(s)
        return True
    except (TypeError, ValueError):
        return False


async def _resolve_host(store: StoreModel) -> str | None:
    """Return the host the storefront lives on — custom_domain wins
    when set, falls back to ``<subdomain>.<platform_domain>``.
    None when the store has neither (data hole).
    """
    if store.custom_domain:
        return store.custom_domain.strip().rstrip("/")
    if store.subdomain:
        return f"{store.subdomain}.{_PLATFORM_DOMAIN}"
    return None


@router.get("/o/{path:path}", include_in_schema=False)
async def redirect_to_order_page(
    path: str,
    db: AsyncSession = Depends(get_admin_db_session),
) -> RedirectResponse:
    """Resolve a WhatsApp template's URL-button substitution to the
    customer-facing storefront order page and return a 302.

    THREE URL value formats accepted (single route, branched by shape):

    1. ``<subdomain>/<order_id>`` — **self-describing** format. The
       redirector forwards directly to
       ``https://<subdomain>.numueg.app/track/<order_id>`` without any
       DB lookup, so test-env orders (stored in the test DB) still
       resolve even though this redirector runs against the prod DB.
       Subdomain already carries the env suffix on test/stage stores.

    2. ``<uuid>`` — legacy single-segment UUID. Looks up the order
       in the prod DB and builds the host from
       ``store.custom_domain`` or ``store.subdomain``. Only works for
       prod orders.

    3. ``<order_number>`` (e.g. ``ORD-000017``) — **back-compat** path
       for WhatsApp messages already in customers' chat history that
       were sent before callers were updated to pass the UUID
       (notification_tasks.py + checkout.py). Looks up the order by
       its display order_number; takes the most recent match if the
       number happens to repeat across stores (rare given typical
       sequence allocation but not impossible).

    Anything else (unparseable path, store with no host, order not
    found) falls back to the apex marketing site so the customer always
    lands on a NUMU surface rather than a 404.
    """
    # Format 1 — self-describing subdomain/<id> value.
    if "/" in path:
        head, _, tail = path.partition("/")
        if _SUBDOMAIN_RE.match(head) and tail:
            target = f"https://{head}.{_PLATFORM_DOMAIN}/track/{tail}"
            logger.info(
                "order_redirect_self_describing",
                extra={
                    "subdomain": head,
                    "order_id": tail,
                    "target": target,
                },
            )
            return RedirectResponse(url=target, status_code=302)
        logger.info("order_redirect_malformed_path", extra={"path": path})
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    # Format 2 — bare UUID.
    if _looks_like_uuid(path):
        order_id = UUID(path)
        order = (
            await db.execute(select(OrderModel).where(OrderModel.id == order_id))
        ).scalar_one_or_none()
        if order is None:
            logger.info(
                "order_redirect_unknown_order", extra={"order_id": str(order_id)}
            )
            return RedirectResponse(url=_APEX_FALLBACK, status_code=302)
    else:
        # Format 3 — order_number back-compat (e.g. "ORD-000017").
        # Global lookup (no store scope); take the most recent. Older
        # orders with the same number lose ties but the customer is
        # almost certainly on the latest order anyway.
        order = (
            await db.execute(
                select(OrderModel)
                .where(OrderModel.order_number == path)
                .order_by(OrderModel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if order is None:
            logger.info("order_redirect_unknown_order_number", extra={"path": path})
            return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    # Resolve the destination host from the matched order's store.
    store = (
        await db.execute(select(StoreModel).where(StoreModel.id == order.store_id))
    ).scalar_one_or_none()
    if store is None:
        logger.warning(
            "order_redirect_orphan_order",
            extra={"order_id": str(order.id), "store_id": str(order.store_id)},
        )
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    host = await _resolve_host(store)
    if host is None:
        logger.warning(
            "order_redirect_store_no_host",
            extra={"order_id": str(order.id), "store_id": str(store.id)},
        )
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    target = f"https://{host}/track/{order.id}"
    logger.info(
        "order_redirect_resolved",
        extra={
            "order_id": str(order.id),
            "store_id": str(store.id),
            "target_host": host,
            "input_format": "uuid" if _looks_like_uuid(path) else "order_number",
        },
    )
    return RedirectResponse(url=target, status_code=302)
