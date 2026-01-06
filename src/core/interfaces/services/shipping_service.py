"""Shipping service interface."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


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
