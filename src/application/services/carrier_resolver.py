"""Resolve the shipping service that actually owns a shipment.

**Why this module exists.** Carrier selection used to be a hardcoded
``if/elif`` chain inside one route function, and every *other* carrier
operation — cancel, return, AWB, pickups, cities — called Bosta
unconditionally. Cancelling a Mylerz shipment issued a request to
Bosta's API. See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P0.

Two rules this module exists to enforce:

1. **Dispatch on the shipment's own carrier**, never on a default.
2. **Never silently fall back to Bosta.** An unknown carrier is an error,
   not a Bosta shipment; an unknown tracking URL is ``None``, not a Bosta
   link.

This is deliberately a small dispatch table, not an abstraction. **P1
replaces its guts with the carrier registry** (`CARRIERS: dict[str,
CarrierSpec]`), at which point ``SUPPORTED_CARRIERS``,
``_TRACKING_URL_TEMPLATES`` and ``capability`` all come from the spec
instead of being hand-maintained here. Keep it boring until then.
"""

from typing import Any

from src.core.exceptions import DomainException, ValidationError

# Carrier slugs with a working provider implementation.
#
# ``jt`` is creatable here but is NOT present in the shipping-settings
# route (`routes/stores/settings.py`), which still hardcodes
# ("aramex", "bosta", "mylerz", "manual"). That inconsistency is real and
# is fixed in P1.6 — don't "fix" it by removing J&T from this tuple.
SUPPORTED_CARRIERS: tuple[str, ...] = ("bosta", "mylerz", "jt")

DEFAULT_CARRIER = "bosta"

# Merchant-facing carrier names. Arabic is Egyptian colloquial per
# DESIGN.md; the Latin slug itself stays LTR wherever it's rendered.
CARRIER_NAMES: dict[str, dict[str, str]] = {
    "bosta": {"en": "Bosta", "ar": "بوسطة"},
    "mylerz": {"en": "Mylerz", "ar": "مايلرز"},
    "jt": {"en": "J&T Express", "ar": "جيه آند تي"},
}


def carrier_name(carrier: str, lang: str = "en") -> str:
    """Display name for a carrier, falling back to the raw slug."""
    return CARRIER_NAMES.get(carrier, {}).get(lang, carrier)


# Public shopper-facing tracking pages, keyed by carrier slug.
# A carrier missing from this map yields None — see _tracking_url_for.
_TRACKING_URL_TEMPLATES: dict[str, str] = {
    "bosta": "https://bosta.co/tracking-shipment/?tracking_number={tracking_number}",
    "mylerz": "https://mylerz.com/track/{tracking_number}",
    "jt": "https://www.jtexpress-eg.com/trajectoryQuery?waybillNo={tracking_number}",
}


# Merchant-facing operation labels, for error copy. Arabic is Egyptian
# colloquial per DESIGN.md § Arabic rules.
OPERATION_LABELS: dict[str, dict[str, str]] = {
    "cancel_shipment": {"en": "cancelling shipments", "ar": "إلغاء الشحنات"},
    "request_return": {"en": "return shipments", "ar": "شحنات المرتجعات"},
    "print_awb": {"en": "printing waybills", "ar": "طباعة البوليصة"},
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
}

# Every operation the routes in this layer may ask a provider for.
# Used to answer "what can this carrier do?" without calling it.
KNOWN_OPERATIONS: tuple[str, ...] = (
    "create_shipment",
    "track_shipment",
    "get_rates",
    "validate_address",
    *OPERATION_LABELS.keys(),
)


