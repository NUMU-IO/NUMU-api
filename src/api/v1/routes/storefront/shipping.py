"""Storefront shipping endpoints.

No auth — called from the checkout page. Paths:
    * GET  /storefront/store/{store_id}/shipping/governorates
    * POST /storefront/store/{store_id}/shipping/options

The legacy `/shipping/quote` endpoint in `shipping_quote.py` is kept
untouched for back-compat with the merchant rate calculator.
"""

import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from src.api.dependencies.repositories import get_shipping_zone_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.shipping import (
    FreeShippingProgressResponse,
    ShippingOptionResponse,
    ShippingOptionsRequest,
    ShippingOptionsResponse,
    StorefrontGovernorateResponse,
)
from src.application.services.shipping_resolver import ShippingResolver
from src.core.value_objects.geography import (
    EGYPTIAN_GOVERNORATES,
    resolve_governorate,
)
from src.infrastructure.repositories.shipping_zone_repository import (
    ShippingZoneRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/shipping/governorates",
    response_model=SuccessResponse[list[StorefrontGovernorateResponse]],
    summary="List governorates this store ships to",
    operation_id="storefront_list_shipping_governorates",
)
async def list_covered_governorates(
    store_id: Annotated[UUID, Path()],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
    locale: Annotated[str, Query(description="'en' or 'ar'")] = "en",
):
    """Return only governorates covered by an active zone.

    Used by the storefront checkout page to populate the governorate
    dropdown. Governorates the merchant doesn't ship to are simply
    absent from the list.

    If the store hasn't configured any zones yet, returns the full
    canonical set — this preserves pre-configuration behaviour (the old
    hardcoded 8-city dropdown was effectively the same "ships
    everywhere" promise) without crashing checkout on fresh stores.
    """
    covered = await repo.get_covered_governorate_codes(store_id)
    source = (
        [g for g in EGYPTIAN_GOVERNORATES if g.code in covered]
        if covered
        else list(EGYPTIAN_GOVERNORATES)
    )
    use_ar = (locale or "en").lower().startswith("ar")
    data = [
        StorefrontGovernorateResponse(
            code=g.code,
            name=g.name_ar if use_ar else g.name_en,
        )
        for g in source
    ]
    # Sort by display name for a predictable dropdown.
    data.sort(key=lambda g: g.name)
    return SuccessResponse(data=data)


@router.post(
    "/shipping/options",
    response_model=SuccessResponse[ShippingOptionsResponse],
    summary="Get priced shipping options for a cart + destination",
    operation_id="storefront_shipping_options",
)
async def get_shipping_options(
    store_id: Annotated[UUID, Path()],
    request: ShippingOptionsRequest,
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    """Fire on governorate change; re-fire on cart mutation.

    Returns the list of rates the resolver produced plus a
    free-shipping progress nudge when a `free_over` rate exists for the
    destination zone. The client should render as radio options and
    disable the payment step's COD choice if no option supports COD.
    """
    # Accept either an ISO 3166-2 code ("EG-DK"), a legacy Bosta code
    # ("DKH"), the governorate's English/Arabic name, or a common
    # city/capital alias ("Mansoura" → Dakahlia). Saved customer
    # addresses from before the ISO-code rollout still carry city
    # names, and the checkout form hydrates `form.city` from those.
    gov = resolve_governorate(request.governorate_code)
    if gov is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unknown governorate: {request.governorate_code}. "  # nosec B608
                "Please select a governorate from the dropdown."
            ),
        )

    resolver = ShippingResolver(repo, currency="EGP")
    result = await resolver.resolve_options(
        store_id=store_id,
        governorate_code=gov.code,
        cart_subtotal_cents=request.cart_subtotal_cents,
        cart_weight_g=request.cart_weight_g,
        cod_requested=request.cod_requested,
    )

    return SuccessResponse(
        data=ShippingOptionsResponse(
            options=[
                ShippingOptionResponse(
                    rate_id=o.rate_id,
                    label=o.label,
                    label_ar=o.label_ar,
                    amount_cents=o.amount_cents,
                    currency=o.currency,
                    estimated_days_min=o.estimated_days_min,
                    estimated_days_max=o.estimated_days_max,
                    cod_supported=o.cod_supported,
                    rate_type=o.rate_type,
                )
                for o in result.options
            ],
            free_shipping_progress=(
                FreeShippingProgressResponse(
                    current_cents=result.free_shipping_progress.current_cents,
                    threshold_cents=result.free_shipping_progress.threshold_cents,
                    remaining_cents=result.free_shipping_progress.remaining_cents,
                    qualified=result.free_shipping_progress.qualified,
                )
                if result.free_shipping_progress
                else None
            ),
        )
    )
