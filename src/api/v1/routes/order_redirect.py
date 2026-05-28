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


@router.get("/o/{order_id}", include_in_schema=False)
async def redirect_to_order_page(
    order_id: UUID,
    db: AsyncSession = Depends(get_admin_db_session),
) -> RedirectResponse:
    """Resolve an order id to the customer-facing tracking URL on its
    store and return a 302 redirect. Always returns a redirect; never a
    JSON body — Meta-side WhatsApp button taps expect a navigation
    response.
    """
    order = (
        await db.execute(select(OrderModel).where(OrderModel.id == order_id))
    ).scalar_one_or_none()
    if order is None:
        logger.info("order_redirect_unknown_order", order_id=str(order_id))
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    store = (
        await db.execute(select(StoreModel).where(StoreModel.id == order.store_id))
    ).scalar_one_or_none()
    if store is None:
        logger.warning(
            "order_redirect_orphan_order",
            order_id=str(order_id),
            store_id=str(order.store_id),
        )
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    # Prefer the custom domain when set, fall back to the platform
    # subdomain. The storefront's /track/:orderId route handles its own
    # auth gate before rendering sensitive details.
    if store.custom_domain:
        host = store.custom_domain.strip().rstrip("/")
    elif store.subdomain:
        host = f"{store.subdomain}.{_PLATFORM_DOMAIN}"
    else:
        # Stores without a subdomain shouldn't exist (provisioning sets
        # it), but the column is nullable in the schema — fail safe to
        # the apex.
        logger.warning(
            "order_redirect_store_no_host",
            order_id=str(order_id),
            store_id=str(store.id),
        )
        return RedirectResponse(url=_APEX_FALLBACK, status_code=302)

    target = f"https://{host}/track/{order.id}"
    logger.info(
        "order_redirect_resolved",
        order_id=str(order_id),
        store_id=str(store.id),
        target_host=host,
    )
    return RedirectResponse(url=target, status_code=302)
