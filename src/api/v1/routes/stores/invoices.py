"""Invoice routes nested under stores.

URL: /stores/{store_id}/invoices

Provides endpoints for:
- Creating invoices from orders
- Listing store invoices
- Getting invoice details
- Submitting to ETA (Egyptian Tax Authority)
- Downloading invoice PDF
"""

import asyncio
import logging
from decimal import Decimal
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response

from src.api.dependencies import verify_store_ownership
from src.api.dependencies.repositories import (
    get_invoice_repository,
    get_order_repository,
)
from src.api.responses import SuccessResponse
from src.api.v1.schemas.public.common import PaginatedListResponse
from src.api.v1.schemas.tenant.invoice import (
    CreateInvoiceRequest,
    InvoiceListResponse,
    InvoiceResponse,
    SubmitInvoiceResponse,
    UpdateInvoiceRequest,
)
from src.config import settings
from src.core.entities.invoice import (
    BuyerInfo,
    Invoice,
    InvoiceStatus,
    SellerInfo,
)
from src.core.entities.order import Order, PaymentStatus
from src.core.entities.store import Store
from src.infrastructure.external_services.eta import ETAInvoiceService
from src.infrastructure.repositories.invoice_repository import InvoiceRepository
from src.infrastructure.repositories.order_repository import OrderRepository

router = APIRouter(prefix="/{store_id}/invoices")
logger = logging.getLogger(__name__)


def _format_currency(cents: int, currency: str = "EGP") -> str:
    """Format cents to currency string."""
    return f"{currency} {cents / 100:,.2f}"


# Map raw order.payment_method strings to display labels for the invoice
# stamp. Falls through to a Title Case form of the raw string when not
# listed.
_PAYMENT_METHOD_LABELS = {
    "cod": "Cash on Delivery",
    "cash_on_delivery": "Cash on Delivery",
    "paymob": "Paymob",
    "paymob_card": "Paymob (Card)",
    "paymob_wallet": "Paymob (Wallet)",
    "fawry": "Fawry",
    "stripe": "Stripe",
    "tap": "Tap",
    "instapay": "InstaPay",
    "bank_transfer": "Bank Transfer",
}


async def _resolve_payment_context(
    invoice: Invoice,
    order_repo: OrderRepository,
) -> dict | None:
    """Build payment context dict for the PDF generator.

    Returns ``None`` when the invoice isn't tied to an order (manual invoices)
    so the template skips the stamp entirely.
    """
    if not invoice.order_id:
        return None

    try:
        order = await order_repo.get_by_id(invoice.order_id)
    except Exception:
        logger.debug(
            "payment_context_lookup_failed",
            extra={"invoice_id": str(invoice.id), "order_id": str(invoice.order_id)},
        )
        return None

    if not order:
        return None

    raw_status = getattr(order, "payment_status", None)
    if raw_status is None:
        return None

    paid_at = getattr(order, "paid_at", None)
    paid_at_str = paid_at.strftime("%Y-%m-%d %H:%M") if paid_at else None

    raw_method = getattr(order, "payment_method", None) or getattr(
        order, "deposit_gateway", None
    )
    method_label = None
    if raw_method:
        key = str(raw_method).lower().strip()
        method_label = _PAYMENT_METHOD_LABELS.get(key, key.replace("_", " ").title())

    return {
        "status": raw_status,  # generator normalizes to a CSS-class key
        "method": method_label,
        "paid_at": paid_at_str,
    }


