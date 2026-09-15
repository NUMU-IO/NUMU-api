"""J&T Express Egypt status push.

J&T posts a waybill's scan history form-encoded as ``bizContent`` and signs
it the way it signs requests: header ``digest`` = base64(md5(bizContent +
privateKey)). Set the track push URL on open.jtjms-eg.com to
``https://numueg.app/api/v1/webhooks/jt/callback``.

Unlike the generic shipping route this also moves the order (ship, deliver,
collect COD, return to origin, cancel). A push that fails verification is
refused; the 30-minute trace poll still catches the shipment up.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.carrier_credentials import load_credentials
from src.application.services.carrier_registry import get_spec
from src.application.services.funnel_emit_service import emit_order_delivered
from src.application.services.shipment_status_sync import apply_carrier_status
from src.core.entities.order import OrderStatus
from src.core.entities.shipment import ShipmentStatus
from src.core.logging import get_logger
from src.infrastructure.database.connection import get_admin_db_session
from src.infrastructure.external_services.jt import JTShippingService
from src.infrastructure.repositories.funnel_event_repository import (
    FunnelEventRepository,
)
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.shipment_repository import ShipmentRepository
from src.infrastructure.repositories.store_repository import StoreRepository
from src.infrastructure.tenancy.rls import narrow_to_tenant
from src.infrastructure.webhooks.carrier_parsers import decode_webhook_body

logger = get_logger(__name__)
router = APIRouter()

ACK = {"code": "1", "msg": "success", "data": "SUCCESS"}


async def _enqueue_purchase_events(session: AsyncSession, order: Any, log: Any) -> None:
    try:
        from src.application.services.meta_capi_purchase_dispatcher import (
            enqueue_meta_capi_purchase,
        )

        await enqueue_meta_capi_purchase(session, order)
    except Exception:
        log.warning("meta_capi_purchase_enqueue_failed", exc_info=True)
    try:
        from src.application.services.tiktok_capi_purchase_dispatcher import (
            enqueue_tiktok_capi_purchase,
        )

        await enqueue_tiktok_capi_purchase(session, order)
    except Exception:
        log.warning("tiktok_capi_purchase_enqueue_failed", exc_info=True)


async def _sync_order(
    session: AsyncSession,
    order: Any,
    order_repo: OrderRepository,
    shipment: Any,
    status: ShipmentStatus,
    tracking_number: str,
    reason: str,
    log: Any,
) -> None:
    from src.application.services.stock_service import try_restock_order

    if status is ShipmentStatus.PICKED_UP:
        if order.status == OrderStatus.CONFIRMED:
            order.start_processing()
        if order.status == OrderStatus.PROCESSING:
            order.ship(tracking_number=tracking_number)

    elif status is ShipmentStatus.DELIVERED:
        delivered_now = order.status == OrderStatus.SHIPPED
        if delivered_now:
            order.deliver()
        cod_collected = bool(shipment and shipment.cod_amount) and not order.is_paid
        if cod_collected:
            order.mark_as_paid(
                payment_id=f"cod-jt-{tracking_number}", payment_method="cod"
            )
            order.metadata["cod_amount"] = shipment.cod_amount / 100
            order.metadata["cod_collected_via"] = "jt_webhook"
            order.metadata["cod_tracking_number"] = tracking_number
        await order_repo.update(order)
        if cod_collected:
            await _enqueue_purchase_events(session, order, log)
        if delivered_now:
            await emit_order_delivered(
                order, FunnelEventRepository(session), order_repo
            )
        return

    elif status is ShipmentStatus.RETURNED:
        if order.status == OrderStatus.SHIPPED:
            order.return_to_origin(reason="Returned by carrier (J&T)")
        elif order.can_be_cancelled:
            order.cancel(reason="Returned by carrier (J&T)")
        await try_restock_order(session, order, reason="jt_returned")

    elif status is ShipmentStatus.FAILED:
        order.metadata.setdefault("delivery_failures", []).append({
            "reason": reason,
            "tracking_number": tracking_number,
            "timestamp": datetime.now(UTC).isoformat(),
        })

    elif status is ShipmentStatus.CANCELLED and order.can_be_cancelled:
        order.cancel(reason="Cancelled via J&T")
        await try_restock_order(session, order, reason="jt_cancelled")

    else:
        return

    await order_repo.update(order)


@router.post("/callback", operation_id="jt_callback")
async def jt_callback(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_admin_db_session)],
) -> dict[str, Any]:
    raw = await request.body()
    event = get_spec("jt").parse_webhook(decode_webhook_body(raw) or {})
    if event is None:
        logger.warning("jt_webhook_unreadable")
        return ACK

    log = logger.bind(
        webhook="jt", tracking_number=event.tracking_number, raw_status=event.raw_status
    )
    shipment_repo = ShipmentRepository(session)
    order_repo = OrderRepository(session)
    shipment = await shipment_repo.get_by_tracking_number_for_update(
        event.tracking_number
    )
    order = await order_repo.get_by_tracking_number_for_update(event.tracking_number)
    if shipment is None and order is None:
        log.info("jt_webhook_unknown_waybill")
        return ACK

    owner = shipment or order
    store = await StoreRepository(session).get_by_id(owner.store_id)
    creds = await load_credentials(store.settings if store else None, "jt") or {}
    signature = request.headers.get("digest", "")
    if not JTShippingService(**creds).verify_webhook_signature(raw, signature):
        log.warning("jt_webhook_signature_invalid", has_signature=bool(signature))
        return {"code": "0", "msg": "digest verification failed", "data": "FAIL"}

    status = event.status
    if status is None:
        log.warning("jt_webhook_status_unmapped")
        return ACK
    if shipment is not None and shipment.status == status:
        return ACK

    await narrow_to_tenant(session, owner.tenant_id)
    reason = event.description or event.raw_status

    if shipment is not None:
        if (
            status in (ShipmentStatus.FAILED, ShipmentStatus.RETURNED)
            and shipment.cod_amount
        ):
            shipment.metadata.update({
                "cod_rejected": True,
                "rejection_reason": reason,
                "rejection_timestamp": datetime.now(UTC).isoformat(),
            })
        if status is ShipmentStatus.DELIVERED and shipment.cod_amount:
            shipment.cod_collected = True
            shipment.cod_collected_at = datetime.now(UTC)
        await apply_carrier_status(
            shipment=shipment,
            shipment_repo=shipment_repo,
            carrier="jt",
            raw_status=event.raw_status,
            description=event.description,
            failure_reason=reason,
            status=status,
        )

    if order is not None:
        try:
            await _sync_order(
                session,
                order,
                order_repo,
                shipment,
                status,
                event.tracking_number,
                reason,
                log,
            )
        except Exception as e:
            log.error("jt_webhook_order_sync_failed", error=str(e))

    await session.commit()
    log.info("jt_webhook_processed", status=status.value)
    return ACK
