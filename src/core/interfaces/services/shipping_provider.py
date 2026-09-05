"""The shipping provider contract — what an Egyptian carrier actually does.

``IShippingService`` (in ``shipping_service.py``) declares only four
methods: get_rates, create_shipment, track_shipment, validate_address.
Everything a real carrier integration needs — cancel, returns, waybill
bytes, pickups, city/zone lookup, webhook verification — lived as extra
methods on the Bosta class only, off the interface, which is how the
routes ended up calling Bosta for every carrier.

This module widens that contract, and makes "what can this carrier do?"
a **declaration** rather than something the routes discover by calling.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P1.

Two deliberate choices:

* ``ProviderCapabilities`` is declared in the carrier registry, not
  inferred. But ``tests/unit/services/test_carrier_registry.py`` asserts
  each declaration matches what the provider class actually implements,
  so a declaration cannot drift from reality.
* Unsupported operations raise ``NotSupportedByCarrier`` rather than
  returning None or silently no-op'ing. A carrier that cannot cancel must
  say so; it must never look like it cancelled.

``IShippingService`` stays for now — Bosta, Mylerz and J&T still
implement it, and P4/P6 migrate them onto this contract. New providers
(the P2 manual carrier first) implement ``ShippingProvider`` directly.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, fields
from typing import Any

from src.core.entities.shipment import ShipmentStatus
from src.core.exceptions import DomainException
from src.core.interfaces.services.shipping_service import (
    Parcel,
    ShipmentLabel,
    ShippingAddress,
    ShippingRate,
    TrackingInfo,
)

__all__ = [
    "CarrierApiError",
    "NotSupportedByCarrier",
    "Parcel",
    "PickupRequest",
    "ProviderCapabilities",
    "ShipmentLabel",
    "ShippingAddress",
    "ShippingProvider",
    "ShippingRate",
    "TrackingInfo",
    "WebhookEvent",
]


class CarrierApiError(ValueError):
    """A carrier answered, but not with success.

    Carries the HTTP status, because *why* a call failed decides who is at
    fault. A 401 means the merchant's credentials are wrong; a 503 means
    the carrier is having a bad day. Providers used to raise a bare
    ``ValueError("Failed to get cities")``, which threw that distinction
    away — and credential verification then reported a carrier outage as
    "your keys were rejected".

    Subclasses ``ValueError`` so every existing ``except ValueError``
    keeps working.
    """

    def __init__(self, status_code: int, message: str = "", carrier: str = "") -> None:
        self.status_code = status_code
        self.carrier = carrier
        super().__init__(message or f"Carrier returned HTTP {status_code}")

    @property
    def is_auth_failure(self) -> bool:
        """Whether this is the merchant's credentials, rather than the carrier."""
        return self.status_code in (401, 403)

    @property
    def is_carrier_side(self) -> bool:
        """Rate limiting or an outage — nothing the merchant can fix."""
        return self.status_code == 429 or self.status_code >= 500


class NotSupportedByCarrier(DomainException):
    """Raised when a provider is asked for an operation it cannot do.

    Routes map this to 501. Never return None for an unsupported
    operation — a caller cannot tell that apart from "nothing happened",
    which is how a failed cancel looked like a successful one.
    """

    def __init__(self, carrier: str, operation: str) -> None:
        self.carrier = carrier
        self.operation = operation
        super().__init__(f"Carrier '{carrier}' does not support {operation}.")


@dataclass(frozen=True)
class ProviderCapabilities:
    """What a carrier can actually do.

    Egypt Post, Aramex and Bosta are not the same shape: a Tier 3 manual
    carrier prints a waybill but has no API to cancel with, and an
    international carrier quotes live rates but barely does COD. Without
    this the UI offers every action for every carrier and the merchant
    finds the gaps by clicking.

    Defaults are all False — a new provider opts in to what it supports,
    so forgetting to declare something fails closed.
    """

    supports_cod: bool = False
    supports_labels: bool = False
    supports_pickup: bool = False
    supports_return: bool = False
    supports_cancel: bool = False
    supports_live_rates: bool = False
    supports_webhooks: bool = False
    supports_tracking: bool = False
    supports_city_lookup: bool = False
    supports_delivery_update: bool = False

    def as_dict(self) -> dict[str, bool]:
        """Serialisable form for the hub."""
        return {f.name: getattr(self, f.name) for f in fields(self)}

    def enabled(self) -> list[str]:
        """The capability names that are True, for compact logging."""
        return [f.name for f in fields(self) if getattr(self, f.name)]


@dataclass(frozen=True)
class PickupRequest:
    """A courier pickup booking."""

    location_id: str
    scheduled_date: str
    time_slot: str | None = None
    contact_name: str | None = None
    contact_phone: str | None = None
    contact_email: str | None = None
    notes: str | None = None


