"""Manual-carrier paperwork: courier profiles, waybills and the CSV round-trip.

Everything a Tier 3 courier needs, since they supply none of it
themselves. A courier with no API means the merchant does the handover on
paper and in spreadsheets, so these are the endpoints that make that
workable rather than manual.

**Route placement matters here.** This router is registered *before* the
shipments router, because several of these paths are single-segment
literals (``/manifest``, ``/waybills``) that ``GET /{shipment_id}`` would
otherwise swallow — exactly how ``GET /pickups`` ended up unreachable and
422-ing on "pickups" as a UUID. ``test_no_shadowed_routes`` guards it.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.
"""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, UploadFile
from fastapi import File as FileParam
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from src.api.dependencies import get_current_store, get_order_repository
from src.api.dependencies.repositories import (
    get_shipment_repository,
    get_store_repository,
)
from src.api.responses import SuccessResponse
from src.application.services.manual_carrier_profiles import (
    ProfileValidationError,
    delete_profile,
    get_profile,
    list_profiles,
    upsert_profile,
)
from src.application.services.manual_carrier_seeds import get_seed, seed_catalog
from src.application.services.shipment_csv import (
    CsvFormatError,
    build_manifest_csv,
    parse_status_sheet,
    resolve_status,
)
from src.application.services.shipment_status_sync import apply_carrier_status
from src.core.entities.store import Store
from src.core.logging import get_logger
from src.infrastructure.external_services.waybill import (
    WaybillRenderError,
    build_context,
    generate_waybill_batch_pdf,
    generate_waybill_pdf,
    generate_waybill_sheet_pdf,
    render_html,
    render_sheet_html,
)
from src.infrastructure.external_services.waybill.generator import (
    TEMPLATE_DIR as WAYBILL_TEMPLATE_DIR,
)
from src.infrastructure.repositories import (
    OrderRepository,
    ShipmentRepository,
    StoreRepository,
)

router = APIRouter(prefix="/{store_id}/shipments")
logger = get_logger(__name__)

#: A single print job is a merchant's whole morning; a runaway one is a
#: timeout. Generous, but bounded.
MAX_LABELS_PER_JOB = 200


class ProfilePayload(BaseModel):
    """A merchant-defined courier."""

    name_en: str | None = None
    name_ar: str | None = None
    governorate_codes: list[str] = Field(default_factory=list)
    contact_phone: str | None = None
    contact_name: str | None = None
    cutoff_time: str | None = None
    tracking_url_template: str | None = None
    notes: str | None = None
    is_active: bool = True
    #: Set when starting from a seeded courier.
    seed_key: str | None = None


class WaybillRequest(BaseModel):
    shipment_ids: list[UUID] = Field(..., min_length=1)
    #: "roll" prints 1:1 for a thermal printer; "sheet" tiles 4-up on A4.
    format: str = "roll"


class ApplyStatusRequest(BaseModel):
    """Rows the merchant confirmed after seeing the preview."""

    rows: list[dict[str, Any]] = Field(..., min_length=1)


def _bad_profile(e: ProfileValidationError) -> HTTPException:
    return HTTPException(
        status_code=400,
        detail={
            "code": "INVALID_COURIER_PROFILE",
            "message_en": str(e),
            "message_ar": str(e),
        },
    )


# ── Courier profiles ─────────────────────────────────────────────────


@router.get(
    "/couriers/seeds",
    summary="Known Egyptian couriers to start from",
    operation_id="list_courier_seeds",
)
async def list_courier_seeds(store: Annotated[Store, Depends(get_current_store)]):
    """Couriers a merchant can pick instead of typing one from scratch.

    Each carries ``data_verified``. An unverified seed ships with full
    governorate coverage and the hub must show it as unconfirmed — it is
    a starting point, not a claim about how that courier operates.
    """
    _ = store
    return SuccessResponse(data=seed_catalog(), message="Courier seeds")


@router.get(
    "/couriers",
    summary="This store's courier profiles",
    operation_id="list_courier_profiles",
)
async def list_courier_profiles(store: Annotated[Store, Depends(get_current_store)]):
    return SuccessResponse(
        data=[p.to_dict() for p in list_profiles(store.settings)],
        message="Courier profiles",
    )


