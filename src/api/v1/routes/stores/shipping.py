"""Merchant shipping configuration routes.

Mounted under /stores/{store_id}/shipping.

All endpoints:
    * require store ownership (via `get_current_store`)
    * tenant-filter through the repository (RLS + explicit filter)
    * return `SuccessResponse[…]` with the data payload
"""

import logging
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, status
from pydantic import ValidationError

from src.api.dependencies.auth import get_current_store
from src.api.dependencies.repositories import get_shipping_zone_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.shipping import (
    CoverageResponse,
    CreateRateRequest,
    CreateZoneRequest,
    FreeShippingProgressResponse,
    PresetResponse,
    RateCalculatorRequest,
    RateResponse,
    ShippingOptionResponse,
    ShippingOptionsResponse,
    UpdateRateRequest,
    UpdateZoneRequest,
    ZoneResponse,
)
from src.application.services.shipping_resolver import ShippingResolver
from src.core.entities.shipping_rate import RateType, ShippingRate, parse_rate_config
from src.core.entities.shipping_zone import ShippingZone
from src.core.entities.store import Store
from src.core.value_objects.geography import (
    EGYPTIAN_GOVERNORATES,
    LogisticsZone,
    get_governorate_by_code,
)
from src.infrastructure.repositories.shipping_zone_repository import (
    ShippingZoneRepository,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/{store_id}/shipping")


# ─── Helpers ──────────────────────────────────────────────────────────


def _validate_governorate_codes(codes: list[str]) -> list[str]:
    """Raise 422 if any code isn't a known canonical governorate.

    Normalizes to the ISO-3166-2 code so the DB always stores that form
    (even if the caller submitted a legacy Bosta code).
    """
    normalized: list[str] = []
    unknown: list[str] = []
    seen: set[str] = set()
    for code in codes:
        gov = get_governorate_by_code(code)
        if gov is None:
            unknown.append(code)
            continue
        if gov.code in seen:
            continue  # de-dup
        seen.add(gov.code)
        normalized.append(gov.code)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown governorate codes: {', '.join(unknown)}",
        )
    return normalized


def _zone_to_response(zone: ShippingZone, rates: list[ShippingRate]) -> ZoneResponse:
    return ZoneResponse(
        id=zone.id,
        store_id=zone.store_id,
        name=zone.name,
        name_ar=zone.name_ar,
        governorate_codes=zone.governorate_codes,
        estimated_days_min=zone.estimated_days_min,
        estimated_days_max=zone.estimated_days_max,
        cod_enabled=zone.cod_enabled,
        cod_fee_cents=zone.cod_fee_cents,
        is_active=zone.is_active,
        sort_order=zone.sort_order,
        rates=[_rate_to_response(r) for r in rates],
    )


def _rate_to_response(rate: ShippingRate) -> RateResponse:
    return RateResponse(
        id=rate.id,
        zone_id=rate.zone_id,
        rate_type=rate.rate_type,
        label=rate.label,
        label_ar=rate.label_ar,
        config=rate.config,
        is_active=rate.is_active,
        sort_order=rate.sort_order,
    )


async def _hydrate_zone_response(
    repo: ShippingZoneRepository, zone: ShippingZone
) -> ZoneResponse:
    """Single-zone hydration (used by GET/POST/PATCH one-zone endpoints).

    For the multi-zone list, use `list_zones` which calls
    `get_zones_with_rates_for_store` to batch the rate load in one
    query (no N+1).
    """
    rates = await repo.list_rates_by_zone(zone.id, include_inactive=True)
    return _zone_to_response(zone, rates)


# ─── Zone CRUD ───────────────────────────────────────────────────────


