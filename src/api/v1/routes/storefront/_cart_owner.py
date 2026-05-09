"""Resolve the cart "owner" — either an authenticated customer or a
guest session — for the SDK cart routes.

Guest cart story:
  Storefront visitors must be able to add items to a cart BEFORE
  creating an account. We persist the cart in Redis keyed by either
  `customer_id` (authenticated) OR `session_id` (guest cookie). Login
  later optionally merges the guest cart into the customer cart.

Cookie:
  `numu_cart_session` — random UUID, 30-day life, HttpOnly, SameSite=Lax.
  Generated on first cart access and echoed back via Set-Cookie. The
  Next.js storefront proxy passes both `cookie` and the inbound
  `set-cookie` through, so the cookie is established with the browser
  on the first GET /api/cart roundtrip and reused on every subsequent
  cart write.

Store identification:
  Anonymous carts must still know which store they belong to (carts
  don't span stores). We resolve via:
    1. The authenticated customer's store_id (when logged in), OR
    2. The `x-numu-host` header set by the storefront proxy →
       resolved to a store row via `fetch_store_by_host`.

  Failing both, we 400 — better than silently mis-keying the cart to
  the wrong store.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, HTTPException, Request, Response, status

from src.api.dependencies.auth import get_optional_customer
from src.api.dependencies.repositories import get_store_repository
from src.core.entities.customer import Customer
from src.infrastructure.repositories.store_repository import StoreRepository

logger = logging.getLogger(__name__)

CART_SESSION_COOKIE = "numu_cart_session"
CART_SESSION_MAX_AGE = 60 * 60 * 24 * 30  # 30 days


@dataclass
class CartOwner:
    """Identifies who a cart belongs to.

    Exactly one of `customer_id` / `session_id` is set. `store_id` is
    always set; routes use it to look up products and validate
    cross-store cart writes.
    """

    customer_id: UUID | None
    session_id: UUID | None
    store_id: UUID
    is_guest: bool

    @property
    def cart_key(self) -> tuple[UUID, UUID]:
        """The (id, store_id) tuple Redis keys the cart on.

        For authenticated buyers we use customer_id; for guests we use
        the session_id. The repo's get_by_customer_id /
        get_by_session_id paths consume this.
        """
        owner_id = self.customer_id if self.customer_id else self.session_id
        # `assert` not exception: callers always set one of the two.
        assert owner_id is not None
        return (owner_id, self.store_id)


async def _resolve_store_id_from_host(
    request: Request,
    store_repo: StoreRepository,
) -> UUID | None:
    """Look up the store from the `x-numu-host` header set by the
    storefront's proxy. Returns None if the header is missing or the
    store can't be resolved — the caller decides whether to 400."""
    host = request.headers.get("x-numu-host") or ""
    host = host.split(":")[0].lower().strip()
    if not host:
        return None
    # Subdomain pattern: `<sub>.numueg.app` / `<sub>.localhost`.
    sub: str | None = None
    if host.endswith(".numueg.app"):
        sub = host.removesuffix(".numueg.app")
    elif host.endswith(".localhost"):
        sub = host.removesuffix(".localhost")
    if sub:
        store = await store_repo.get_by_subdomain(sub)
        if store:
            return store.id
    # Custom-domain fallback.
    try:
        store = await store_repo.get_by_custom_domain(host)
        if store:
            return store.id
    except Exception:
        pass
    return None


async def get_cart_owner(
    request: Request,
    response: Response,
    customer: Annotated[Customer | None, Depends(get_optional_customer)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> CartOwner:
    """FastAPI dependency: return the CartOwner for the current request.

    Side effect: when a guest visitor has no `numu_cart_session` cookie,
    we mint one and set it on the response. The Next.js storefront's
    cart-proxy passes Set-Cookie through verbatim so the browser
    establishes the cookie on the first cart roundtrip.
    """
    if customer is not None:
        return CartOwner(
            customer_id=customer.id,
            session_id=None,
            store_id=customer.store_id,
            is_guest=False,
        )

    # Guest path — need a store_id from the host.
    store_id = await _resolve_store_id_from_host(request, store_repo)
    if store_id is None:
        # Without a store we can't safely key a cart. Better to 400
        # loudly than silently bind the cart to the wrong tenant.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Unable to identify store for guest cart. The storefront "
                "proxy should forward `x-numu-host`."
            ),
        )

    session_str = request.cookies.get(CART_SESSION_COOKIE)
    session_id: UUID
    if session_str:
        try:
            session_id = UUID(session_str)
        except ValueError:
            session_id = uuid4()
    else:
        session_id = uuid4()

    # Always (re)set the cookie so its TTL slides with activity. HttpOnly
    # because no theme code reads this — only the backend touches it,
    # via the proxy. SameSite=Lax: lets the cookie survive top-level
    # nav from the merchant hub iframe / customizer back to the live
    # storefront, while still being protected from cross-site posts.
    # `Secure` only on HTTPS so dev (http://lumiere.localhost:3000) works.
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto") == "https"
    )
    secure_flag = "; Secure" if is_https else ""
    response.headers.append(
        "set-cookie",
        f"{CART_SESSION_COOKIE}={session_id}; Path=/; "
        f"Max-Age={CART_SESSION_MAX_AGE}; HttpOnly; SameSite=Lax{secure_flag}",
    )

    return CartOwner(
        customer_id=None,
        session_id=session_id,
        store_id=store_id,
        is_guest=True,
    )