@dataclass(frozen=True)
class WebhookEvent:
    """A carrier webhook, normalised.

    ``status`` is already mapped to NUMU's vocabulary via the registry's
    per-carrier status map, so downstream code never branches on a
    carrier's private strings. ``raw_status`` is kept for the audit trail
    — an unmapped status must be visible, not swallowed.
    """

    tracking_number: str
    status: ShipmentStatus | None
    raw_status: str
    description: str = ""
    cod_collected: bool = False
    cod_amount: float | None = None
    occurred_at: str | None = None
    payload: dict[str, Any] | None = None

    @property
    def is_mapped(self) -> bool:
        """False when the carrier sent a status we don't recognise."""
        return self.status is not None


class ShippingProvider(ABC):
    """What every carrier integration must implement.

    Only the four base methods are abstract — they are the irreducible
    minimum for a carrier to be useful. Everything else has a default
    that raises :class:`NotSupportedByCarrier`, so a thin provider stays
    small and honest instead of stubbing methods that quietly do nothing.

    Declare what you override in the registry's ``capabilities``; the
    registry test enforces that the two agree.
    """

    #: Registry slug. Set by each concrete provider.
    carrier: str = "unknown"

    # ── Core contract — every provider implements these ──────────────

    @abstractmethod
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
        """Book a shipment and return its tracking number and label."""

    @abstractmethod
    async def track_shipment(self, carrier: str, tracking_number: str) -> TrackingInfo:
        """Current status and event history for a waybill."""

    # ── Optional contract — override what the carrier supports ───────

    async def get_rates(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
    ) -> list[ShippingRate]:
        """Live quotes. Requires ``supports_live_rates``.

        A carrier that cannot quote must raise rather than invent a
        price — Mylerz and J&T shipped hardcoded `_default_rates`
        fallbacks that presented guesses as carrier quotes.
        """
        raise NotSupportedByCarrier(self.carrier, "live rates")

    async def cancel_shipment(self, tracking_number: str) -> bool:
        """Cancel with the carrier. Requires ``supports_cancel``."""
        raise NotSupportedByCarrier(self.carrier, "cancelling shipments")

    async def request_return(
        self, tracking_number: str, reason: str = ""
    ) -> str | None:
        """Open a return leg. Requires ``supports_return``."""
        raise NotSupportedByCarrier(self.carrier, "return shipments")

    async def get_label(self, tracking_number: str) -> bytes:
        """Waybill (بوليصة) PDF bytes. Requires ``supports_labels``."""
        raise NotSupportedByCarrier(self.carrier, "printing waybills")

    async def update_delivery(self, tracking_number: str, **changes: Any) -> Any:
        """Amend receiver/COD/notes. Requires ``supports_delivery_update``."""
        raise NotSupportedByCarrier(self.carrier, "editing a delivery")

    async def create_pickup(self, request: PickupRequest) -> Any:
        """Book a courier pickup. Requires ``supports_pickup``."""
        raise NotSupportedByCarrier(self.carrier, "scheduling pickups")

    async def list_pickups(self, page: int = 0, limit: int = 50) -> Any:
        """Scheduled pickups. Requires ``supports_pickup``."""
        raise NotSupportedByCarrier(self.carrier, "listing pickups")

    async def cancel_pickup(self, pickup_id: str) -> bool:
        """Cancel a pickup. Requires ``supports_pickup``."""
        raise NotSupportedByCarrier(self.carrier, "cancelling pickups")

    async def get_cities(self) -> list[dict[str, Any]]:
        """Carrier's serviceable cities. Requires ``supports_city_lookup``."""
        raise NotSupportedByCarrier(self.carrier, "city lookup")

    async def get_zones(self, city_id: str) -> list[dict[str, Any]]:
        """Zones within a city. Requires ``supports_city_lookup``."""
        raise NotSupportedByCarrier(self.carrier, "zone lookup")

    async def validate_address(
        self, address: ShippingAddress
    ) -> tuple[bool, ShippingAddress | None]:
        """Serviceability check: does this carrier deliver here?

        Kept from the old interface but redefined — this is coverage, not
        postal correctness. A carrier with no opinion returns
        ``(True, None)`` meaning "unknown, not invalid"; it must never
        reject an address just because it cannot confirm it.
        """
        return True, None

    # ── Webhooks ─────────────────────────────────────────────────────

    def verify_webhook(
        self, payload: bytes, signature: str | None, secret: str | None
    ) -> bool:
        """Authenticate an inbound webhook. Requires ``supports_webhooks``.

        Fails closed: a provider that cannot verify returns False rather
        than accepting unsigned traffic.
        """
        return False

    def parse_webhook(self, payload: dict[str, Any]) -> WebhookEvent | None:
        """Normalise a webhook body. Requires ``supports_webhooks``."""
        raise NotSupportedByCarrier(self.carrier, "webhooks")