class UnknownCarrierError(ValidationError):
    """Raised when a carrier slug has no provider implementation.

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

    Distinct from UnknownCarrierError: the carrier is valid, but this
    provider does not implement this operation. Routes map it to 501.

    P1 replaces the method-presence check with declared
    ``ProviderCapabilities``.
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
        return {
            "code": "CARRIER_OPERATION_UNSUPPORTED",
            "message_en": (
                f"{carrier_name(self.carrier, 'en')} does not support "
                f"{OPERATION_LABELS.get(self.operation, {}).get('en', self.operation)}."
            ),
            "message_ar": (
                f"{carrier_name(self.carrier, 'ar')} مش بيدعم "
                f"{OPERATION_LABELS.get(self.operation, {}).get('ar', self.operation)}."
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
    if not carrier:
        raise UnknownCarrierError(str(carrier))
    slug = carrier.strip().lower()
    if slug not in SUPPORTED_CARRIERS:
        raise UnknownCarrierError(carrier)
    return slug


def tracking_url_for(carrier: str, tracking_number: str | None) -> str | None:
    """Public tracking URL for a carrier, or None if we don't know one.

    Deliberately returns None rather than falling back to another
    carrier's URL. A tracking link that points at the wrong carrier is
    worse than no link — it sends the shopper somewhere that will never
    recognise their number.
    """
    if not tracking_number:
        return None
    template = _TRACKING_URL_TEMPLATES.get(carrier)
    if not template:
        return None
    return template.format(tracking_number=tracking_number)


async def service_for_carrier(carrier: str, store_settings: dict | None) -> Any:
    """Resolve the provider for a carrier slug.

    Raises:
        UnknownCarrierError: the slug has no provider.
    """
    slug = validate_carrier(carrier)
    settings = store_settings or {}

    if slug == "mylerz":
        from src.infrastructure.external_services.mylerz import (
            get_mylerz_service_for_store,
        )

        return await get_mylerz_service_for_store(settings)

    if slug == "jt":
        from src.infrastructure.external_services.jt import get_jt_service_for_store

        return await get_jt_service_for_store(settings)

    from src.infrastructure.external_services.bosta.shipping_service import (
        get_bosta_service_for_store,
    )

    return await get_bosta_service_for_store(settings)


async def service_for_shipment(shipment: Any, store_settings: dict | None) -> Any:
    """Resolve the provider that actually owns this shipment.

    This is the fix for the P0 bug: every carrier action must dispatch on
    ``shipment.carrier``, never on a default.
    """
    return await service_for_carrier(shipment.carrier, store_settings)


def supported_operations(carrier: str) -> list[str]:
    """Which operations this carrier's provider actually implements.

    Answers "what can this carrier do?" **without** constructing a client
    or making a call, by inspecting the provider class. Mylerz and J&T
    implement only the four base methods; Bosta implements twenty.

    This exists so the hub can disable the actions a carrier can't do
    rather than letting merchants discover each one by clicking it and
    getting a 501. Before P0 those clicks silently hit Bosta instead.

    P1 replaces class introspection with declared ProviderCapabilities.
    """
    try:
        slug = validate_carrier(carrier)
    except UnknownCarrierError:
        return []

    if slug == "mylerz":
        from src.infrastructure.external_services.mylerz.shipping_service import (
            MylerzShippingService as cls,
        )
    elif slug == "jt":
        from src.infrastructure.external_services.jt.shipping_service import (
            JTShippingService as cls,
        )
    else:
        from src.infrastructure.external_services.bosta.shipping_service import (
            BostaShippingService as cls,
        )

    return [op for op in KNOWN_OPERATIONS if callable(getattr(cls, op, None))]


def carrier_catalog() -> list[dict[str, Any]]:
    """Every supported carrier with its display names and capabilities.

    Minimal precursor to P1.3's ``GET /shipping/carriers`` registry
    endpoint — same intent, hand-maintained until the registry lands.
    """
    return [
        {
            "slug": slug,
            "name_en": carrier_name(slug, "en"),
            "name_ar": carrier_name(slug, "ar"),
            "is_default": slug == DEFAULT_CARRIER,
            "tracking_url_template": _TRACKING_URL_TEMPLATES.get(slug),
            "supported_operations": supported_operations(slug),
        }
        for slug in SUPPORTED_CARRIERS
    ]


def capability(service: Any, operation: str, carrier: str) -> Any:
    """Return a provider method, or raise if the provider lacks it.

    Mylerz and J&T implement only the four base interface methods, so
    calling ``print_awb`` or ``create_pickup`` on them would raise
    AttributeError deep in a route. This turns that into an explicit,
    translatable domain error at the call site.

    P1 replaces method presence with declared ProviderCapabilities.
    """
    fn = getattr(service, operation, None)
    if fn is None or not callable(fn):
        raise CarrierCapabilityError(carrier, operation)
    return fn