@router.get(
    "/zones",
    response_model=SuccessResponse[list[ZoneResponse]],
    summary="List shipping zones",
    operation_id="list_shipping_zones",
)
async def list_zones(
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    # Single query pulls zones + their governorates + rates via selectin
    # loading — avoids N+1 against `shipping_rates` as zone count grows.
    pairs = await repo.get_zones_with_rates_for_store(store.id, include_inactive=True)
    data = [_zone_to_response(zone, rates) for zone, rates in pairs]
    return SuccessResponse(data=data)


@router.post(
    "/zones",
    response_model=SuccessResponse[ZoneResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a shipping zone",
    operation_id="create_shipping_zone",
)
async def create_zone(
    request: CreateZoneRequest,
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    codes = _validate_governorate_codes(request.governorate_codes)
    entity = ShippingZone(
        id=uuid4(),
        tenant_id=store.tenant_id,
        store_id=store.id,
        name=request.name,
        name_ar=request.name_ar,
        estimated_days_min=request.estimated_days_min,
        estimated_days_max=request.estimated_days_max,
        cod_enabled=request.cod_enabled,
        cod_fee_cents=request.cod_fee_cents,
        is_active=request.is_active,
        sort_order=request.sort_order,
        governorate_codes=codes,
    )
    try:
        created = await repo.create_zone(entity, codes)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc
    return SuccessResponse(data=_zone_to_response(created, []))


@router.get(
    "/zones/{zone_id}",
    response_model=SuccessResponse[ZoneResponse],
    summary="Get a shipping zone",
    operation_id="get_shipping_zone",
)
async def get_zone(
    zone_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    zone = await repo.get_zone(zone_id)
    if zone is None or zone.store_id != store.id:
        raise HTTPException(status_code=404, detail="Zone not found")
    return SuccessResponse(data=await _hydrate_zone_response(repo, zone))


@router.patch(
    "/zones/{zone_id}",
    response_model=SuccessResponse[ZoneResponse],
    summary="Update a shipping zone",
    operation_id="update_shipping_zone",
)
async def update_zone(
    zone_id: Annotated[UUID, Path()],
    request: UpdateZoneRequest,
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    zone = await repo.get_zone(zone_id)
    if zone is None or zone.store_id != store.id:
        raise HTTPException(status_code=404, detail="Zone not found")

    # Apply updates.
    if request.name is not None:
        zone.name = request.name
    if request.name_ar is not None:
        zone.name_ar = request.name_ar
    if request.estimated_days_min is not None:
        zone.estimated_days_min = request.estimated_days_min
    if request.estimated_days_max is not None:
        zone.estimated_days_max = request.estimated_days_max
    if request.cod_enabled is not None:
        zone.cod_enabled = request.cod_enabled
    if request.cod_fee_cents is not None:
        zone.cod_fee_cents = request.cod_fee_cents
    if request.is_active is not None:
        zone.is_active = request.is_active
    if request.sort_order is not None:
        zone.sort_order = request.sort_order

    codes: list[str] | None = None
    if request.governorate_codes is not None:
        codes = _validate_governorate_codes(request.governorate_codes)

    try:
        updated = await repo.update_zone(zone, governorate_codes=codes)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return SuccessResponse(data=await _hydrate_zone_response(repo, updated))


@router.delete(
    "/zones/{zone_id}",
    response_model=SuccessResponse[dict],
    summary="Deactivate (soft-delete) a shipping zone",
    operation_id="delete_shipping_zone",
)
async def delete_zone(
    zone_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    zone = await repo.get_zone(zone_id)
    if zone is None or zone.store_id != store.id:
        raise HTTPException(status_code=404, detail="Zone not found")
    deleted = await repo.delete_zone(zone_id)
    return SuccessResponse(data={"deleted": deleted})


# ─── Rate CRUD ───────────────────────────────────────────────────────


@router.post(
    "/zones/{zone_id}/rates",
    response_model=SuccessResponse[RateResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create a rate on a zone",
    operation_id="create_shipping_rate",
)
async def create_rate(
    zone_id: Annotated[UUID, Path()],
    request: CreateRateRequest,
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    zone = await repo.get_zone(zone_id)
    if zone is None or zone.store_id != store.id:
        raise HTTPException(status_code=404, detail="Zone not found")

    # `request.config` is already a typed union model; extract rate_type
    # from its discriminator and serialize the payload to JSONB shape.
    cfg_model = request.config
    rate_type = RateType(cfg_model.type)
    config_payload = cfg_model.model_dump(exclude={"type"})

    rate = ShippingRate(
        id=uuid4(),
        tenant_id=store.tenant_id,
        zone_id=zone_id,
        rate_type=rate_type,
        label=request.label,
        label_ar=request.label_ar,
        config=config_payload,
        is_active=request.is_active,
        sort_order=request.sort_order,
    )
    created = await repo.create_rate(rate)
    return SuccessResponse(data=_rate_to_response(created))


@router.patch(
    "/zones/{zone_id}/rates/{rate_id}",
    response_model=SuccessResponse[RateResponse],
    summary="Update a rate",
    operation_id="update_shipping_rate",
)
async def update_rate(
    zone_id: Annotated[UUID, Path()],
    rate_id: Annotated[UUID, Path()],
    request: UpdateRateRequest,
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    rate = await repo.get_rate(rate_id)
    if rate is None or rate.tenant_id != store.tenant_id or rate.zone_id != zone_id:
        raise HTTPException(status_code=404, detail="Rate not found")

    if request.label is not None:
        rate.label = request.label
    if request.label_ar is not None:
        rate.label_ar = request.label_ar
    if request.is_active is not None:
        rate.is_active = request.is_active
    if request.sort_order is not None:
        rate.sort_order = request.sort_order
    if request.config is not None:
        cfg_model = request.config
        rate.rate_type = RateType(cfg_model.type)
        rate.config = cfg_model.model_dump(exclude={"type"})

    updated = await repo.update_rate(rate)
    return SuccessResponse(data=_rate_to_response(updated))


@router.delete(
    "/zones/{zone_id}/rates/{rate_id}",
    response_model=SuccessResponse[dict],
    summary="Deactivate a rate",
    operation_id="delete_shipping_rate",
)
async def delete_rate(
    zone_id: Annotated[UUID, Path()],
    rate_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    rate = await repo.get_rate(rate_id)
    if rate is None or rate.tenant_id != store.tenant_id or rate.zone_id != zone_id:
        raise HTTPException(status_code=404, detail="Rate not found")
    deleted = await repo.delete_rate(rate_id)
    return SuccessResponse(data={"deleted": deleted})


# ─── Coverage ────────────────────────────────────────────────────────


@router.get(
    "/coverage",
    response_model=SuccessResponse[CoverageResponse],
    summary="Return covered/uncovered governorates for the store",
    operation_id="get_shipping_coverage",
)
async def get_coverage(
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    covered = await repo.get_covered_governorate_codes(store.id)
    all_codes = {g.code for g in EGYPTIAN_GOVERNORATES}
    uncovered = sorted(all_codes - covered)
    # Conflicts should never occur — repository's one-active-zone-per-
    # governorate invariant prevents them. Not computed on this path
    # today; if field reports show any, add a scan query here.
    return SuccessResponse(
        data=CoverageResponse(
            covered=sorted(covered),
            uncovered=uncovered,
            conflicts=[],
        )
    )


# ─── Preset: Egypt 4-zone ───────────────────────────────────────────


@router.post(
    "/preset/egypt-4-zone",
    response_model=SuccessResponse[PresetResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Apply the Quick Start: Egypt 4-zone preset",
    operation_id="apply_egypt_4_zone_preset",
)
async def apply_egypt_4_zone_preset(
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    """Create 4 zones with default rates covering all 27 governorates.

    Rejects if the store already has any active zones — merchants who
    want to re-apply the preset should first deactivate existing zones
    (or use the dashboard's "start over" action, which wires through
    the same endpoint after a bulk soft-delete).
    """
    existing = await repo.list_zones_by_store(store.id, include_inactive=False)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot apply preset: store already has active shipping zones. "
                "Deactivate existing zones first."
            ),
        )

    # Zone definitions mirror the design doc recommendations.
    # Greater Cairo bundles Cairo + Giza + Qalyubia (not strictly the
    # LogisticsZone.GREATER_CAIRO set, which is also those three).
    # We also split Delta from Canal-Sinai for ETA granularity.
    preset_zones: list[tuple[str, str, list[LogisticsZone], int, int, int, int]] = [
        # (name_en, name_ar, [zones to include], flat_rate_cents, eta_min, eta_max, sort_order)
        (
            "Greater Cairo",
            "القاهرة الكبرى",
            [LogisticsZone.GREATER_CAIRO],
            5000,
            1,
            2,
            1,
        ),
        (
            "Alexandria & Delta",
            "الإسكندرية والدلتا",
            [LogisticsZone.DELTA],
            6000,
            2,
            3,
            2,
        ),
        (
            "Canal, Sinai & Upper Egypt",
            "القناة وسيناء والصعيد",
            [LogisticsZone.CANAL_SINAI, LogisticsZone.UPPER_EGYPT],
            7000,
            3,
            5,
            3,
        ),
        (
            "Remote (Red Sea · Matrouh · New Valley)",
            "المناطق النائية",
            [LogisticsZone.REMOTE],
            9000,
            4,
            7,
            4,
        ),
    ]

    created_zone_ids: list[UUID] = []
    assigned_codes: list[str] = []

    for (
        name_en,
        name_ar,
        logistics_zones,
        rate_cents,
        eta_min,
        eta_max,
        sort,
    ) in preset_zones:
        codes = [g.code for g in EGYPTIAN_GOVERNORATES if g.zone in logistics_zones]
        zone_entity = ShippingZone(
            id=uuid4(),
            tenant_id=store.tenant_id,
            store_id=store.id,
            name=name_en,
            name_ar=name_ar,
            estimated_days_min=eta_min,
            estimated_days_max=eta_max,
            cod_enabled=True,
            cod_fee_cents=0,
            is_active=True,
            sort_order=sort,
            governorate_codes=codes,
        )
        created_zone = await repo.create_zone(zone_entity, codes)
        created_zone_ids.append(created_zone.id)
        assigned_codes.extend(codes)

        # One standard flat rate per zone.
        rate_entity = ShippingRate(
            id=uuid4(),
            tenant_id=store.tenant_id,
            zone_id=created_zone.id,
            rate_type=RateType.FLAT,
            label="Standard",
            label_ar="قياسي",
            config={"amount_cents": rate_cents},
            is_active=True,
            sort_order=1,
        )
        await repo.create_rate(rate_entity)

    return SuccessResponse(
        data=PresetResponse(
            created_zone_ids=created_zone_ids,
            assigned_governorate_codes=sorted(set(assigned_codes)),
        )
    )


# ─── Rate calculator preview ────────────────────────────────────────


@router.post(
    "/calculate",
    response_model=SuccessResponse[ShippingOptionsResponse],
    summary="Preview rates for a cart (merchant calculator tool)",
    operation_id="calculate_shipping_preview",
)
async def calculate_preview(
    request: RateCalculatorRequest,
    store: Annotated[Store, Depends(get_current_store)],
    repo: Annotated[ShippingZoneRepository, Depends(get_shipping_zone_repository)],
):
    """Same shape as storefront /shipping/options — shares the resolver."""
    resolver = ShippingResolver(repo, currency=store.currency or "EGP")
    # Normalize code (accept legacy Bosta codes like "CAI" → "EG-C").
    gov = get_governorate_by_code(request.governorate_code)
    if gov is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown governorate code: {request.governorate_code}",
        )
    try:
        result = await resolver.resolve_options(
            store_id=store.id,
            governorate_code=gov.code,
            cart_subtotal_cents=request.cart_subtotal_cents,
            cart_weight_g=request.cart_weight_g,
            cod_requested=request.cod_requested,
        )
    except ValidationError as exc:
        logger.warning("shipping_resolver_validation_failed", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail="A configured rate has an invalid config payload.",
        ) from exc

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


# Silence "unused import" — re-exported into the public reference endpoint.
__all__ = ["router", "parse_rate_config"]