@router.post(
    "/",
    response_model=SuccessResponse[InvoiceResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Create new invoice",
    operation_id="create_invoice",
)
async def create_invoice(
    store: Annotated[Store, Depends(verify_store_ownership)],
    request: CreateInvoiceRequest,
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
):
    """Create a new invoice for the store.

    The invoice is created in DRAFT status. Line items are automatically
    calculated with 14% VAT. After review, submit to ETA using the
    submit endpoint.
    """
    seller = SellerInfo(
        tax_id=request.seller.tax_id,
        name=request.seller.name,
        name_ar=request.seller.name_ar,
        branch_id=request.seller.branch_id,
        governorate=request.seller.governorate,
        city=request.seller.city,
        street=request.seller.street,
        building_number=request.seller.building_number,
        activity_code=request.seller.activity_code,
    )

    buyer = BuyerInfo(
        buyer_type=request.buyer.buyer_type,
        tax_id=request.buyer.tax_id,
        national_id=request.buyer.national_id,
        name=request.buyer.name,
        name_ar=request.buyer.name_ar,
        governorate=request.buyer.governorate,
        city=request.buyer.city,
        street=request.buyer.street,
        building_number=request.buyer.building_number,
        phone=request.buyer.phone,
        email=request.buyer.email,
    )

    invoice_number = await invoice_repo.get_next_invoice_number(store.id)

    invoice = Invoice(
        id=uuid4(),
        store_id=store.id,
        order_id=request.order_id,
        customer_id=request.customer_id,
        invoice_number=invoice_number,
        invoice_type=request.invoice_type,
        status=InvoiceStatus.DRAFT,
        seller=seller,
        buyer=buyer,
        extra_discount=request.extra_discount,
        shipping_fee=request.shipping_fee,
        vat_rate=request.vat_rate,
        prices_include_vat=True,
        notes=request.notes,
        notes_ar=request.notes_ar,
        original_invoice_number=request.original_invoice_number,
    )

    for item in request.line_items:
        invoice.add_line_item(
            description=item.description,
            description_ar=item.description_ar,
            item_code=item.item_code,
            item_type=item.item_type,
            unit_type=item.unit_type,
            quantity=item.quantity,
            unit_price=item.unit_price,
            discount=item.discount,
            vat_rate=item.vat_rate,
            internal_code=item.internal_code,
        )

    created = await invoice_repo.create(invoice)
    response = _build_invoice_response(created)

    return SuccessResponse(
        data=response,
        message="Invoice created successfully",
    )


@router.get(
    "/",
    response_model=SuccessResponse[PaginatedListResponse[InvoiceListResponse]],
    summary="List store invoices",
    operation_id="list_invoices",
)
async def list_invoices(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    status_filter: Annotated[InvoiceStatus | None, Query(alias="status")] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """List all invoices for the store with optional filtering."""
    invoices, total = await invoice_repo.list_by_store(
        store.id, status_filter=status_filter, page=page, page_size=page_size
    )

    items = [
        InvoiceListResponse(
            id=inv.id,
            invoice_number=inv.invoice_number,
            invoice_type=inv.invoice_type,
            status=inv.status,
            date_issued=inv.date_issued,
            buyer_name=inv.buyer.name,
            currency=inv.currency,
            total=inv.total,
            total_formatted=_format_currency(inv.total, inv.currency),
            eta_uuid=inv.eta_uuid,
            order_id=inv.order_id,
            created_at=inv.created_at,
        )
        for inv in invoices
    ]

    return SuccessResponse(
        data=PaginatedListResponse(
            items=items,
            total=total,
            page=page,
            page_size=page_size,
            total_pages=(total + page_size - 1) // page_size,
        ),
    )


@router.get(
    "/{invoice_id}",
    response_model=SuccessResponse[InvoiceResponse],
    summary="Get invoice details",
    operation_id="get_invoice",
)
async def get_invoice(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    invoice_id: Annotated[UUID, Path(description="Invoice ID")],
):
    """Get detailed invoice information including line items and ETA status."""
    invoice = await invoice_repo.get_by_id(invoice_id)

    if not invoice or invoice.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    response = _build_invoice_response(invoice)
    return SuccessResponse(data=response)


@router.patch(
    "/{invoice_id}",
    response_model=SuccessResponse[InvoiceResponse],
    summary="Update draft invoice",
    operation_id="update_invoice",
)
async def update_invoice(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    invoice_id: Annotated[UUID, Path(description="Invoice ID")],
    request: UpdateInvoiceRequest,
):
    """Update a draft invoice. Only draft invoices can be modified."""
    invoice = await invoice_repo.get_by_id(invoice_id)

    if not invoice or invoice.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if not invoice.is_editable:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot edit invoice with status: {invoice.status.value}",
        )

    if request.buyer:
        invoice.buyer = BuyerInfo(
            buyer_type=request.buyer.buyer_type,
            tax_id=request.buyer.tax_id,
            national_id=request.buyer.national_id,
            name=request.buyer.name,
            name_ar=request.buyer.name_ar,
            governorate=request.buyer.governorate,
            city=request.buyer.city,
            street=request.buyer.street,
            building_number=request.buyer.building_number,
            phone=request.buyer.phone,
            email=request.buyer.email,
        )

    if request.line_items is not None:
        invoice.line_items = []
        for item in request.line_items:
            invoice.add_line_item(
                description=item.description,
                description_ar=item.description_ar,
                item_code=item.item_code,
                item_type=item.item_type,
                unit_type=item.unit_type,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount=item.discount,
                vat_rate=item.vat_rate,
                internal_code=item.internal_code,
            )

    if request.extra_discount is not None:
        invoice.extra_discount = request.extra_discount
        invoice.calculate_totals()

    if request.shipping_fee is not None:
        invoice.shipping_fee = request.shipping_fee
        invoice.calculate_totals()

    if request.notes is not None:
        invoice.notes = request.notes

    if request.notes_ar is not None:
        invoice.notes_ar = request.notes_ar

    invoice.touch()
    updated = await invoice_repo.update(invoice)

    response = _build_invoice_response(updated)
    return SuccessResponse(
        data=response,
        message="Invoice updated successfully",
    )


@router.post(
    "/{invoice_id}/submit",
    response_model=SuccessResponse[SubmitInvoiceResponse],
    summary="Submit invoice to ETA",
    operation_id="submit_invoice_to_eta",
)
async def submit_invoice_to_eta(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    invoice_id: Annotated[UUID, Path(description="Invoice ID")],
):
    """Submit invoice to Egyptian Tax Authority (ETA).

    The invoice must be in DRAFT status. After submission:
    - If accepted: status becomes ACCEPTED, ETA UUID is assigned
    - If rejected: status becomes REJECTED with error message
    """
    invoice = await invoice_repo.get_by_id(invoice_id)

    if not invoice or invoice.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status not in (InvoiceStatus.DRAFT, InvoiceStatus.REJECTED):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot submit invoice with status: {invoice.status.value}",
        )

    if not settings.eta_enabled:
        invoice.status = InvoiceStatus.ACCEPTED
        invoice.eta_uuid = f"simulated-{uuid4().hex[:12]}"
        invoice.eta_long_id = f"simulated-long-{uuid4().hex[:20]}"
        invoice.eta_status_code = "accepted"
        invoice.touch()
        await invoice_repo.update(invoice)

        return SuccessResponse(
            data=SubmitInvoiceResponse(
                success=True,
                invoice_id=invoice.id,
                invoice_number=invoice.invoice_number,
                status=invoice.status,
                eta_uuid=invoice.eta_uuid,
                eta_long_id=invoice.eta_long_id,
                eta_portal_url=invoice.eta_portal_url,
            ),
            message="Invoice accepted (ETA simulation mode)",
        )

    eta_service = ETAInvoiceService()
    r2_service = None
    try:
        from src.infrastructure.external_services.cloudflare_r2 import (
            CloudflareR2StorageService,
        )

        r2_service = CloudflareR2StorageService()
        if not r2_service.client:
            r2_service = None
    except Exception:
        pass

    updated_invoice = await eta_service.process_invoice_submission(
        invoice, storage_service=r2_service
    )
    await invoice_repo.update(updated_invoice)

    if updated_invoice.status == InvoiceStatus.ACCEPTED:
        return SuccessResponse(
            data=SubmitInvoiceResponse(
                success=True,
                invoice_id=updated_invoice.id,
                invoice_number=updated_invoice.invoice_number,
                status=updated_invoice.status,
                eta_uuid=updated_invoice.eta_uuid,
                eta_long_id=updated_invoice.eta_long_id,
                eta_portal_url=updated_invoice.eta_portal_url,
            ),
            message="Invoice submitted and accepted by ETA",
        )
    else:
        return SuccessResponse(
            data=SubmitInvoiceResponse(
                success=False,
                invoice_id=updated_invoice.id,
                invoice_number=updated_invoice.invoice_number,
                status=updated_invoice.status,
                error_message=updated_invoice.eta_status_message,
            ),
            message="Invoice submission failed",
        )


