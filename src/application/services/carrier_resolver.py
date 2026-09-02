"""Resolve the shipping service that actually owns a shipment.

**Why this module exists.** Carrier selection used to be a hardcoded
``if/elif`` chain inside one route function, and every *other* carrier
operation — cancel, return, AWB, pickups, cities — called Bosta
unconditionally. Cancelling a Mylerz shipment issued a request to
Bosta's API. See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P0.

Two rules this module enforces:

1. **Dispatch on the shipment's own carrier**, never on a default.
2. **Never silently fall back to Bosta.** An unknown carrier is an error,
   not a Bosta shipment; an unknown tracking URL is ``None``, not a Bosta
   link.

P1 update: the hand-maintained tables that used to live here (slugs,
names, tracking templates, provider imports) now come from
:mod:`src.application.services.carrier_registry`. This module keeps only
*behaviour* — resolve, validate, capability-check, bilingual errors —
and the registry holds the *data*. The public API is unchanged so the
call sites from P0 did not have to churn.
"""

from typing import Any

from src.application.services.carrier_registry import (
    CARRIERS,
    CarrierSpec,
    carrier_slugs,
    catalog,
    default_carrier,
    get_spec,
)
from src.core.exceptions import DomainException, ValidationError

# Backwards-compatible aliases. These are now *derived from* the
# registry rather than hand-maintained — adding a carrier there is
# enough. Kept as module constants because callers import them.
SUPPORTED_CARRIERS: tuple[str, ...] = carrier_slugs()
DEFAULT_CARRIER: str = default_carrier()


def carrier_name(carrier: str, lang: str = "en") -> str:
    """Display name for a carrier, falling back to the raw slug."""
    spec = get_spec(carrier)
    if spec is None:
        return carrier
    return spec.name_ar if lang == "ar" else spec.name_en


# Merchant-facing operation labels, for error copy. Arabic is Egyptian
# colloquial per DESIGN.md § Arabic rules. Keyed by provider method name
# because that is what a route asks for.
OPERATION_LABELS: dict[str, dict[str, str]] = {
    "cancel_shipment": {"en": "cancelling shipments", "ar": "إلغاء الشحنات"},
    "request_return": {"en": "return shipments", "ar": "شحنات المرتجعات"},
    "print_awb": {"en": "printing waybills", "ar": "طباعة البوليصة"},
    "get_label": {"en": "printing waybills", "ar": "طباعة البوليصة"},
    "update_delivery": {"en": "editing a delivery", "ar": "تعديل الشحنة"},
    "get_delivery": {"en": "delivery details", "ar": "تفاصيل الشحنة"},
    "create_pickup": {"en": "scheduling pickups", "ar": "حجز استلام"},
    "list_pickups": {"en": "listing pickups", "ar": "عرض مواعيد الاستلام"},
    "get_pickup": {"en": "pickup details", "ar": "تفاصيل الاستلام"},
    "update_pickup": {"en": "editing pickups", "ar": "تعديل الاستلام"},
    "delete_pickup": {"en": "cancelling pickups", "ar": "إلغاء الاستلام"},
    "get_pickup_locations": {"en": "pickup locations", "ar": "أماكن الاستلام"},
    "get_cities": {"en": "city lookup", "ar": "قائمة المدن"},
    "get_city_zones": {"en": "zone lookup", "ar": "قائمة المناطق"},
    "get_rates": {"en": "live rates", "ar": "أسعار الشحن المباشرة"},
}

# Which declared capability each provider method needs. A method with no
# entry here is part of the base contract every carrier implements.
#
# This is the P1 replacement for `hasattr` introspection: capability is
# now *declared* in the registry, and the registry's own test asserts the
# declaration matches what the provider class really implements, so the
# two cannot drift.
OPERATION_CAPABILITY: dict[str, str] = {
    "cancel_shipment": "supports_cancel",
    "request_return": "supports_return",
    "print_awb": "supports_labels",
    "get_label": "supports_labels",
    "update_delivery": "supports_delivery_update",
    "get_delivery": "supports_delivery_update",
    "create_pickup": "supports_pickup",
    "list_pickups": "supports_pickup",
    "get_pickup": "supports_pickup",
    "update_pickup": "supports_pickup",
    "delete_pickup": "supports_pickup",
    "get_pickup_locations": "supports_pickup",
    "get_cities": "supports_city_lookup",
    "get_city_zones": "supports_city_lookup",
    "get_rates": "supports_live_rates",
    "track_shipment": "supports_tracking",
}

#: Every operation the route layer may ask a provider for.
KNOWN_OPERATIONS: tuple[str, ...] = (
    "create_shipment",
    "track_shipment",
    "validate_address",
    *OPERATION_CAPABILITY.keys(),
)


class UnknownCarrierError(ValidationError):
    """Raised when a carrier slug has no registry entry.

    Subclasses ValidationError so existing 400-mapping handlers catch it.
    """

    def __init__(self, carrier: str) -> None:
        self.carrier = carrier
        super().__init__(
            f"Unknown carrier '{carrier}'. "
            f"Supported carriers: {', '.join(SUPPORTED_CARRIERS)}."
        )

    def as_detail(self) -> dict[str, Any]:
        """Structured bilingual body for HTTPException."""
        return {
            "code": "UNKNOWN_CARRIER",
            "message_en": (
                f"Unknown carrier '{self.carrier}'. "
                f"Supported: {', '.join(SUPPORTED_CARRIERS)}."
            ),
            "message_ar": (
                f"شركة شحن غير معروفة '{self.carrier}'. "
                f"المتاح: {', '.join(SUPPORTED_CARRIERS)}."
            ),
            "carrier": self.carrier,
            "supported_carriers": list(SUPPORTED_CARRIERS),
        }