@router.post(
    "/couriers",
    summary="Add a courier",
    operation_id="create_courier_profile",
    status_code=201,
)
async def create_courier_profile(
    payload: ProfilePayload,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
):
    """Create a courier, optionally pre-filled from a seed."""
    values = payload.model_dump()
    if payload.seed_key:
        seed = get_seed(payload.seed_key)
        if seed is None:
            raise HTTPException(
                status_code=400,
                detail={
                    "code": "UNKNOWN_COURIER_SEED",
                    "message_en": f"No known courier '{payload.seed_key}'.",
                    "message_ar": f"مفيش شركة معروفة اسمها '{payload.seed_key}'.",
                },
            )
        # Merchant-supplied values win over the seed's defaults.
        values = {**seed.to_profile_values(), **{k: v for k, v in values.items() if v}}

    try:
        settings, profile = upsert_profile(store.settings, values)
    except ProfileValidationError as e:
        raise _bad_profile(e) from e

    store.settings = settings
    await store_repo.update(store)
    return SuccessResponse(data=profile.to_dict(), message="Courier added")


@router.patch(
    "/couriers/{profile_id}",
    summary="Update a courier",
    operation_id="update_courier_profile",
)
async def update_courier_profile(
    payload: ProfilePayload,
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    profile_id: Annotated[str, Path()],
):
    existing = get_profile(store.settings, profile_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Courier not found")

    # PATCH semantics: only what was sent changes.
    merged = {**existing.to_dict(), **payload.model_dump(exclude_unset=True)}
    try:
        settings, profile = upsert_profile(
            store.settings, merged, profile_id=profile_id
        )
    except ProfileValidationError as e:
        raise _bad_profile(e) from e

    store.settings = settings
    await store_repo.update(store)
    return SuccessResponse(data=profile.to_dict(), message="Courier updated")


@router.delete(
    "/couriers/{profile_id}",
    summary="Remove a courier",
    operation_id="delete_courier_profile",
)
async def delete_courier_profile(
    store: Annotated[Store, Depends(get_current_store)],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    profile_id: Annotated[str, Path()],
):
    """Remove a courier.

    Shipments already in transit keep their own carrier field, so this
    never orphans a parcel that is already out.
    """
    if get_profile(store.settings, profile_id) is None:
        raise HTTPException(status_code=404, detail="Courier not found")

    store.settings = delete_profile(store.settings, profile_id)
    await store_repo.update(store)
    return SuccessResponse(data={"id": profile_id}, message="Courier removed")


# ── Waybills ─────────────────────────────────────────────────────────


async def _label_context(
    shipment: Any, store: Store, order_repo: OrderRepository
) -> dict[str, Any]:
    """Everything the label needs, from the shipment and its order."""
    order = await order_repo.get_by_id(shipment.order_id)
    address = getattr(order, "shipping_address", None)

    parts = []
    if address:
        parts = [address.address_line1, address.address_line2, address.city]
    courier_name = ""
    for profile in list_profiles(store.settings):
        if (shipment.shipping_method or "").endswith(profile.id):
            courier_name = profile.name_ar or profile.name_en
            break

    return build_context(
        tracking_number=shipment.tracking_number or "",
        store_name=store.name,
        recipient_name=(
            f"{address.first_name} {address.last_name}".strip() if address else ""
        ),
        recipient_phone=getattr(address, "phone", None),
        address_line=" — ".join(p for p in parts if p),
        governorate=getattr(address, "state", None),
        cod_amount_cents=shipment.cod_amount or 0,
        currency=getattr(order, "currency", "EGP") or "EGP",
        order_number=getattr(order, "order_number", None),
        created_at=(
            shipment.created_at.strftime("%Y-%m-%d")
            if getattr(shipment, "created_at", None)
            else None
        ),
        courier_name=courier_name,
        # `OrderLineItem` has no `name` — this read `li.name` and 500'd
        # every waybill print. The variant matters on a label: two sizes of
        # the same product are indistinguishable to whoever packs the box.
        items=[
            {
                "name": " — ".join(p for p in (li.product_name, li.variant_name) if p),
                "quantity": li.quantity,
            }
            for li in (getattr(order, "line_items", None) or [])
        ],
    )


def _label_html_response(html: str) -> HTMLResponse:
    """The same label, rendered by the browser instead of WeasyPrint.

    WeasyPrint needs cairo/pango, which the Docker image has and a Windows
    dev box does not — the 503 below used to tell merchants to "use the
    HTML preview" when no such thing existed. It is also a real fallback:
    the stylesheet sizes the page at 100×150mm, so Ctrl+P gives the same
    label off any printer.

    The template links `label.css` relatively, which WeasyPrint resolves
    against the template directory. A browser would resolve it against the
    API host and 404 — an unstyled label, which is worse than none. So the
    stylesheet is inlined here and only here; the PDF path is untouched.
    """
    css = (WAYBILL_TEMPLATE_DIR / "label.css").read_text(encoding="utf-8")
    return HTMLResponse(
        content=html.replace(
            '<link rel="stylesheet" href="label.css">',
            f"<style>{css}</style>",
        )
    )


@router.post(
    "/waybills",
    summary="Print waybills for a batch of shipments",
    operation_id="print_waybills",
)
async def print_waybills(
    request: WaybillRequest,
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    as_html: bool = Query(
        False,
        alias="html",
        description="Return the printable HTML instead of a PDF.",
    ),
):
    """One PDF for the day's parcels.

    ``roll`` prints each label 1:1 on a thermal roll; ``sheet`` tiles the
    same label four-up on A4 for a merchant with an office printer. Same
    layout either way.
    """
    if len(request.shipment_ids) > MAX_LABELS_PER_JOB:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "TOO_MANY_LABELS",
                "message_en": f"Print at most {MAX_LABELS_PER_JOB} labels at once.",
                "message_ar": f"اطبع {MAX_LABELS_PER_JOB} بوليصة كحد أقصى في المرة.",
            },
        )

    contexts = []
    for shipment_id in request.shipment_ids:
        shipment = await shipment_repo.get_by_id(shipment_id)
        if not shipment or shipment.store_id != store.id:
            raise HTTPException(
                status_code=404, detail=f"Shipment {shipment_id} not found"
            )
        contexts.append(await _label_context(shipment, store, order_repo))

    if as_html:
        return _label_html_response(
            render_sheet_html(contexts)
            if request.format == "sheet"
            else render_html(contexts)
        )

    try:
        pdf = (
            generate_waybill_sheet_pdf(contexts)
            if request.format == "sheet"
            else generate_waybill_batch_pdf(contexts)
        )
    except WaybillRenderError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (f"inline; filename=waybills-{len(contexts)}.pdf")
        },
    )