@router.delete(
    "/{invoice_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete draft invoice",
    operation_id="delete_invoice",
)
async def delete_invoice(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    invoice_id: Annotated[UUID, Path(description="Invoice ID")],
):
    """Delete a draft invoice. Only draft invoices can be deleted."""
    invoice = await invoice_repo.get_by_id(invoice_id)

    if not invoice or invoice.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    if invoice.status != InvoiceStatus.DRAFT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only draft invoices can be deleted",
        )

    await invoice_repo.delete(invoice_id)
    return None


@router.get(
    "/{invoice_id}/pdf",
    summary="Download invoice PDF",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "Invoice PDF file",
        }
    },
    operation_id="download_invoice_pdf",
)
async def download_invoice_pdf(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    invoice_id: Annotated[UUID, Path(description="Invoice ID")],
    regenerate: bool = Query(False, description="Force PDF regeneration"),
):
    """Download invoice as PDF.

    Generates a bilingual Arabic/English PDF using the invoice_ar template.
    The PDF is cached in Cloudflare R2 for subsequent downloads.
    Pass ``?regenerate=true`` to force re-generation after the invoice or
    its underlying order's payment_status changes.
    """
    invoice = await invoice_repo.get_by_id(invoice_id)

    if not invoice or invoice.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invoice not found",
        )

    # Try to serve cached PDF from R2
    if invoice.pdf_r2_key and not regenerate:
        try:
            from src.infrastructure.external_services.cloudflare_r2 import (
                CloudflareR2StorageService,
            )

            r2 = CloudflareR2StorageService()
            if r2.client and await r2.file_exists(invoice.pdf_r2_key):
                signed_url = await r2.get_signed_url(invoice.pdf_r2_key, expires_in=300)
                return Response(
                    status_code=307,
                    headers={"Location": signed_url},
                )
        except Exception:
            logger.debug("r2_cache_miss", extra={"invoice_id": str(invoice_id)})

    # Best-effort payment context from the linked order. None when the
    # invoice was created without an order_id (manual invoices).
    payment_ctx = await _resolve_payment_context(invoice, order_repo)

    # Generate PDF
    from src.infrastructure.external_services.invoice import InvoicePDFGenerator

    generator = InvoicePDFGenerator(
        template_name="invoice_ar.html",
        language="ar_en",
    )
    pdf_bytes = await asyncio.to_thread(generator.generate, invoice, payment_ctx)

    # Upload to R2 (non-blocking best-effort)
    try:
        from src.core.interfaces.services.storage_service import StorageBucket
        from src.infrastructure.external_services.cloudflare_r2 import (
            CloudflareR2StorageService,
        )

        r2 = CloudflareR2StorageService()
        if r2.client:
            uploaded = await r2.upload_file(
                file_content=pdf_bytes,
                filename=f"{invoice.invoice_number}.pdf",
                content_type="application/pdf",
                bucket=StorageBucket.DOCUMENTS,
            )
            invoice.pdf_r2_key = uploaded.key
            invoice.pdf_url = uploaded.url
            invoice.touch()
            await invoice_repo.update(invoice)
    except Exception:
        logger.warning(
            "pdf_r2_upload_failed",
            extra={"invoice_id": str(invoice_id)},
            exc_info=True,
        )

    safe_filename = invoice.invoice_number.replace("/", "-")
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{safe_filename}.pdf"',
            # Defeat browser + intermediate caches so a template / wording
            # change is visible on the next click without the merchant
            # having to clear cache or use incognito.
            "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