class CarrierCapabilityError(DomainException):
    """Raised when a carrier cannot perform the requested operation.

    Distinct from UnknownCarrierError: the carrier is valid, but its
    registry entry does not declare this capability. Routes map it to 501.
    """

    def __init__(self, carrier: str, operation: str) -> None:
        self.carrier = carrier
        self.operation = operation
        self.supported = supported_operations(carrier)
        super().__init__(
            f"Carrier '{carrier}' does not support this operation ({operation})."
        )

    def as_detail(self) -> dict[str, Any]:
        """Structured bilingual body for HTTPException.

        Matches the ``{code, message_en, message_ar}`` contract that
        ``api/middleware/error_handler.py`` preserves, so the hub can
        localize and branch on the code instead of surfacing raw English
        to an Arabic-speaking merchant.
        """
        labels = OPERATION_LABELS.get(self.operation, {})
        return {
            "code": "CARRIER_OPERATION_UNSUPPORTED",
            "message_en": (
                f"{carrier_name(self.carrier, 'en')} does not support "
                f"{labels.get('en', self.operation)}."
            ),
            "message_ar": (
                f"{carrier_name(self.carrier, 'ar')} مش بيدعم "
                f"{labels.get('ar', self.operation)}."
            ),
            "carrier": self.carrier,
            "operation": self.operation,
            # Lets the client disable the right buttons instead of
            # discovering each unsupported action by clicking it.
            "supported_operations": self.supported,
        }


def validate_carrier(carrier: str | None) -> str:
    """Return a known carrier slug, or raise.

    Never defaults. Passing an unrecognised slug used to silently book a
    real Bosta shipment; now it raises.
    """
    spec = get_spec(carrier)
    if spec is None:
        raise UnknownCarrierError(str(carrier) if carrier else "")
    return spec.slug


def spec_for(carrier: str | None) -> CarrierSpec:
    """Registry entry for a slug, or raise UnknownCarrierError."""
    spec = get_spec(carrier)
    if spec is None:
        raise UnknownCarrierError(str(carrier) if carrier else "")
    return spec


def tracking_url_for(carrier: str, tracking_number: str | None) -> str | None:
    """Public tracking URL for a carrier, or None if we don't know one.

    Deliberately returns None rather than falling back to another
    carrier's URL. A tracking link that points at the wrong carrier is
    worse than no link — it sends the shopper somewhere that will never
    recognise their number.
    """
    spec = get_spec(carrier)
    if spec is None:
        return None
    return spec.tracking_url(tracking_number)


def map_carrier_status(carrier: str, raw_status: str | None) -> Any:
    """Carrier's own status string → NUMU's ShipmentStatus, or None."""
    spec = get_spec(carrier)
    if spec is None:
        return None
    return spec.map_status(raw_status)


async def service_for_carrier(carrier: str, store_settings: dict | None) -> Any:
    """Resolve the provider for a carrier slug.

    Raises:
        UnknownCarrierError: the slug has no registry entry.
    """
    spec = spec_for(carrier)
    return await spec.factory(store_settings or {})


async def service_for_shipment(shipment: Any, store_settings: dict | None) -> Any:
    """Resolve the provider that actually owns this shipment.

    This is the fix for the P0 bug: every carrier action must dispatch on
    ``shipment.carrier``, never on a default.
    """
    return await service_for_carrier(shipment.carrier, store_settings)


def supports(carrier: str, operation: str) -> bool:
    """Whether a carrier declares the capability an operation needs."""
    spec = get_spec(carrier)
    if spec is None:
        return False
    required = OPERATION_CAPABILITY.get(operation)
    if required is None:
        return True  # base contract — every carrier has it
    return bool(getattr(spec.capabilities, required, False))


def supported_operations(carrier: str) -> list[str]:
    """Which operations this carrier declares it can do.

    Answers "what can this carrier do?" **without** constructing a client
    or making a call, so the hub can render shipping settings for a store
    that has not connected anything yet.

    Exists so the hub disables the actions a carrier can't do rather than
    letting merchants discover each one by clicking it and getting a 501.
    Before P0 those clicks silently hit Bosta instead.
    """
    if get_spec(carrier) is None:
        return []
    return [op for op in KNOWN_OPERATIONS if supports(carrier, op)]


def carrier_catalog() -> list[dict[str, Any]]:
    """Every selectable carrier, with names, capabilities and credentials.

    Registry-backed: adding a carrier to ``CARRIERS`` makes it appear
    here, and in the hub, with no further code change.
    """
    entries = catalog()
    for entry in entries:
        entry["supported_operations"] = supported_operations(entry["slug"])
    return entries


def capability(service: Any, operation: str, carrier: str) -> Any:
    """Return a provider method, or raise if the carrier can't do it.

    Checks the **declared** capability first, so a carrier that has a
    method but hasn't been verified against the live API is still gated.
    The attribute check stays as a backstop for the base contract.
    """
    if not supports(carrier, operation):
        raise CarrierCapabilityError(carrier, operation)
    fn = getattr(service, operation, None)
    if fn is None or not callable(fn):
        raise CarrierCapabilityError(carrier, operation)
    return fn


__all__ = [
    "CARRIERS",
    "DEFAULT_CARRIER",
    "KNOWN_OPERATIONS",
    "OPERATION_CAPABILITY",
    "OPERATION_LABELS",
    "SUPPORTED_CARRIERS",
    "CarrierCapabilityError",
    "UnknownCarrierError",
    "capability",
    "carrier_catalog",
    "carrier_name",
    "map_carrier_status",
    "service_for_carrier",
    "service_for_shipment",
    "spec_for",
    "supported_operations",
    "supports",
    "tracking_url_for",
    "validate_carrier",
]
