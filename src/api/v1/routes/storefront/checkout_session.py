"""Storefront-facing checkout-session token issue endpoint (FR-007b).

Issues a short-lived (30-min) token bound to the caller's cart + the
phone they supply at the checkout Contact step. The token is consumed
by phone-bound anonymous endpoints (currently:
``POST /storefront/{store_slug}/whatsapp/opt-in``, FR-007a; future:
abandoned-checkout recovery, push registration).

Auth = the existing ``numu_cart_session`` HTTP-only cookie (already
issued for cart access). The route relies on the ``get_cart_owner``
dependency to resolve the cart and store.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status

from src.api.v1.routes.storefront._cart_owner import CartOwner, get_cart_owner
from src.api.v1.schemas.stores.whatsapp_connection import (
    CheckoutSessionIssueRequest,
    CheckoutSessionIssueResponse,
)
from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.repositories.checkout_session_repository import (
    CheckoutSessionRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _get_checkout_session_repository() -> CheckoutSessionRepository:
    """Lightweight factory — Redis client is lazy inside the repo."""
    return CheckoutSessionRepository()


@router.post(
    "/storefront/{store_slug}/checkout-session",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckoutSessionIssueResponse,
    tags=["checkout-session"],
)
async def issue_checkout_session(
    body: CheckoutSessionIssueRequest,
    cart_owner: Annotated[CartOwner, Depends(get_cart_owner)],
    repo: Annotated[
        CheckoutSessionRepository, Depends(_get_checkout_session_repository)
    ],
    store_slug: str = Path(..., min_length=1),
) -> CheckoutSessionIssueResponse:
    """Issue a checkout-session token at the Contact step.

    The route MUST be reachable only via the ``numu_cart_session`` cookie
    (or an authenticated session). ``get_cart_owner`` raises 400 if neither
    is present.

    The supplied ``phone`` is canonicalized to E.164 via the project's
    ``PhoneNumber`` value object and stored on the session. Subsequent
    phone-bound endpoints (e.g., WhatsApp opt-in) compare their inbound
    phone against this stored value (FR-007a's "phone-matches-cart" rule).
    """
    # Canonicalize the supplied phone. 422 on parse failure.
    try:
        phone_e164 = PhoneNumber.parse(body.phone.strip(), default_region="EG").e164
    except InvalidPhoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "invalid_phone", "message": str(exc)},
        ) from exc

    # cart_owner.session_id is the numu_cart_session UUID (or
    # customer_id for authenticated). Either way the value uniquely
    # identifies the cart slot; we record it as a string on the session.
    if cart_owner.session_id is not None:
        cart_session_id = str(cart_owner.session_id)
    elif cart_owner.customer_id is not None:
        cart_session_id = str(cart_owner.customer_id)
    else:
        # Should be unreachable — get_cart_owner ensures one of the two
        # is set, but the assertion-style here keeps mypy strict happy.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "no_cart_owner"},
        )

    session = await repo.create(
        cart_session_id=cart_session_id,
        store_id=cart_owner.store_id,
        phone=phone_e164,
    )

    logger.info(
        "checkout_session_issued",
        extra={
            "store_id": str(cart_owner.store_id),
            "expires_at": session.expires_at.isoformat(),
        },
    )

    return CheckoutSessionIssueResponse(
        token=str(session.token),
        expires_at=session.expires_at,
    )
