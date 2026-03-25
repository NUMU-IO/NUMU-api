"""Public storefront upsell endpoint.

URL: /storefront/store/{store_id}/upsells
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import get_product_repository, get_store_repository
from src.api.dependencies.repositories import get_upsell_rule_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.upsell import UpsellOfferResponse
from src.infrastructure.repositories import ProductRepository, StoreRepository
from src.infrastructure.repositories.upsell_rule_repository import (
    UpsellRuleRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/upsells",
    response_model=SuccessResponse[list[UpsellOfferResponse]],
    summary="Get matching upsell offers",
    operation_id="get_storefront_upsells",
)
async def get_upsell_offers(
    store_id: Annotated[UUID, Path(description="Store ID")],
    upsell_repo: Annotated[UpsellRuleRepository, Depends(get_upsell_rule_repository)],
    product_repo: Annotated[ProductRepository, Depends(get_product_repository)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    product_ids: str | None = Query(
        None, description="Comma-separated product UUIDs from the order"
    ),
    category_ids: str | None = Query(
        None, description="Comma-separated category UUIDs from order products"
    ),
    cart_value: int = Query(0, ge=0, description="Cart total in cents"),
    lang: str = Query("en", description="Language for headlines: en or ar"),
):
    """Get upsell offers matching the given order context.

    This is a public endpoint (no auth required) used on the order
    confirmation page to show post-purchase upsell offers.
    """
    # Verify store exists
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Store not found",
        )

    # Parse IDs
    parsed_product_ids: list[UUID] = []
    if product_ids:
        for pid in product_ids.split(","):
            pid = pid.strip()
            if pid:
                try:
                    parsed_product_ids.append(UUID(pid))
                except ValueError:
                    pass

    parsed_category_ids: list[UUID] = []
    if category_ids:
        for cid in category_ids.split(","):
            cid = cid.strip()
            if cid:
                try:
                    parsed_category_ids.append(UUID(cid))
                except ValueError:
                    pass

    # Find matching rules
    rules = await upsell_repo.get_matching_rules(
        store_id=store_id,
        product_ids=parsed_product_ids,
        category_ids=parsed_category_ids,
        cart_value=cart_value,
    )

    # Build offers with product details
    offers: list[UpsellOfferResponse] = []
    for rule in rules:
        # Fetch the offer product
        product = await product_repo.get_by_id(rule.offer_product_id)
        if not product:
            continue

        # Skip out-of-stock products
        if product.quantity <= 0:
            continue

        original_price = product.price.cents

        # Calculate discounted price
        if rule.discount_type == "percentage":
            discount_amount = (original_price * rule.discount_value) // 100
            discounted_price = original_price - discount_amount
        elif rule.discount_type == "fixed":
            discounted_price = max(0, original_price - rule.discount_value)
        else:
            discounted_price = original_price

        compare_at = (
            product.compare_at_price.cents if product.compare_at_price else None
        )

        # Pick locale
        headline = rule.headline_ar if lang == "ar" else rule.headline_en
        description = rule.description_ar if lang == "ar" else rule.description_en

        offers.append(
            UpsellOfferResponse(
                rule_id=str(rule.id),
                product={
                    "id": str(product.id),
                    "name": product.name,
                    "slug": product.slug,
                    "price": original_price,
                    "compare_at_price": compare_at,
                    "images": product.images or [],
                    "is_in_stock": product.quantity > 0,
                },
                discount_type=rule.discount_type,
                discount_value=rule.discount_value,
                discounted_price=discounted_price,
                original_price=original_price,
                headline=headline,
                description=description,
            )
        )

    return SuccessResponse(
        data=offers,
        message="Upsell offers retrieved successfully",
    )
