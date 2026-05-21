"""Storefront wishlist routes (Phase 4.5).

URL: /storefront/me/wishlist

Authenticated customers AND guest visitors both get persistence —
guests via the same `numu_cart_session` cookie carts already use, so
the cart's session→customer merge sweep handles wishlist alongside.

The SDK's `useWishlist` hook (already shipped in Phase 2 with
localStorage fallback) detects the customer state via
`useCustomer()` and, when authed, switches to talking to these
endpoints. Anonymous + offline still gets the localStorage path.
"""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from src.api.dependencies.repositories import (
    get_product_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.routes.storefront._cart_owner import CartOwner, get_cart_owner
from src.core.entities.product import ProductStatus
from src.infrastructure.repositories import ProductRepository, StoreRepository

router = APIRouter()


# ─── Schemas ───────────────────────────────────────────────────────


class WishlistItemRequest(BaseModel):
    product_id: UUID
    variant_id: UUID | None = None


class WishlistItemResponse(BaseModel):
    id: str
    product_id: str
    variant_id: str | None = None
    added_at: str
    # Product summary so the client doesn't need a second fetch when
    # rendering the wishlist page. We keep it small (not the full
    # ProductResponse shape) — themes that want full product detail
    # can call /storefront/store/{id}/products/{slug} directly.
    product_name: str | None = None
    product_slug: str | None = None
    product_image: str | None = None
    product_price: float | None = None
    product_currency: str | None = None
    in_stock: bool | None = None


class WishlistResponse(BaseModel):
    items: list[WishlistItemResponse]
    total: int


# ─── Helper ────────────────────────────────────────────────────────


async def _attach_product_summary(
    items: list[Any],
    product_repo: ProductRepository,
) -> list[WishlistItemResponse]:
    """Hydrate wishlist rows with a per-product summary.

    We fan out N product fetches; for typical wishlist sizes (<50)
    this is fine. If wishlists ever exceed 100 items per customer
    we'd want a single `WHERE id = ANY(...)` query — but the SDK
    caps the localStorage fallback at "reasonable" without enforcing
    a hard limit, so revisit when we see the use case.
    """
    out: list[WishlistItemResponse] = []
    for it in items:
        product = await product_repo.get_by_id(it.product_id)
        if not product:
            # Product was deleted; surface a sparse row so the theme
            # can render "Item no longer available" + remove button.
            out.append(
                WishlistItemResponse(
                    id=str(it.id),
                    product_id=str(it.product_id),
                    variant_id=str(it.variant_id) if it.variant_id else None,
                    added_at=it.added_at.isoformat(),
                )
            )
            continue
        out.append(
            WishlistItemResponse(
                id=str(it.id),
                product_id=str(it.product_id),
                variant_id=str(it.variant_id) if it.variant_id else None,
                added_at=it.added_at.isoformat(),
                product_name=product.name,
                product_slug=product.slug,
                product_image=product.images[0] if product.images else None,
                product_price=float(product.price.amount) if product.price else None,
                product_currency=product.price.currency.value
                if product.price
                else None,
                in_stock=product.is_in_stock,
            )
        )
    return out


# ─── Routes ────────────────────────────────────────────────────────


@router.get(
    "/wishlist",
    response_model=SuccessResponse[WishlistResponse],
    summary="Get wishlist",
    operation_id="get_wishlist",
)
async def get_wishlist(
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Return the wishlist for the current owner (customer or guest).

    Empty list when neither customer nor session has any saved items.
    """

    # Lazy import + per-request session via the repo's get_db dep
    # would require restructuring the repo; for now the wishlist repo
    # accepts a raw session. We pull the session out of the cart-owner
    # path's chain by going through the session-bearing repo helper.
    items = await _resolve_wishlist_for_owner(owner, product_repo)
    return SuccessResponse(
        data=WishlistResponse(items=items, total=len(items)),
        message="Wishlist retrieved",
    )


@router.post(
    "/wishlist",
    response_model=SuccessResponse[WishlistResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Add to wishlist",
    operation_id="add_to_wishlist",
)
async def add_to_wishlist(
    body: WishlistItemRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Idempotent — adding the same (product, variant) twice is a
    no-op that returns the existing wishlist."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.wishlist_repository import (
        WishlistRepository,
    )

    # Validate the product exists + belongs to the owner's store.
    # Cross-store wishlist additions would let a malicious request
    # probe products across tenants by id.
    product = await product_repo.get_by_id(body.product_id)
    if not product or product.store_id != owner.store_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Product not found in this store.",
        )
    if product.status != ProductStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Product is not available.",
        )

    store = await store_repo.get_by_id(owner.store_id)
    tenant_id = (store.tenant_id if store else None) or owner.store_id

    async with AsyncSessionLocal() as session:
        repo = WishlistRepository(session)
        await repo.add(
            tenant_id=tenant_id,
            store_id=owner.store_id,
            customer_id=owner.customer_id,
            session_id=str(owner.session_id) if owner.session_id else None,
            product_id=body.product_id,
            variant_id=body.variant_id,
        )

    items = await _resolve_wishlist_for_owner(owner, product_repo)
    return SuccessResponse(
        data=WishlistResponse(items=items, total=len(items)),
        message="Item added to wishlist",
    )


@router.delete(
    "/wishlist",
    response_model=SuccessResponse[WishlistResponse],
    summary="Remove from wishlist",
    operation_id="remove_from_wishlist",
)
async def remove_from_wishlist(
    body: WishlistItemRequest,
    owner: Annotated[CartOwner, Depends(get_cart_owner)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
):
    """Idempotent — removing an absent item still returns the current
    wishlist."""
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.wishlist_repository import (
        WishlistRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = WishlistRepository(session)
        await repo.remove(
            customer_id=owner.customer_id,
            session_id=str(owner.session_id) if owner.session_id else None,
            product_id=body.product_id,
            variant_id=body.variant_id,
        )

    items = await _resolve_wishlist_for_owner(owner, product_repo)
    return SuccessResponse(
        data=WishlistResponse(items=items, total=len(items)),
        message="Item removed from wishlist",
    )


# ─── Internals ────────────────────────────────────────────────────


async def _resolve_wishlist_for_owner(
    owner: CartOwner,
    product_repo: ProductRepository,
) -> list[WishlistItemResponse]:
    """Pull the owner's wishlist + hydrate with product summaries.

    Lives outside the route bodies so list/add/delete all return a
    consistent post-mutation snapshot without duplicating the
    hydration code.
    """
    from src.infrastructure.database.connection import AsyncSessionLocal
    from src.infrastructure.repositories.wishlist_repository import (
        WishlistRepository,
    )

    async with AsyncSessionLocal() as session:
        repo = WishlistRepository(session)
        rows = await repo.list_for_owner(
            customer_id=owner.customer_id,
            session_id=str(owner.session_id) if owner.session_id else None,
            store_id=owner.store_id,
        )
    return await _attach_product_summary(rows, product_repo)