@router.get(
    "/{shipment_id}/waybill",
    summary="Print one waybill",
    operation_id="print_waybill",
)
async def print_waybill(
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    shipment_id: Annotated[UUID, Path()],
    as_html: bool = Query(
        False,
        alias="html",
        description="Return the printable HTML instead of a PDF.",
    ),
):
    shipment = await shipment_repo.get_by_id(shipment_id)
    if not shipment or shipment.store_id != store.id:
        raise HTTPException(status_code=404, detail="Shipment not found")

    context = await _label_context(shipment, store, order_repo)
    if as_html:
        return _label_html_response(render_html([context]))

    try:
        pdf = generate_waybill_pdf(context)
    except WaybillRenderError as e:
        raise HTTPException(status_code=503, detail=str(e)) from e

    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f"inline; filename=waybill-{shipment.tracking_number}.pdf"
            )
        },
    )


# ── CSV round-trip ───────────────────────────────────────────────────


@router.get(
    "/manifest",
    summary="Export the pickup manifest as CSV",
    operation_id="export_shipment_manifest",
)
async def export_shipment_manifest(
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    status: str = Query("created", description="Shipment status to include"),
    limit: int = Query(500, ge=1, le=2000),
):
    """The sheet the merchant hands the courier.

    Written UTF-8 **with a BOM** so Excel opens Arabic correctly — the
    opposite of the rule for source files, and deliberate.
    """
    shipments = await shipment_repo.get_by_store(
        store_id=store.id, status=status, skip=0, limit=limit
    )

    rows = []
    for shipment in shipments:
        order = await order_repo.get_by_id(shipment.order_id)
        address = getattr(order, "shipping_address", None)
        rows.append({
            "tracking_number": shipment.tracking_number,
            "order_number": getattr(order, "order_number", None),
            "recipient_name": (
                f"{address.first_name} {address.last_name}".strip() if address else ""
            ),
            "recipient_phone": getattr(address, "phone", None),
            "address": " — ".join(
                p
                for p in (
                    [address.address_line1, address.address_line2, address.city]
                    if address
                    else []
                )
                if p
            ),
            "governorate": getattr(address, "state", None),
            "cod_amount": f"{(shipment.cod_amount or 0) / 100:.2f}",
            "currency": getattr(order, "currency", "EGP"),
            "notes": (shipment.metadata or {}).get("notes"),
        })

    csv_bytes = build_manifest_csv(rows)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=manifest-{stamp}.csv"},
    )


