"""Public storefront product review routes.

URL: /storefront/store/{store_id}/products/{product_id}/reviews

- GET  → public, returns approved reviews + aggregate stats
- POST → customer-authenticated, creates a new (auto-approved) review
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from src.api.dependencies import (
    get_customer_repository,
    get_product_repository,
    get_product_review_repository,
    get_store_repository,
)
from src.api.dependencies.auth import get_current_customer_payload
from src.api.responses import SuccessResponse
from src.core.entities.product_review import ProductReview
from src.core.exceptions import EntityNotFoundError
from src.core.interfaces.services.token_service import CustomerTokenPayload
from src.infrastructure.repositories import (
    CustomerRepository,
    ProductRepository,
    ProductReviewRepository,
    StoreRepository,
)

router = APIRouter()


# ─── Schemas ─────────────────────────────────────────────────────────────────


class ReviewItem(BaseModel):
    id: str
    product_id: str
    reviewer_name: str
    rating: int
    title: str | None = None
    body: str | None = None
    helpful_count: int = 0
    created_at: str


class ReviewStatsResponse(BaseModel):
    average: float
    count: int
    distribution: dict[int, int] = Field(
        ..., description="Count of reviews at each star level (1..5)"
    )


class ReviewListResponse(BaseModel):
    items: list[ReviewItem]
    stats: ReviewStatsResponse


class CreateReviewRequest(BaseModel):
    rating: int = Field(..., ge=1, le=5)
    title: str | None = Field(None, max_length=200)
    body: str | None = Field(None, max_length=4000)


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _to_item(review: ProductReview) -> ReviewItem:
    return ReviewItem(
        id=str(review.id),
        product_id=str(review.product_id),
        reviewer_name=review.reviewer_name,
        rating=review.rating,
        title=review.title,
        body=review.body,
        helpful_count=review.helpful_count,
        created_at=review.created_at.isoformat()
        if isinstance(review.created_at, datetime)
        else str(review.created_at or ""),
    )


# ─── Routes ──────────────────────────────────────────────────────────────────


@router.get(
    "/{product_id}/reviews",
    response_model=SuccessResponse[ReviewListResponse],
    summary="List approved reviews for a product",
    operation_id="list_product_reviews",
)
async def list_product_reviews(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    review_repo: Annotated[
        ProductReviewRepository, Depends(get_product_review_repository)
    ],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
):
    """Return approved reviews for a product plus aggregate stats.

    Public endpoint — no auth needed. Rating distribution is included so
    the storefront can render the "5★: 65%" summary without another call.
    """
    # Sanity check: product exists and belongs to this store
    product = await product_repo.get_by_id(product_id)
    if not product or str(product.store_id) != str(store_id):
        raise EntityNotFoundError("Product", str(product_id))

    skip = (page - 1) * limit
    reviews = await review_repo.list_for_product(
        product_id=product_id, approved_only=True, skip=skip, limit=limit
    )
    stats = await review_repo.stats_for_product(
        product_id=product_id, approved_only=True
    )

    return SuccessResponse(
        data=ReviewListResponse(
            items=[_to_item(r) for r in reviews],
            stats=ReviewStatsResponse(
                average=round(stats.average, 2),
                count=stats.count,
                distribution=stats.distribution,
            ),
        ),
        message="Reviews retrieved successfully",
    )


@router.post(
    "/{product_id}/reviews",
    response_model=SuccessResponse[ReviewItem],
    status_code=status.HTTP_201_CREATED,
    summary="Post a review for a product",
    operation_id="create_product_review",
)
async def create_product_review(
    store_id: Annotated[UUID, Path(description="Store ID")],
    product_id: Annotated[UUID, Path(description="Product ID")],
    body_in: CreateReviewRequest,
    payload: Annotated[CustomerTokenPayload, Depends(get_current_customer_payload)],
    review_repo: Annotated[
        ProductReviewRepository, Depends(get_product_review_repository)
    ],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    customer_repo: Annotated[CustomerRepository, Depends(get_customer_repository)],
):
    """Create a review for a product.

    Requires an authenticated customer. Customers can review a given
    product at most once; a second attempt returns 409.
    """
    # Validate store
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))

    # Validate product belongs to store
    product = await product_repo.get_by_id(product_id)
    if not product or str(product.store_id) != str(store_id):
        raise EntityNotFoundError("Product", str(product_id))

    customer = await customer_repo.get_by_id(payload.customer_id)
    if not customer:
        raise HTTPException(status_code=404, detail="Customer not found")

    if await review_repo.customer_has_reviewed(
        product_id=product_id, customer_id=customer.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You've already reviewed this product",
        )

    reviewer_name = (customer.full_name or "").strip() or (
        customer.email.value
        if hasattr(customer.email, "value")
        else str(customer.email)
    )

    review = ProductReview(
        id=uuid4(),
        tenant_id=store.tenant_id or store_id,
        store_id=store_id,
        product_id=product_id,
        customer_id=customer.id,
        reviewer_name=reviewer_name,
        rating=body_in.rating,
        title=body_in.title,
        body=body_in.body,
        is_approved=True,
    )
    saved = await review_repo.create(review)

    return SuccessResponse(
        data=_to_item(saved),
        message="Review posted successfully",
    )
