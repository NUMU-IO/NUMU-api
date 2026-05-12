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
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.entities.store import Store
from src.infrastructure.external_services.bosta.shipping_service import (
    get_bosta_service_for_store,
)
from src.infrastructure.repositories import (
    OrderRepository,
    ShipmentRepository,
)

router = APIRouter(prefix="/{store_id}/shipments")


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
    carrier: str = "bosta",
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

    # Select carrier service
    if carrier == "mylerz":
        from src.infrastructure.external_services.mylerz import (
            get_mylerz_service_for_store,
        )

        shipping_service = await get_mylerz_service_for_store(store.settings or {})
    elif carrier == "jt":
        from src.infrastructure.external_services.jt import (
            get_jt_service_for_store,
        )

        shipping_service = await get_jt_service_for_store(store.settings or {})
    else:
        carrier = "bosta"  # Normalize to bosta as default
        shipping_service = await get_bosta_service_for_store(store.settings or {})

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
        cod_amount = order.total

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

    # Build tracking URL based on carrier
    tracking_url_map = {
        "bosta": f"https://bosta.co/tracking-shipment/?tracking_number={label.tracking_number}",
        "mylerz": f"https://mylerz.com/track/{label.tracking_number}",
        "jt": f"https://www.jtexpress-eg.com/trajectoryQuery?waybillNo={label.tracking_number}",
    }
    tracking_url = tracking_url_map.get(
        carrier,
        f"https://bosta.co/tracking-shipment/?tracking_number={label.tracking_number}",
    )

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
    """Cancel a shipment via Bosta API."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.is_terminal:
        raise HTTPException(
            status_code=409, detail="Shipment is already in a terminal state"
        )

    bosta_service = await get_bosta_service_for_store(store.settings or {})

    if shipment.tracking_number:
        cancelled = await bosta_service.cancel_shipment(shipment.tracking_number)
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

    bosta_service = await get_bosta_service_for_store(store.settings or {})

    return_tracking = await bosta_service.request_return(
        tracking_number=shipment.tracking_number,
        reason=reason,
    )
    if not return_tracking:
        raise HTTPException(status_code=400, detail="Failed to create return shipment")

    return_shipment = Shipment(
        store_id=store.id,
        tenant_id=store.tenant_id,
        order_id=shipment.order_id,
        carrier="bosta",
        tracking_number=return_tracking,
        tracking_url=f"https://bosta.co/tracking-shipment/?tracking_number={return_tracking}",
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
    """Get real-time tracking from Bosta API."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not shipment.tracking_number:
        raise HTTPException(status_code=400, detail="Shipment has no tracking number")

    bosta_service = await get_bosta_service_for_store(store.settings or {})
    tracking = await bosta_service.track_shipment("Bosta", shipment.tracking_number)

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
    """Update a delivery on Bosta (receiver info, COD, notes)."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if shipment.is_terminal:
        raise HTTPException(status_code=409, detail="Cannot update a terminal shipment")
    if not shipment.tracking_number:
        raise HTTPException(
            status_code=400, detail="Shipment has no tracking number to update"
        )

    bosta_service = await get_bosta_service_for_store(store.settings or {})

    receiver = None
    if any([receiver_phone, receiver_first_name, receiver_last_name]):
        receiver = {}
        if receiver_first_name:
            receiver["firstName"] = receiver_first_name
        if receiver_last_name:
            receiver["lastName"] = receiver_last_name
        if receiver_phone:
            receiver["phone"] = receiver_phone

    await bosta_service.update_delivery(
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
        message="Shipment updated on Bosta",
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

    bosta_service = await get_bosta_service_for_store(store.settings or {})
    pdf_bytes = await bosta_service.print_awb(shipment.tracking_number)

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
    """Fetch full delivery details directly from Bosta API."""
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")
    if not shipment.tracking_number:
        raise HTTPException(status_code=400, detail="Shipment has no tracking number")

    bosta_service = await get_bosta_service_for_store(store.settings or {})
    details = await bosta_service.get_delivery(shipment.tracking_number)

    return SuccessResponse(data=details, message="Bosta delivery details retrieved")


# ── Pickup Management ────────────────────────────────────────────


@router.get(
    "/pickups/locations",
    summary="Get business pickup locations",
    operation_id="get_pickup_locations",
)
async def get_pickup_locations(
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get available pickup locations configured in Bosta dashboard."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    locations = await bosta_service.get_pickup_locations()
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
):
    """Schedule a courier pickup from a business location."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})

    contact_person = None
    if any([contact_name, contact_phone, contact_email]):
        contact_person = {}
        if contact_name:
            contact_person["name"] = contact_name
        if contact_phone:
            contact_person["phone"] = contact_phone
        if contact_email:
            contact_person["email"] = contact_email

    pickup = await bosta_service.create_pickup(
        business_location_id=business_location_id,
        scheduled_date=scheduled_date,
        scheduled_time_slot=scheduled_time_slot,
        contact_person=contact_person,
        notes=notes,
    )
    return SuccessResponse(data=pickup, message="Pickup scheduled")


@router.get(
    "/pickups",
    summary="List pickups",
    operation_id="list_pickups",
)
async def list_pickups(
    store: Annotated[Store, Depends(get_current_store)],
    page: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
):
    """List all scheduled pickups from Bosta."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    result = await bosta_service.list_pickups(page=page, limit=limit)
    return SuccessResponse(data=result, message="Pickups retrieved")


@router.get(
    "/pickups/{pickup_id}",
    summary="Get pickup details",
    operation_id="get_pickup",
)
async def get_pickup(
    pickup_id: str,
    store: Annotated[Store, Depends(get_current_store)],
):
    """Get details of a specific pickup."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    pickup = await bosta_service.get_pickup(pickup_id)
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
):
    """Update a scheduled pickup."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})

    contact_person = None
    if any([contact_name, contact_phone]):
        contact_person = {}
        if contact_name:
            contact_person["name"] = contact_name
        if contact_phone:
            contact_person["phone"] = contact_phone

    result = await bosta_service.update_pickup(
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
):
    """Cancel/delete a scheduled pickup."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    deleted = await bosta_service.delete_pickup(pickup_id)
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
    """Get all cities available for Bosta delivery."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    cities = await bosta_service.get_cities()
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
    """Get delivery zones within a specific city."""
    bosta_service = await get_bosta_service_for_store(store.settings or {})
    zones = await bosta_service.get_city_zones(city_id)
    return SuccessResponse(data=zones, message="City zones retrieved")
