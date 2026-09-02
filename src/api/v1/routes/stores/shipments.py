"""Shipment management routes nested under stores.

URL: /stores/{store_id}/shipments
"""

import asyncio
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from src.api.dependencies import (
    get_current_store,
    get_order_repository,
)
from src.api.dependencies.repositories import get_shipment_repository
from src.api.responses import SuccessResponse
from src.api.v1.schemas.tenant.shipment import (
    BulkCreateShipmentRequest,
    BulkShipmentResultItem,
    BulkShipmentResultResponse,
    CodSummaryResponse,
    CreateShipmentRequest,
    ShipmentListItemResponse,
    ShipmentResponse,
    ShipmentStatsResponse,
)
from src.application.services.carrier_resolver import (
    DEFAULT_CARRIER,
    CarrierCapabilityError,
    UnknownCarrierError,
    capability,
    carrier_catalog,
    service_for_carrier,
    service_for_shipment,
    tracking_url_for,
)
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.entities.store import Store
from src.infrastructure.repositories import (
    OrderRepository,
    ShipmentRepository,
)

router = APIRouter(prefix="/{store_id}/shipments")


async def _resolve_for_shipment(shipment: Shipment, store: Store, operation: str):
    """Resolve the provider owning this shipment and assert it can do `operation`.

    Every carrier action dispatches on ``shipment.carrier``. Before this
    existed, all of them called Bosta unconditionally — cancelling a
    Mylerz shipment hit Bosta's API.
    """
    try:
        service = await service_for_shipment(shipment, store.settings or {})
        return service, capability(service, operation, shipment.carrier)
    except UnknownCarrierError as e:
        # A shipment already persisted with a carrier we can no longer
        # resolve — a data problem, not a client error.
        raise HTTPException(status_code=409, detail=e.as_detail()) from e
    except CarrierCapabilityError as e:
        raise HTTPException(status_code=501, detail=e.as_detail()) from e


async def _resolve_for_carrier(carrier: str, store: Store, operation: str):
    """Resolve a provider by explicit carrier slug and assert `operation`."""
    try:
        service = await service_for_carrier(carrier, store.settings or {})
        return service, capability(service, operation, carrier)
    except UnknownCarrierError as e:
        raise HTTPException(status_code=400, detail=e.as_detail()) from e
    except CarrierCapabilityError as e:
        raise HTTPException(status_code=501, detail=e.as_detail()) from e


