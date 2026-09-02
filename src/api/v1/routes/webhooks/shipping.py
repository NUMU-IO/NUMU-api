"""One webhook endpoint for every carrier.

``POST /webhooks/shipping/{carrier}``

``webhooks/{bosta,jt,mylerz}.py`` were three near-identical files. Only
three things actually differed per carrier — the field names in the body,
the signature header, and the status vocabulary. Everything else was the
same ~200 lines copied three times: find the shipment by tracking number,
derive the tenant from it, decrypt that store's webhook secret, verify
with a global fallback, apply the status, sync the order.

Copies drift, and these had. Only Bosta's handler mapped ``IN_WAREHOUSE``,
and each file decided independently which statuses were terminal.

The three carrier-specific pieces now live in the registry
(``status_map``, ``webhook_signature_header``, ``webhook_parser_loader``)
and this route supplies the rest. **A new carrier needs no new route
file** — a registry entry is enough.

The per-carrier paths still work and are unchanged; they are the URLs
already configured in each carrier's dashboard, and changing those is a
merchant-visible migration, not a refactor. Point new integrations here.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P1.5.
"""

import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies.database import get_admin_db_session
from src.application.services.carrier_credentials import load_credentials
from src.application.services.carrier_registry import CarrierSpec, get_spec
from src.application.services.shipment_status_sync import apply_carrier_status
from src.core.logging import get_logger

router = APIRouter()
logger = get_logger(__name__)


async def _verify(
    *,
    spec: CarrierSpec,
    session: AsyncSession,
    tenant_id: Any,
    raw_body: bytes,
    signature: str | None,
    log: Any,
) -> bool:
    """Verify a webhook signature: per-store secret, then global.

    Mirrors the precedence the per-carrier handlers used — each merchant
    has their own carrier account, so the store's own secret is tried
    first and the platform secret is the fallback.
    """
    if not signature:
        return False

    provider_cls = spec.provider_class()

    # Per-store secret.
    if tenant_id is not None:
        try:
            from src.infrastructure.repositories.store_repository import (
                StoreRepository,
            )

            store = await StoreRepository(session).get_by_id(tenant_id)
            if store:
                creds = await load_credentials(store.settings or {}, spec.slug)
                secret = (creds or {}).get("webhook_secret")
                if secret:
                    service = provider_cls(webhook_secret=secret)
                    if service.verify_webhook_signature(raw_body, signature):
                        return True
        except Exception as e:
            log.warning("webhook_store_secret_verify_failed", error=str(e))

    # Platform-level secret.
    try:
        if provider_cls().verify_webhook_signature(raw_body, signature):
            return True
    except Exception as e:
        log.warning("webhook_global_secret_verify_failed", error=str(e))

    return False


@router.post("/{carrier}", operation_id="carrier_shipping_webhook")
async def carrier_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_admin_db_session)],
    carrier: Annotated[str, Path(description="Carrier slug, e.g. bosta")],
):
    """Receive a delivery status update from any registered carrier.

    Always answers 200 with a body describing what happened. Carriers
    retry on non-2xx, and retrying a payload we cannot parse or a
    tracking number we do not have will never succeed — so those are
    reported, not failed. Genuine problems are surfaced in the logs and
    in ``status`` rather than as an HTTP error.
    """
    log = logger.bind(webhook="shipping", carrier=carrier)

    spec = get_spec(carrier)
    if spec is None or not spec.capabilities.supports_webhooks:
        log.warning("webhook_unknown_carrier")
        return {"status": "ignored", "reason": "unknown_carrier", "carrier": carrier}

    raw_body = await request.body()
    try:
        data = json.loads(raw_body)
    except (ValueError, TypeError):
        log.warning("webhook_unparseable_body")
        return {"status": "ignored", "reason": "invalid_json"}

    event = spec.parse_webhook(data)
    if event is None:
        log.warning("webhook_no_tracking_number")
        return {"status": "ignored", "reason": "no_tracking_number"}

    log = log.bind(
        tracking_number=event.tracking_number,
        raw_status=event.raw_status,
    )
    log.info("webhook_received")

    from src.infrastructure.repositories.order_repository import OrderRepository
    from src.infrastructure.repositories.shipment_repository import ShipmentRepository

    shipment_repo = ShipmentRepository(session)
    order_repo = OrderRepository(session)

    shipment = None
    order = None
    try:
        shipment = await shipment_repo.get_by_tracking_number_for_update(
            event.tracking_number
        )
    except Exception as e:
        log.warning("shipment_lookup_failed", error=str(e))
    try:
        order = await order_repo.get_by_tracking_number_for_update(
            event.tracking_number
        )
    except Exception as e:
        log.warning("order_lookup_failed", error=str(e))

    if shipment is None and order is None:
        # Not ours, or not ours yet. 200 so the carrier stops retrying.
        log.info("webhook_no_matching_shipment")
        return {"status": "ignored", "reason": "unknown_tracking_number"}

    tenant_id = getattr(shipment, "tenant_id", None) or getattr(
        order, "tenant_id", None
    )

    signature = (
        request.headers.get(spec.webhook_signature_header)
        if spec.webhook_signature_header
        else None
    )
    verified = await _verify(
        spec=spec,
        session=session,
        tenant_id=tenant_id,
        raw_body=raw_body,
        signature=signature,
        log=log,
    )
    if not verified:
        # Matches the per-carrier handlers: unverified traffic is recorded
        # and still processed, because most stores have no webhook secret
        # configured and rejecting them would silently stop all status
        # updates. Tightening this is a deliberate change, not a cleanup.
        log.warning("webhook_signature_unverified", has_signature=bool(signature))

    # 🔴 A carrier we don't have a mapping for must not be guessed at.
    if not event.is_mapped:
        log.warning("webhook_status_unmapped", raw_status=event.raw_status)
        return {
            "status": "ignored",
            "reason": "unmapped_status",
            "raw_status": event.raw_status,
        }

    applied = await apply_carrier_status(
        shipment=shipment,
        shipment_repo=shipment_repo,
        carrier=spec.slug,
        raw_status=event.raw_status,
        description=event.description,
        cod_amount=event.cod_amount,
    )

    if order is not None and applied is not None:
        try:
            order.tracking_url = spec.tracking_url(event.tracking_number)
            await order_repo.update(order)
        except Exception as e:
            log.warning("order_sync_failed", error=str(e))

    await session.commit()

    return {
        "status": "processed" if applied else "ignored",
        "carrier": spec.slug,
        "tracking_number": event.tracking_number,
        "shipment_status": applied.value if applied else None,
        "signature_verified": verified,
    }