@router.post(
    "/status-import",
    summary="Preview a courier's status sheet",
    operation_id="preview_status_import",
)
async def preview_status_import(
    store: Annotated[Store, Depends(get_current_store)],
    file: Annotated[UploadFile, FileParam(...)],
):
    """Parse a courier's sheet and report what *would* change.

    **Applies nothing.** Every row comes back, including the ones that
    failed, with a reason and the line number Excel shows — a silent row
    count is how a merchant loses ten parcels without noticing.
    """
    _ = store
    data = await file.read()
    try:
        preview = parse_status_sheet(data)
    except CsvFormatError as e:
        raise HTTPException(
            status_code=400,
            detail={
                "code": "INVALID_STATUS_SHEET",
                "message_en": str(e),
                "message_ar": str(e),
            },
        ) from e

    return SuccessResponse(data=preview.as_dict(), message="Preview ready")


@router.post(
    "/status-import/apply",
    summary="Apply reviewed status rows",
    operation_id="apply_status_import",
)
async def apply_status_import(
    request: ApplyStatusRequest,
    store: Annotated[Store, Depends(get_current_store)],
    shipment_repo: Annotated[ShipmentRepository, Depends(get_shipment_repository)],
):
    """Apply the rows the merchant confirmed.

    Rows are re-resolved against this store's shipments rather than
    trusted from the request: the preview is advisory, and a tracking
    number that isn't ours must not move anything.
    """
    applied, skipped = [], []

    for row in request.rows:
        tracking = (row.get("tracking_number") or "").strip()
        raw_status = (row.get("raw_status") or row.get("status") or "").strip()
        if not tracking or not raw_status:
            skipped.append({"tracking_number": tracking, "reason": "incomplete row"})
            continue

        shipment = await shipment_repo.get_by_tracking_number(tracking)
        if not shipment or shipment.store_id != store.id:
            skipped.append({"tracking_number": tracking, "reason": "not this store"})
            continue

        # Resolve through the sheet's vocabulary, the same one the preview
        # showed the merchant — not the carrier's. A Tier 3 sheet is written
        # by the merchant ("delivered", "تم التسليم"), and `manual` has an
        # empty carrier status_map by design, so routing this through
        # `map_carrier_status` skipped every row as unmapped.
        resolved = resolve_status(raw_status)
        if resolved is None:
            skipped.append({
                "tracking_number": tracking,
                "reason": f"unmapped '{raw_status}'",
            })
            continue

        status = await apply_carrier_status(
            shipment=shipment,
            shipment_repo=shipment_repo,
            carrier=shipment.carrier,
            raw_status=raw_status,
            description=row.get("note") or "",
            cod_amount=row.get("cod_amount"),
            status=resolved,
        )
        if status is None:
            skipped.append({
                "tracking_number": tracking,
                "reason": f"unmapped '{raw_status}'",
            })
        else:
            applied.append({"tracking_number": tracking, "status": status.value})

    logger.info(
        "status_import_applied",
        store_id=str(store.id),
        applied=len(applied),
        skipped=len(skipped),
    )
    return SuccessResponse(
        data={"applied": applied, "skipped": skipped},
        message=f"{len(applied)} updated, {len(skipped)} skipped",
    )