def _shipment_to_response(s: Shipment) -> ShipmentResponse:
    return ShipmentResponse(
        id=s.id,
        store_id=s.store_id,
        order_id=s.order_id,
        carrier=s.carrier,
        carrier_shipment_id=s.carrier_shipment_id,
        tracking_number=s.tracking_number,
        tracking_url=s.tracking_url,
        awb_url=s.awb_url,
        status=s.status.value if isinstance(s.status, ShipmentStatus) else s.status,
        shipment_type=s.shipment_type,
        parent_shipment_id=s.parent_shipment_id,
        shipping_method=s.shipping_method,
        shipping_cost=s.shipping_cost,
        cod_amount=s.cod_amount,
        cod_collected=s.cod_collected,
        cod_collected_at=s.cod_collected_at,
        delivery_attempts=s.delivery_attempts,
        status_history=s.status_history,
        shipped_at=s.shipped_at,
        delivered_at=s.delivered_at,
        cancelled_at=s.cancelled_at,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


def _shipment_to_list_item(s: Shipment) -> ShipmentListItemResponse:
    return ShipmentListItemResponse(
        id=s.id,
        order_id=s.order_id,
        tracking_number=s.tracking_number,
        tracking_url=s.tracking_url,
        awb_url=s.awb_url,
        carrier=s.carrier,
        status=s.status.value if isinstance(s.status, ShipmentStatus) else s.status,
        shipment_type=s.shipment_type,
        shipping_method=s.shipping_method,
        cod_amount=s.cod_amount,
        cod_collected=s.cod_collected,
        delivery_attempts=s.delivery_attempts,
        created_at=s.created_at,
        shipped_at=s.shipped_at,
        delivered_at=s.delivered_at,
    )


async def _create_shipment_for_order(
    order_id: UUID,
    store: Store,
    order_repo: OrderRepository,
    shipment_repo: ShipmentRepository,
    carrier: str = DEFAULT_CARRIER,
    shipping_method: str = "standard",
    notes: str | None = None,
) -> Shipment:
    """Core logic for creating a shipment for an order via the selected carrier."""
    from src.core.interfaces.services.shipping_service import Parcel, ShippingAddress

    order = await order_repo.get_by_id(order_id)
    if not order:
        raise HTTPException(status_code=404, detail=f"Order {order_id} not found")
    if order.store_id != store.id:
        raise HTTPException(
            status_code=404, detail=f"Order {order_id} not found in this store"
        )

    # Check for existing shipment
    existing = await shipment_repo.get_by_order(order_id)
    active = [s for s in existing if not s.is_terminal and s.shipment_type == "forward"]
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"Order {order_id} already has an active shipment",
        )

    # Select carrier service.
    #
    # An unrecognised slug is a 400 — it must NEVER fall through to Bosta.
    # This used to read `carrier = "bosta"  # Normalize to bosta as default`,
    # so a typo'd carrier silently booked a real Bosta delivery.
    try:
        shipping_service = await service_for_carrier(carrier, store.settings or {})
    except UnknownCarrierError as e:
        raise HTTPException(status_code=400, detail=e.as_detail()) from e

    # Map order address to shipping address
    addr = order.shipping_address
    to_address = ShippingAddress(
        name=f"{addr.first_name} {addr.last_name}",
        street1=addr.address_line1,
        street2=addr.address_line2,
        city=addr.city,
        state=addr.state,
        country=addr.country or "Egypt",
        phone=addr.phone,
    )

    # Origin address from store settings or defaults
    from_address = ShippingAddress(
        name=store.name,
        street1="Store Address",
        city="Cairo",
        country="Egypt",
    )

    parcel = Parcel(length=30, width=20, height=15, weight=1.0)

    # COD amount for cash-on-delivery orders
    cod_amount = 0
    if order.payment_method and order.payment_method.lower() in (
        "cod",
        "cash_on_delivery",
    ):
        cod_amount = (
            order.collected_total if order.collected_total is not None else order.total
        )

    rate_id = f"{carrier}_{shipping_method}"
    label = await shipping_service.create_shipment(
        from_address=from_address,
        to_address=to_address,
        parcel=parcel,
        rate_id=rate_id,
        cod_amount=cod_amount if cod_amount > 0 else None,
        order_reference=order.order_number,
        notes=notes,
    )

    # Build tracking URL based on carrier. Returns None for a carrier we
    # have no tracking page for — never another carrier's URL.
    tracking_url = tracking_url_for(carrier, label.tracking_number)

    # Create shipment entity
    shipment = Shipment(
        store_id=store.id,
        tenant_id=store.tenant_id,
        order_id=order.id,
        carrier=carrier,
        carrier_shipment_id=label.tracking_number,
        tracking_number=label.tracking_number,
        tracking_url=tracking_url,
        awb_url=label.label_url,
        status=ShipmentStatus.CREATED,
        shipping_method=shipping_method,
        shipping_cost=order.shipping_cost,
        cod_amount=cod_amount,
        shipment_type="forward",
        status_history=[
            {
                "from": "pending",
                "to": "created",
                "description": f"Shipment created via {carrier} API",
                "timestamp": datetime.utcnow().isoformat(),
            }
        ],
    )

    created = await shipment_repo.create(shipment)

    # Update order tracking fields
    order.tracking_number = label.tracking_number
    order.tracking_url = tracking_url
    order.shipping_method = f"{carrier}_{shipping_method}"
    await order_repo.update(order)

    return created


# ── Endpoints ────────────────────────────────────────────────────────


