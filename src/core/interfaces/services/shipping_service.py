"""Shipping service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass
class ShippingAddress:
    """Shipping address for rate calculation."""

    name: str
    street1: str
    city: str
    country: str
    street2: str | None = None
    state: str | None = None
    zip: str | None = None
    phone: str | None = None


@dataclass
class Parcel:
    """Package dimensions and weight."""

    length: float  # cm
    width: float  # cm
    height: float  # cm
    weight: float  # kg


@dataclass
class ShippingRate:
    """Shipping rate option."""

    carrier: str
    service: str
    rate_id: str
    amount: int  # In cents
    currency: str
    estimated_days: int | None = None


@dataclass
class ShipmentLabel:
    """Shipping label data."""

    label_url: str
    tracking_number: str
    carrier: str
    service: str


def parse_carrier_timestamp(value: object) -> datetime:
    """Coerce a carrier's timestamp into a real ``datetime``.

    ``TrackingEvent.timestamp`` is typed ``datetime`` but dataclasses do
    not validate, so a provider passing the raw string straight from the
    carrier looked fine until something called ``.isoformat()`` on it —
    which the tracking route does, returning 500.

    Mylerz and J&T both did exactly that. It stayed hidden while every
    carrier action resolved to Bosta (which parses); the moment tracking
    began dispatching on the shipment's real carrier, tracking a Mylerz
    or J&T shipment started failing.

    Carriers are inconsistent about format, so this accepts what they
    actually send and falls back to "now" rather than raising — losing a
    timestamp is much better than losing the whole tracking history to
    one unparseable entry.
    """
    if isinstance(value, datetime):
        return value

    if isinstance(value, int | float):
        # Some carriers send epoch seconds, some milliseconds.
        seconds = value / 1000 if value > 1e11 else value
        try:
            return datetime.fromtimestamp(seconds, tz=UTC)
        except (OSError, OverflowError, ValueError):
            return datetime.now(UTC)

    if isinstance(value, str) and value.strip():
        raw = value.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            pass
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
        ):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue

    return datetime.now(UTC)


@dataclass
class TrackingEvent:
    """Tracking event data."""

    status: str
    description: str
    location: str | None
    timestamp: datetime


@dataclass
class TrackingInfo:
    """Tracking information."""

    carrier: str
    tracking_number: str
    status: str
    events: list[TrackingEvent]
    estimated_delivery: datetime | None = None


class IShippingService(ABC):
    """Shipping service interface."""

    @abstractmethod
    async def get_rates(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
    ) -> list[ShippingRate]:
        """Get shipping rates for a parcel."""
        ...

    @abstractmethod
    async def create_shipment(
        self,
        from_address: ShippingAddress,
        to_address: ShippingAddress,
        parcel: Parcel,
        rate_id: str,
    ) -> ShipmentLabel:
        """Create a shipment and get label."""
        ...

    @abstractmethod
    async def track_shipment(
        self,
        carrier: str,
        tracking_number: str,
    ) -> TrackingInfo:
        """Track a shipment."""
        ...

    @abstractmethod
    async def validate_address(
        self,
        address: ShippingAddress,
    ) -> tuple[bool, ShippingAddress | None]:
        """Validate and potentially correct an address."""
        ...