async def _build_invoice_from_order(
    store: Store,
    order: Order,
    invoice_repo: InvoiceRepository,
) -> Invoice:
    """Build + persist a VAT-inclusive invoice from a paid order.

    Mirrors the logic in ``invoice_on_paid_handler`` so a merchant can
    download an invoice on demand for orders whose event-bus handler
    never fired (legacy paid orders, dev-mode without the bus, or a
    transient handler failure). Idempotent: callers should look up an
    existing invoice first.
    """
    store_address = dict(store.address) if store.address else {}
    store_settings = dict(store.settings) if store.settings else {}
    ship = order.shipping_address

    seller = SellerInfo(
        tax_id=store_settings.get("tax_id", ""),
        name=store.name,
        name_ar=store_settings.get("name_ar", store.name),
        branch_id=store_settings.get("branch_id", "0"),
        country=store_address.get("country", "EG"),
        governorate=store_address.get("governorate", store_address.get("state", "")),
        city=store_address.get("city", ""),
        street=store_address.get("street", store_address.get("address_line1", "")),
        building_number=store_address.get("building_number", ""),
        activity_code=store_settings.get("activity_code", "4649"),
        phone=store_settings.get("phone") or getattr(store, "phone", None),
    )

    buyer_name = f"{ship.first_name or ''} {ship.last_name or ''}".strip() or "Customer"
    buyer = BuyerInfo(
        buyer_type="P",
        name=buyer_name,
        name_ar=buyer_name,
        city=ship.city or "",
        street=ship.address_line1 or "",
        phone=ship.phone or "",
    )

    invoice_number = await invoice_repo.get_next_invoice_number(store.id)
    invoice = Invoice(
        id=uuid4(),
        store_id=store.id,
        tenant_id=store.tenant_id,
        order_id=order.id,
        customer_id=order.customer_id,
        invoice_number=invoice_number,
        internal_id=order.order_number,
        status=InvoiceStatus.ACCEPTED,
        seller=seller,
        buyer=buyer,
        currency=order.currency,
        shipping_fee=order.shipping_cost or 0,
        prices_include_vat=True,
    )

    for li in order.line_items:
        invoice.add_line_item(
            description=li.product_name,
            description_ar=li.product_name,
            item_code=li.sku or "EG-0000-0000",
            quantity=Decimal(str(li.quantity)),
            unit_price=Decimal(str(li.unit_price)) / 100,
            internal_code=li.sku,
        )

    # ETA: simulated identifiers when not enabled, real submission otherwise.
    if settings.eta_enabled:
        try:
            invoice = await ETAInvoiceService().process_invoice_submission(invoice)
        except Exception as exc:
            logger.warning(
                "eta_submission_failed_on_lazy_create",
                extra={"order_id": str(order.id), "error": str(exc)},
            )
            invoice.status = InvoiceStatus.REJECTED
            invoice.eta_status_message = str(exc)[:500]
    else:
        invoice.eta_uuid = f"simulated-{uuid4().hex[:12]}"
        invoice.eta_long_id = f"simulated-long-{uuid4().hex[:20]}"
        invoice.eta_status_code = "accepted"

    return await invoice_repo.create(invoice)