@router.post(
    "/",
    response_model=SuccessResponse[ShipmentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create shipment",
    operation_id="create_shipment",
)
async def create_shipment(
    request: CreateShipmentRequest,
    store: Annotated[Store, Depends(get_current_store)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Create a shipment for an order via the selected carrier."""
    shipment = await _create_shipment_for_order(
        order_id=request.order_id,
        store=store,
        order_repo=order_repo,
        shipment_repo=shipment_repo,
        carrier=request.carrier,
        shipping_method=request.shipping_method,
        notes=request.notes,
    )
    return SuccessResponse(
        data=_shipment_to_response(shipment),
        message="Shipment created successfully",
    )


@router.post(
    "/bulk",
    response_model=SuccessResponse[BulkShipmentResultResponse],
    summary="Bulk create shipments",
    operation_id="bulk_create_shipments",
)
async def bulk_create_shipments(
    request: BulkCreateShipmentRequest,
    store: Annotated[Store, Depends(get_current_store)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Create shipments for multiple orders. Partial failures don't block success."""
    sem = asyncio.Semaphore(5)
    results: list[BulkShipmentResultItem] = []

    async def process_one(oid: UUID) -> BulkShipmentResultItem:
        async with sem:
            try:
                shipment = await _create_shipment_for_order(
                    order_id=oid,
                    store=store,
                    order_repo=order_repo,
                    shipment_repo=shipment_repo,
                )
                return BulkShipmentResultItem(
                    order_id=oid,
                    success=True,
                    tracking_number=shipment.tracking_number,
                    shipment_id=shipment.id,
                )
            except Exception as e:
                return BulkShipmentResultItem(
                    order_id=oid,
                    success=False,
                    error=str(e),
                )

    results = await asyncio.gather(*[process_one(oid) for oid in request.order_ids])
    succeeded = sum(1 for r in results if r.success)

    return SuccessResponse(
        data=BulkShipmentResultResponse(
            total=len(results),
            succeeded=succeeded,
            failed=len(results) - succeeded,
            results=list(results),
        ),
        message=f"{succeeded}/{len(results)} shipments created",
    )


@router.get(
    "/",
    response_model=SuccessResponse[list[ShipmentListItemResponse]],
    summary="List shipments",
    operation_id="list_shipments",
)
async def list_shipments(
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    status_filter: str | None = Query(None, alias="status"),
    carrier: str | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    has_cod: bool | None = Query(None),
    order_id: UUID | None = Query(None, description="Filter by order ID"),
    has_label: bool | None = Query(
        None, description="Filter by whether AWB / shipping label exists"
    ),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List shipments for the store with optional filters."""
    shipments = await shipment_repo.get_by_store(
        store_id=store.id,
        skip=skip,
        limit=limit,
        status=status_filter,
        carrier=carrier,
        date_from=date_from,
        date_to=date_to,
        has_cod=has_cod,
        order_id=order_id,
        has_label=has_label,
    )
    return SuccessResponse(
        data=[_shipment_to_list_item(s) for s in shipments],
        message="Shipments retrieved",
    )


@router.get(
    "/stats",
    response_model=SuccessResponse[ShipmentStatsResponse],
    summary="Shipment dashboard stats",
    operation_id="get_shipment_stats",
)
async def get_shipment_stats(
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Get aggregated shipment statistics for the dashboard."""
    stats = await shipment_repo.get_stats(store.id)
    return SuccessResponse(
        data=ShipmentStatsResponse(**stats),
        message="Shipment stats retrieved",
    )


@router.get(
    "/cod/summary",
    response_model=SuccessResponse[CodSummaryResponse],
    summary="COD reconciliation summary",
    operation_id="get_cod_summary",
)
async def get_cod_summary(
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
):
    """Get COD collection summary for reconciliation."""
    summary = await shipment_repo.get_cod_summary(
        store_id=store.id,
        date_from=date_from,
        date_to=date_to,
    )
    return SuccessResponse(
        data=CodSummaryResponse(**summary),
        message="COD summary retrieved",
    )


# ── Literal single-segment routes MUST be declared above `/{shipment_id}` ──
#
# FastAPI matches in declaration order, so a one-segment literal declared
# after `/{shipment_id}` is unreachable: the path parameter matches first
# and then 422s trying to parse the literal as a UUID.
#
# `GET /pickups` sat in the Pickup Management section below and was dead —
# every call returned 422 "invalid UUID: pickups". `POST /pickups` was fine
# only because no single-segment POST `/{shipment_id}` exists, and
# `/pickups/{id}`, `/pickups/locations` and `/bosta/cities` are all
# multi-segment, so they never collided.
#
# `test_no_shadowed_routes` in tests/unit/api/test_shipment_routes.py
# guards this for every route in this router — add new literals here.


@router.get(
    "/carriers",
    summary="List supported carriers and their capabilities",
    operation_id="list_shipment_carriers",
)
async def list_shipment_carriers(
    store: Annotated[Store, Depends(get_current_store)],
):
    """The carrier catalog: names, tier, capabilities, credential fields.

    Registry-backed — adding a carrier to ``CARRIERS`` makes it appear
    here, and in the hub, with no further code change. This is what lets
    the hub render the carrier list and generate connect-forms instead of
    hardcoding a panel per carrier (P3 consumes it).

    Also lets the hub disable the actions a carrier can't do rather than
    letting merchants find out by clicking and getting a 501: Mylerz and
    J&T implement 4 of the 20 operations Bosta does. Before P0 those
    clicks silently called Bosta instead of failing.

    Answers from the registry alone — no credentials, no network — so it
    works for a store that has connected nothing.

    Read-only and store-scoped for auth; the catalog itself is global.
    """
    _ = store
    return SuccessResponse(data=carrier_catalog(), message="Carriers retrieved")


@router.get(
    "/pickups",
    summary="List pickups",
    operation_id="list_pickups",
)
async def list_pickups(
    store: Annotated[Store, Depends(get_current_store)],
    page: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """List all scheduled pickups for a carrier."""
    _, list_all = await _resolve_for_carrier(carrier, store, "list_pickups")
    result = await list_all(page=page, limit=limit)
    return SuccessResponse(data=result, message="Pickups retrieved")


@router.get(
    "/{shipment_id}",
    response_model=SuccessResponse[ShipmentResponse],
    summary="Get shipment detail",
    operation_id="get_shipment",
)
async def get_shipment(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Get full shipment details including status history."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    return SuccessResponse(
        data=_shipment_to_response(shipment),
        message="Shipment retrieved",
    )


@router.post(
    "/{shipment_id}/cancel",
    response_model=SuccessResponse[ShipmentResponse],
    summary="Cancel shipment",
    operation_id="cancel_shipment",
)
async def cancel_shipment(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Cancel a shipment with the carrier that owns it."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.is_terminal:
        raise HTTPException(
            status_code=409, detail="Shipment is already in a terminal state"
        )

    if shipment.tracking_number:
        _, cancel = await _resolve_for_shipment(shipment, store, "cancel_shipment")
        cancelled = await cancel(shipment.tracking_number)
        if not cancelled:
            raise HTTPException(
                status_code=400, detail="Failed to cancel shipment with carrier"
            )

    shipment.mark_cancelled("Cancelled by merchant")
    updated = await shipment_repo.update(shipment)
    return SuccessResponse(
        data=_shipment_to_response(updated),
        message="Shipment cancelled",
    )


@router.post(
    "/{shipment_id}/return",
    response_model=SuccessResponse[ShipmentResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Request return shipment",
    operation_id="request_return_shipment",
)
async def request_return_shipment(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    reason: str = Query("Customer return"),
):
    """Request a return shipment, creating a child shipment."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")

    _, request_return = await _resolve_for_shipment(shipment, store, "request_return")

    return_tracking = await request_return(
        tracking_number=shipment.tracking_number,
        reason=reason,
    )
    if not return_tracking:
        raise HTTPException(status_code=400, detail="Failed to create return shipment")

    # The return leg belongs to the same carrier as the forward leg — it
    # used to be hardcoded to Bosta, mislabelling every non-Bosta return.
    return_shipment = Shipment(
        store_id=store.id,
        tenant_id=store.tenant_id,
        order_id=shipment.order_id,
        carrier=shipment.carrier,
        tracking_number=return_tracking,
        tracking_url=tracking_url_for(shipment.carrier, return_tracking),
        status=ShipmentStatus.CREATED,
        shipment_type="return",
        parent_shipment_id=shipment.id,
        status_history=[
            {
                "from": "pending",
                "to": "created",
                "description": f"Return shipment created. Reason: {reason}",
                "timestamp": datetime.utcnow().isoformat(),
            }
        ],
    )
    created = await shipment_repo.create(return_shipment)
    return SuccessResponse(
        data=_shipment_to_response(created),
        message="Return shipment created",
    )


@router.get(
    "/{shipment_id}/track",
    summary="Track shipment",
    operation_id="track_shipment_detail",
)
async def track_shipment_detail(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Get real-time tracking from the carrier that owns this shipment."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not shipment.tracking_number:
        raise HTTPException(status_code=400, detail="Shipment has no tracking number")

    _, track = await _resolve_for_shipment(shipment, store, "track_shipment")
    # Carrier arg was the literal "Bosta" regardless of the real carrier.
    tracking = await track(shipment.carrier, shipment.tracking_number)

    return SuccessResponse(
        data={
            "tracking_number": tracking.tracking_number,
            "status": tracking.status,
            "estimated_delivery": tracking.estimated_delivery.isoformat()
            if tracking.estimated_delivery
            else None,
            "events": [
                {
                    "status": event.status,
                    "description": event.description,
                    "location": event.location,
                    "timestamp": event.timestamp.isoformat(),
                }
                for event in tracking.events
            ],
        },
        message="Tracking info retrieved",
    )


# ── Update Delivery ──────────────────────────────────────────────


@router.patch(
    "/{shipment_id}",
    response_model=SuccessResponse[ShipmentResponse],
    summary="Update shipment on Bosta",
    operation_id="update_shipment",
)
async def update_shipment(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    notes: str | None = None,
    cod_amount: float | None = None,
    receiver_phone: str | None = None,
    receiver_first_name: str | None = None,
    receiver_last_name: str | None = None,
):
    """Update a delivery with the owning carrier (receiver info, COD, notes)."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.is_terminal:
        raise HTTPException(status_code=409, detail="Cannot update a terminal shipment")
    if not shipment.tracking_number:
        raise HTTPException(
            status_code=400, detail="Shipment has no tracking number to update"
        )

    # Only Bosta implements update_delivery today; others get a clean 501.
    _, update_delivery = await _resolve_for_shipment(shipment, store, "update_delivery")

    receiver = None
    if any([receiver_phone, receiver_first_name, receiver_last_name]):
        receiver = {}
        if receiver_first_name:
            receiver["firstName"] = receiver_first_name
        if receiver_last_name:
            receiver["lastName"] = receiver_last_name
        if receiver_phone:
            receiver["phone"] = receiver_phone

    await update_delivery(
        shipment.tracking_number,
        receiver=receiver,
        cod=cod_amount,
        notes=notes,
    )

    # Update local record
    if cod_amount is not None:
        shipment.cod_amount = int(cod_amount * 100)
    if notes:
        shipment.metadata["notes"] = notes
    shipment.touch()
    updated = await shipment_repo.update(shipment)

    return SuccessResponse(
        data=_shipment_to_response(updated),
        message=f"Shipment updated with {shipment.carrier}",
    )


@router.get(
    "/{shipment_id}/awb",
    summary="Print AWB (Air Waybill)",
    operation_id="print_shipment_awb",
)
async def print_shipment_awb(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Get the Air Waybill PDF for printing."""
    from fastapi.responses import Response

    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not shipment.tracking_number:
        raise HTTPException(status_code=400, detail="Shipment has no tracking number")

    _, print_awb = await _resolve_for_shipment(shipment, store, "print_awb")
    pdf_bytes = await print_awb(shipment.tracking_number)

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"inline; filename=AWB-{shipment.tracking_number}.pdf"
        },
    )


@router.get(
    "/{shipment_id}/bosta-details",
    summary="Get delivery details from Bosta",
    operation_id="get_bosta_delivery_details",
)
async def get_bosta_delivery_details(
    shipment_id: Annotated[UUID, Path()],
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Fetch full delivery details from the carrier that owns this shipment.

    Path stays ``/bosta-details`` for hub back-compat, but it now resolves
    the shipment's real carrier — asking Bosta about a Mylerz waybill
    returned nothing useful. Carriers without ``get_delivery`` return 501.
    P1/P3 rename this to a carrier-neutral path.
    """
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not shipment.tracking_number:
        raise HTTPException(status_code=400, detail="Shipment has no tracking number")

    _, get_delivery = await _resolve_for_shipment(shipment, store, "get_delivery")
    details = await get_delivery(shipment.tracking_number)

    return SuccessResponse(
        data=details, message=f"{shipment.carrier} delivery details retrieved"
    )


# ── Pickup Management ────────────────────────────────────────────


@router.get(
    "/pickups/locations",
    summary="Get business pickup locations",
    operation_id="get_pickup_locations",
)
async def get_pickup_locations(
    store: Annotated[Store, Depends(get_current_store)],
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """Get pickup locations configured in the carrier's dashboard.

    Pickup routes are store-scoped, so they take an explicit ``carrier``
    rather than inferring one. Default stays "bosta" for back-compat;
    carriers without pickup support return 501 instead of silently
    querying Bosta.
    """
    _, get_locations = await _resolve_for_carrier(
        carrier, store, "get_pickup_locations"
    )
    locations = await get_locations()
    return SuccessResponse(data=locations, message="Pickup locations retrieved")


@router.post(
    "/pickups",
    summary="Schedule a pickup",
    operation_id="create_pickup",
    status_code=status.HTTP_201_CREATED,
)
async def create_pickup(
    store: Annotated[Store, Depends(get_current_store)],
    business_location_id: str,
    scheduled_date: str,
    scheduled_time_slot: str,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    contact_email: str | None = None,
    notes: str | None = None,
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """Schedule a courier pickup from a business location."""
    _, create = await _resolve_for_carrier(carrier, store, "create_pickup")

    contact_person = None
    if any([contact_name, contact_phone, contact_email]):
        contact_person = {}
        if contact_name:
            contact_person["name"] = contact_name
        if contact_phone:
            contact_person["phone"] = contact_phone
        if contact_email:
            contact_person["email"] = contact_email

    pickup = await create(
        business_location_id=business_location_id,
        scheduled_date=scheduled_date,
        scheduled_time_slot=scheduled_time_slot,
        contact_person=contact_person,
        notes=notes,
    )
    return SuccessResponse(data=pickup, message="Pickup scheduled")


# NOTE: `GET /pickups` is NOT declared here — a single-segment literal
# would be shadowed by `GET /{shipment_id}` above. It lives with the other
# literal-prefix routes near the top of this file. See the comment there.


@router.get(
    "/pickups/{pickup_id}",
    summary="Get pickup details",
    operation_id="get_pickup",
)
async def get_pickup(
    pickup_id: str,
    store: Annotated[Store, Depends(get_current_store)],
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """Get details of a specific pickup."""
    _, get_one = await _resolve_for_carrier(carrier, store, "get_pickup")
    pickup = await get_one(pickup_id)
    return SuccessResponse(data=pickup, message="Pickup details retrieved")


@router.patch(
    "/pickups/{pickup_id}",
    summary="Update pickup",
    operation_id="update_pickup",
)
async def update_pickup(
    pickup_id: str,
    store: Annotated[Store, Depends(get_current_store)],
    scheduled_date: str | None = None,
    scheduled_time_slot: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
    notes: str | None = None,
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """Update a scheduled pickup."""
    _, update = await _resolve_for_carrier(carrier, store, "update_pickup")

    contact_person = None
    if any([contact_name, contact_phone]):
        contact_person = {}
        if contact_name:
            contact_person["name"] = contact_name
        if contact_phone:
            contact_person["phone"] = contact_phone

    result = await update(
        pickup_id,
        scheduled_date=scheduled_date,
        scheduled_time_slot=scheduled_time_slot,
        contact_person=contact_person,
        notes=notes,
    )
    return SuccessResponse(data=result, message="Pickup updated")


@router.delete(
    "/pickups/{pickup_id}",
    summary="Cancel pickup",
    operation_id="delete_pickup",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_pickup(
    pickup_id: str,
    store: Annotated[Store, Depends(get_current_store)],
    carrier: str = Query(DEFAULT_CARRIER, description="Carrier slug"),
):
    """Cancel/delete a scheduled pickup."""
    _, delete = await _resolve_for_carrier(carrier, store, "delete_pickup")
    deleted = await delete(pickup_id)
    if not deleted:
        raise HTTPException(status_code=400, detail="Failed to cancel pickup")
    return None


# ── Cities & Zones ───────────────────────────────────────────────


@router.get(
    "/bosta/cities",
    summary="Get Bosta cities",
    operation_id="get_bosta_cities",
)
async def get_bosta_cities(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get all cities available for Bosta delivery.

    Bosta-namespaced by URL contract — the hub's connection probe calls
    ``getBostaCities``. The carrier is passed explicitly rather than
    being an accidental default. P1/P3 generalise this to
    ``/carriers/{slug}/cities``.
    """
    _, get_cities = await _resolve_for_carrier("bosta", store, "get_cities")
    cities = await get_cities()
    return SuccessResponse(data=cities, message="Cities retrieved")


@router.get(
    "/bosta/cities/{city_id}/zones",
    summary="Get zones in a city",
    operation_id="get_bosta_city_zones",
)
async def get_bosta_city_zones(
    city_id: str,
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get delivery zones within a specific Bosta city.

    Bosta-namespaced by URL contract; see get_bosta_cities.
    """
    _, get_city_zones = await _resolve_for_carrier("bosta", store, "get_city_zones")
    zones = await get_city_zones(city_id)
    return SuccessResponse(data=zones, message="City zones retrieved")
