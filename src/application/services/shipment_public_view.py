"""The shopper-safe view of a shipment's journey.

The order-tracking endpoints already show order milestones — placed,
paid, shipped, delivered. What they never showed is the **parcel's own**
progress, because they never read the shipment at all. A customer could
see "shipped" and a tracking number, and nothing else.

That gap is worst for a manual (Tier 3) courier. NUMU issues those
tracking numbers itself and there is no carrier website to send anyone
to, so the number was a dead end.

**Why this is a separate module, and deliberately narrow.**
``Shipment.status_history`` is internal. Its ``description`` field
carries things like ``"Auto-create failed: <raw carrier error>"``,
``"Cancelled: <merchant's reason>"`` and ``"Synced from bosta API:
<raw status>"``. The tracking endpoints are public and one of them is
keyed on a short, guessable order number, so none of that can be
exposed.

So this maps each history entry to **a status and a timestamp only**,
with customer-facing bilingual copy written here. Nothing merchant- or
carrier-authored crosses the boundary.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P10.
"""

from datetime import datetime
from typing import Any

from src.core.entities.shipment import ShipmentStatus

#: Customer-facing copy per status. Written here, not derived from the
#: internal description, so nothing merchant- or carrier-authored reaches
#: a public endpoint. Arabic is Egyptian colloquial per DESIGN.md.
STATUS_COPY: dict[ShipmentStatus, dict[str, str]] = {
    ShipmentStatus.PENDING: {
        "en": "Preparing your order",
        "ar": "بنجهّز طلبك",
    },
    ShipmentStatus.CREATED: {
        "en": "Ready to ship",
        "ar": "جاهز للشحن",
    },
    ShipmentStatus.PICKED_UP: {
        "en": "Picked up by the courier",
        "ar": "المندوب استلم الشحنة",
    },
    ShipmentStatus.IN_TRANSIT: {
        "en": "On its way",
        "ar": "في الطريق إليك",
    },
    ShipmentStatus.OUT_FOR_DELIVERY: {
        "en": "Out for delivery today",
        "ar": "خارج للتسليم النهارده",
    },
    ShipmentStatus.DELIVERED: {
        "en": "Delivered",
        "ar": "تم التسليم",
    },
    ShipmentStatus.RETURNED: {
        "en": "Returned to the store",
        "ar": "رجعت للمتجر",
    },
    ShipmentStatus.CANCELLED: {
        "en": "Cancelled",
        "ar": "اتلغت",
    },
    # Deliberately not "failed". A customer reading "failed" thinks the
    # order is lost; the courier will normally try again, and the honest
    # thing is to say a delivery attempt did not succeed.
    ShipmentStatus.FAILED: {
        "en": "A delivery attempt didn't succeed",
        "ar": "محاولة توصيل ما نجحتش",
    },
}

#: Statuses a shopper should never be shown as a step of their own.
#: Nothing here today, but the filter exists so adding an internal-only
#: status later does not silently leak it.
_INTERNAL_ONLY: set[ShipmentStatus] = set()


def _coerce_status(raw: Any) -> ShipmentStatus | None:
    if isinstance(raw, ShipmentStatus):
        return raw
    try:
        return ShipmentStatus(str(raw).strip().lower())
    except (ValueError, AttributeError):
        return None


def _coerce_time(raw: Any) -> datetime | None:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def public_events(shipment: Any) -> list[dict[str, Any]]:
    """The parcel's journey, safe to show a customer.

    Status and timestamp only — never the internal description. Entries
    that cannot be understood are dropped rather than guessed at, and a
    repeated status collapses so a carrier re-sending "in transit" three
    times does not read as three separate movements.
    """
    history = getattr(shipment, "status_history", None) or []
    events: list[dict[str, Any]] = []
    last_status: ShipmentStatus | None = None

    for entry in history:
        if not isinstance(entry, dict):
            continue
        status = _coerce_status(entry.get("to"))
        if status is None or status in _INTERNAL_ONLY:
            continue
        if status == last_status:
            continue  # same step reported twice
        copy = STATUS_COPY.get(status)
        if copy is None:
            continue
        events.append({
            "status": status.value,
            "label_en": copy["en"],
            "label_ar": copy["ar"],
            "occurred_at": _coerce_time(entry.get("timestamp")),
        })
        last_status = status

    return events


def public_shipment(
    shipment: Any, *, tracking_url: str | None = None
) -> dict[str, Any]:
    """A shipment as a customer may see it.

    Carries no address, no phone, no COD amount and no internal notes —
    the tracking endpoints are public, and one is keyed on a guessable
    order number.
    """
    status = _coerce_status(getattr(shipment, "status", None))
    copy = STATUS_COPY.get(status) if status else None

    return {
        "carrier": getattr(shipment, "carrier", None),
        "tracking_number": getattr(shipment, "tracking_number", None),
        # None for a manual courier: NUMU issued the number and there is
        # no carrier site to send anyone to. This page is where it lives.
        "tracking_url": tracking_url,
        "status": status.value if status else None,
        "status_label_en": copy["en"] if copy else None,
        "status_label_ar": copy["ar"] if copy else None,
        "delivered_at": getattr(shipment, "delivered_at", None),
        "events": public_events(shipment),
    }


__all__ = ["STATUS_COPY", "public_events", "public_shipment"]