@router.get(
    "/by-order/{order_id}",
    response_model=SuccessResponse[InvoiceResponse],
    summary="Get (or create) the invoice for an order",
    operation_id="get_invoice_for_order",
)
async def get_invoice_for_order(
    store: Annotated[Store, Depends(verify_store_ownership)],
    invoice_repo: Annotated[InvoiceRepository, Depends(get_invoice_repository)],
    order_repo: Annotated[OrderRepository, Depends(get_order_repository)],
    order_id: Annotated[UUID, Path(description="Order ID")],
    create_if_missing: bool = Query(
        True,
        description=(
            "When true (default), creates the invoice on-the-fly if the "
            "order is paid but no invoice exists yet (handles the race "
            "where mark-as-paid completes before the on-paid handler)."
        ),
    ),
):
    """Return the invoice tied to ``order_id`` for the store.

    Flow:
        1. Look up the order; 404 if not found or not in this store.
        2. Look up an existing invoice via ``get_by_order_id``.
        3. If missing AND ``create_if_missing`` AND the order is paid,
           build + persist a fresh VAT-inclusive invoice.
        4. If missing AND not paid, return 409 with a clear message
           (rather than the previous "No invoice found" which left the
           merchant guessing).
    """
    order = await order_repo.get_by_id(order_id)
    if not order or order.store_id != store.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    invoice = await invoice_repo.get_by_order_id(order_id)

    if invoice is None and create_if_missing:
        if order.payment_status != PaymentStatus.PAID:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "Invoice is generated after the order is marked as paid. "
                    "Mark the order as paid first."
                ),
            )
        invoice = await _build_invoice_from_order(store, order, invoice_repo)
        logger.info(
            "invoice_lazy_created",
            extra={
                "order_id": str(order_id),
                "invoice_number": invoice.invoice_number,
            },
        )

    if invoice is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No invoice found for this order",
        )

    return SuccessResponse(data=_build_invoice_response(invoice))


