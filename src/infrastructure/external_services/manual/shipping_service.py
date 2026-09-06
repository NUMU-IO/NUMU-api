"""The manual carrier — Tier 3, for couriers with no API.

One adapter covers **every** courier that can't be integrated: البريد
المصري (whose وصّلها service is a web portal, not an API), Cathedis,
Sprint, MCS, R2S, Apex, Xceed, Door To Door, and the merchant's own
motorbike guy. That is the highest coverage-per-effort item in the whole
shipping plan.

NUMU supplies what the courier can't: a tracking number, a waybill, and a
status history. The courier supplies the delivery.

This is the **first provider to implement ``ShippingProvider``
directly**. It inherits honest failure for everything it can't do —
cancel, live rates, pickups, city lookup all raise
:class:`NotSupportedByCarrier`, which the routes turn into a 501 with a
bilingual reason rather than a silent no-op.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P2.
"""

import secrets
from datetime import UTC, datetime
from typing import Any

from src.core.interfaces.services.shipping_provider import (
    NotSupportedByCarrier,
    Parcel,
    ShipmentLabel,
    ShippingAddress,
    ShippingProvider,
    TrackingInfo,
)

#: Prefix on every NUMU-issued tracking number, so support can tell one
#: from a carrier's own at a glance.
TRACKING_PREFIX = "NM"

# Crockford base32 minus I, L, O, U: no character pairs a human can
# confuse when reading a number off a printed waybill over the phone,
# and no accidental words.
_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_BODY_LENGTH = 10


def generate_tracking_number() -> str:
    """A NUMU-issued waybill number.

    ~32^10 ≈ 10^15 values, drawn from a CSPRNG. It is printed on a label
    and read aloud, so it avoids look-alike characters rather than
    maximising density.
    """
    body = "".join(secrets.choice(_ALPHABET) for _ in range(_BODY_LENGTH))
    return f"{TRACKING_PREFIX}{body}"


class ManualShippingService(ShippingProvider):
    """A courier the merchant manages themselves.

    Every operation that would need the courier's API raises. That is the
    point: a merchant must never see a "cancelled" that only happened in
    our database while the parcel is still on a motorbike.
    """

    carrier = "manual"

    def __init__(self, store_settings: dict | None = None, **_: Any) -> None:
        self.store_settings = store_settings or {}

    # ── Core contract ────────────────────────────────────────────────

    async def create_shipment(
        self,
        *,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
        rate_id: str,
        cod_amount: int | None = None,
        order_reference: str | None = None,
        notes: str | None = None,
    ) -> ShipmentLabel:
        """Issue a NUMU waybill number.

        Nothing is booked with anyone — there is no one to book with. The
        parcel becomes real when the merchant prints the waybill and hands
        it over, which is why the shipment starts at ``created`` and only
        a human moves it forward.

        ``rate_id`` carries the profile id as ``manual_<profile>`` when the
        merchant picked a specific courier.
        """
        _ = (from_address, to_address, parcel, cod_amount, notes)
        return ShipmentLabel(
            # Waybill PDF is generated on demand by the label endpoint,
            # not stored at a URL — there is no carrier hosting it.
            label_url="",
            tracking_number=generate_tracking_number(),
            carrier=self.carrier,
            service=rate_id or "manual",
        )

    async def track_shipment(self, carrier: str, tracking_number: str) -> TrackingInfo:
        """There is no carrier to ask.

        The shipment's own ``status_history`` is the record, and the route
        serves that. Raising here would turn "we already know the status"
        into an error, so this returns an empty, honest answer instead.
        """
        _ = carrier
        return TrackingInfo(
            carrier=self.carrier,
            tracking_number=tracking_number,
            status="unknown",
            events=[],
            estimated_delivery=None,
        )

    # ── Explicitly unsupported ───────────────────────────────────────
    #
    # Inherited defaults already raise; these are spelled out because a
    # future reader will reasonably ask "why doesn't manual cancel?".

    async def cancel_shipment(self, tracking_number: str) -> bool:
        """No API to cancel with.

        The merchant cancels by telling the courier and marking the
        shipment cancelled themselves. Returning True here would claim we
        stopped a parcel we cannot reach.
        """
        raise NotSupportedByCarrier(self.carrier, "cancelling with the courier")

    async def get_label(self, tracking_number: str) -> bytes:
        """Served by the waybill generator, not the provider.

        The label endpoint renders it from the shipment and the store's
        branding; there is no carrier PDF to fetch.
        """
        raise NotSupportedByCarrier(self.carrier, "fetching a carrier label")

    async def validate_address(
        self, address: ShippingAddress
    ) -> tuple[bool, ShippingAddress | None]:
        """Coverage is a profile setting, checked at rate time.

        Returns "unknown", never "invalid" — a manual courier has no
        opinion about an address, and rejecting one we simply can't
        confirm would block a perfectly deliverable order.
        """
        _ = address
        return True, None

    # ── Webhooks ─────────────────────────────────────────────────────

    def verify_webhook(
        self, payload: bytes, signature: str | None, secret: str | None
    ) -> bool:
        """No courier sends us webhooks. Fails closed."""
        _ = (payload, signature, secret)
        return False


async def get_manual_service_for_store(
    store_settings: dict | None = None,
) -> ManualShippingService:
    """Factory matching the other providers' shape, for the registry."""
    return ManualShippingService(store_settings=store_settings)


def issued_at() -> str:
    """Timestamp for a manually-created shipment's first history entry."""
    return datetime.now(UTC).isoformat()


__all__ = [
    "TRACKING_PREFIX",
    "ManualShippingService",
    "generate_tracking_number",
    "get_manual_service_for_store",
    "issued_at",
]
