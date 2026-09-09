"""Apply a carrier status change to a shipment — one implementation.

``webhooks/{bosta,jt,mylerz}.py`` each carried a private status map and a
near-identical ``_update_shipment_status`` (~1,600 lines across the
three). The bodies had already drifted: only Bosta's branch handled
``IN_WAREHOUSE``, and each file decided independently which raw statuses
counted as terminal.

Two things live here now:

* the **transition** — which ``Shipment`` mark_* method a status change
  should call, so COD collection, delivery attempts and timestamps are
  recorded the same way regardless of which carrier reported it;
* the **mapping**, which is delegated to the carrier registry, so a
  carrier's status vocabulary is declared once instead of three times.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P1.5.
"""

from typing import Any

from src.application.services.carrier_resolver import map_carrier_status
from src.core.entities.shipment import ShipmentStatus
from src.core.logging import get_logger

logger = get_logger(__name__)


async def apply_carrier_status(
    *,
    shipment: Any,
    shipment_repo: Any,
    carrier: str,
    raw_status: str,
    description: str = "",
    cod_amount: float | None = None,
    failure_reason: str = "",
    status: ShipmentStatus | None = None,
) -> ShipmentStatus | None:
    """Transition a shipment from a carrier-reported status.

    Returns the applied :class:`ShipmentStatus`, or None when nothing was
    applied (no shipment, or a status this carrier's registry entry does
    not map).

    An **unmapped status is logged and ignored, never guessed**. Coercing
    an unknown carrier string to IN_TRANSIT would silently invent
    progress the carrier never reported — and the log line is how we find
    out a carrier added a status we don't handle yet.

    ``status`` short-circuits that lookup for a caller that has already
    resolved the word through a different vocabulary. The CSV importer is
    the case that matters: a Tier 3 sheet is written by the merchant, not
    the carrier, so it says "delivered" or "تم التسليم" — and the manual
    carrier has an empty ``status_map`` precisely because it has no
    carrier vocabulary of its own.
    """
    if shipment is None:
        return None

    log = logger.bind(
        carrier=carrier,
        raw_status=raw_status,
        shipment_id=str(getattr(shipment, "id", "")),
    )

    new_status = status or map_carrier_status(carrier, raw_status)
    if new_status is None:
        log.warning("carrier_status_unmapped")
        return None

    normalized = (raw_status or "").strip().upper()
    text = description or f"{carrier} status: {raw_status}"

    # Use the entity's own transitions where they exist — they record COD
    # collection, delivery attempts and the timestamps that plain
    # update_status() does not.
    if new_status is ShipmentStatus.DELIVERED:
        shipment.mark_delivered(
            cod_collected=bool(cod_amount),
            cod_amount=cod_amount,
        )
    elif new_status is ShipmentStatus.PICKED_UP:
        shipment.mark_picked_up()
    elif new_status is ShipmentStatus.FAILED:
        shipment.mark_failed(failure_reason or text)
    elif new_status is ShipmentStatus.RETURNED:
        shipment.mark_returned()
    elif new_status is ShipmentStatus.CANCELLED:
        shipment.mark_cancelled(failure_reason or "Cancelled by carrier")
    elif new_status is ShipmentStatus.OUT_FOR_DELIVERY:
        shipment.update_status(ShipmentStatus.OUT_FOR_DELIVERY, "Out for delivery")
    else:
        shipment.update_status(new_status, text)

    await shipment_repo.update(shipment)
    log.info(
        "shipment_status_updated",
        new_status=new_status.value,
        normalized=normalized,
    )
    return new_status


__all__ = ["apply_carrier_status"]