def _build_invoice_response(invoice: Invoice) -> InvoiceResponse:
    """Build InvoiceResponse from Invoice entity."""
    from src.api.v1.schemas.tenant.invoice import (
        BuyerInfoResponse,
        InvoiceLineItemResponse,
        SellerInfoResponse,
        TaxLineResponse,
    )

    return InvoiceResponse(
        id=invoice.id,
        store_id=invoice.store_id,
        order_id=invoice.order_id,
        customer_id=invoice.customer_id,
        invoice_number=invoice.invoice_number,
        internal_id=invoice.internal_id,
        invoice_type=invoice.invoice_type,
        status=invoice.status,
        date_issued=invoice.date_issued,
        seller=SellerInfoResponse(
            tax_id=invoice.seller.tax_id,
            name=invoice.seller.name,
            name_ar=invoice.seller.name_ar,
            branch_id=invoice.seller.branch_id,
            governorate=invoice.seller.governorate,
            city=invoice.seller.city,
            street=invoice.seller.street,
            building_number=invoice.seller.building_number,
            activity_code=invoice.seller.activity_code,
        ),
        buyer=BuyerInfoResponse(
            buyer_type=invoice.buyer.buyer_type,
            tax_id=invoice.buyer.tax_id,
            national_id=invoice.buyer.national_id,
            name=invoice.buyer.name,
            name_ar=invoice.buyer.name_ar,
            governorate=invoice.buyer.governorate,
            city=invoice.buyer.city,
            street=invoice.buyer.street,
            building_number=invoice.buyer.building_number,
            phone=invoice.buyer.phone,
            email=invoice.buyer.email,
        ),
        currency=invoice.currency,
        line_items=[
            InvoiceLineItemResponse(
                description=item.description,
                description_ar=item.description_ar,
                item_type=item.item_type,
                item_code=item.item_code,
                unit_type=item.unit_type,
                quantity=item.quantity,
                unit_price=item.unit_price,
                discount=item.discount,
                sales_total=item.sales_total,
                net_total=item.net_total,
                taxes=[
                    TaxLineResponse(
                        tax_type=tax.tax_type.value,
                        amount=tax.amount,
                        rate=tax.rate,
                        sub_type=tax.sub_type,
                    )
                    for tax in item.taxes
                ],
                total=item.total,
                internal_code=item.internal_code,
            )
            for item in invoice.line_items
        ],
        prices_include_vat=invoice.prices_include_vat,
        vat_rate=invoice.vat_rate,
        subtotal=invoice.subtotal,
        vat_amount=invoice.vat_amount,
        net_amount_before_vat=invoice.net_amount_before_vat,
        total_discount=invoice.total_discount,
        total_taxes=invoice.total_taxes,
        extra_discount=invoice.extra_discount,
        shipping_fee=invoice.shipping_fee,
        grand_total=invoice.grand_total,
        total=invoice.total,
        subtotal_formatted=_format_currency(invoice.subtotal, invoice.currency),
        vat_amount_formatted=_format_currency(invoice.vat_amount, invoice.currency),
        shipping_fee_formatted=_format_currency(invoice.shipping_fee, invoice.currency),
        grand_total_formatted=_format_currency(invoice.grand_total, invoice.currency),
        total_formatted=_format_currency(invoice.total, invoice.currency),
        eta_uuid=invoice.eta_uuid,
        eta_long_id=invoice.eta_long_id,
        eta_status_code=invoice.eta_status_code,
        eta_status_message=invoice.eta_status_message,
        eta_portal_url=invoice.eta_portal_url,
        qr_code_data=invoice.qr_code_data,
        qr_code_image=invoice.qr_code_image,
        pdf_url=invoice.pdf_url,
        notes=invoice.notes,
        notes_ar=invoice.notes_ar,
        created_at=invoice.created_at,
        updated_at=invoice.updated_at,
    )
