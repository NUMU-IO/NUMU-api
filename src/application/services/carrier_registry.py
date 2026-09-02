"""The carrier registry — one place that knows every carrier.

Before this, carrier knowledge was scattered across at least five
hardcoded surfaces that drifted apart:

* ``routes/stores/shipments.py`` — an ``if/elif`` dispatch chain
* ``routes/stores/settings.py`` — a per-carrier ``if`` chain, an
  ``("aramex","bosta","mylerz","manual")`` tuple, per-carrier response
  fields and per-carrier request fields
* ``webhooks/{bosta,jt,mylerz}.py`` — three near-identical files with a
  private status map each
* ``numu-mcp`` — its own ``SHIPMENT_CARRIERS`` tuple
* ``Logistics.tsx`` — a hardcoded array with inline SVG logos

The drift was real and user-visible: **J&T is fully creatable through the
shipments route but absent from every settings surface**, so a merchant
could hold J&T shipments they could not enable J&T to make.

Adding a carrier should mean adding one entry here. This module is the
idiom the codebase already uses for
``application/services/email_template_registry.py`` — a literal dict plus
a ``validate_registry()`` called at startup.

See ``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P1.3.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from src.core.entities.shipment import ShipmentStatus
from src.core.interfaces.services.shipping_provider import ProviderCapabilities

# ── Credential field descriptors ────────────────────────────────────


@dataclass(frozen=True)
class CredentialField:
    """One input on the hub's "connect this carrier" form.

    The hub generates the form from these instead of hardcoding a panel
    per carrier, so a new carrier needs no frontend commit.
    """

    key: str
    label_en: str
    label_ar: str
    required: bool = True
    #: Rendered masked and never echoed back in a response.
    secret: bool = True
    help_en: str = ""
    help_ar: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label_en": self.label_en,
            "label_ar": self.label_ar,
            "required": self.required,
            "secret": self.secret,
            "help_en": self.help_en,
            "help_ar": self.help_ar,
        }


# ── Carrier spec ────────────────────────────────────────────────────

#: Tier 1 native API, Tier 2 aggregator, Tier 3 manual/offline.
CarrierTier = str


@dataclass(frozen=True)
class CarrierSpec:
    """Everything the platform knows about one carrier."""

    slug: str
    name_en: str
    name_ar: str
    tier: CarrierTier
    capabilities: ProviderCapabilities
    #: Builds a configured provider from a store's settings dict.
    factory: Callable[[dict], Awaitable[Any]]
    #: Imports and returns the provider class. A *callable*, not the class
    #: itself: evaluating it at definition time would eagerly import all
    #: three carrier SDK modules on any import of this registry — which
    #: the settings route, the webhook router and the hub catalog all do,
    #: none of which need a provider. Use :meth:`provider_class`.
    provider_cls_loader: Callable[[], type]
    #: Shopper-facing tracking page. None when the carrier has none —
    #: never substitute another carrier's URL.
    tracking_url_template: str | None = None
    credential_fields: tuple[CredentialField, ...] = ()
    #: Carrier's own status strings → NUMU's vocabulary.
    status_map: dict[str, ShipmentStatus] = field(default_factory=dict)
    #: Brand colour for the hub's carrier card.
    brand_color: str | None = None
    is_default: bool = False
    #: False hides it from the hub while keeping shipments resolvable.
    is_selectable: bool = True
    #: A cheap, read-only provider method used to prove credentials
    #: actually work. None when the carrier has no safe call to make —
    #: then the UI must say "configured, not verified" rather than show a
    #: green badge it cannot justify.
    #:
    #: Saving credentials used to set ``is_configured: True`` without ever
    #: calling the carrier, so a typo'd API key showed a live badge. The
    #: hub patched around it with a localStorage probe; this is the
    #: server-side fix.
    verification_operation: str | None = None
    #: Header the carrier signs its webhooks with.
    webhook_signature_header: str | None = None
    #: Turns a raw webhook body into a WebhookEvent. Lazily imported for
    #: the same reason as ``provider_cls_loader``.
    webhook_parser_loader: Callable[[], Callable] | None = None

    def parse_webhook(self, data: dict[str, Any]) -> Any:
        """Normalise a raw webhook body, or None if unparseable.

        Fills in ``status`` from this carrier's status map so callers
        never see the carrier's private vocabulary.
        """
        if self.webhook_parser_loader is None:
            return None
        parser = self.webhook_parser_loader()
        event = parser(data)
        if event is None:
            return None
        # dataclass is frozen; rebuild with the mapped status.
        from dataclasses import replace

        return replace(event, status=self.map_status(event.raw_status))

    def provider_class(self) -> type:
        """Import and return the provider class.

        Deferred on purpose — see ``provider_cls_loader``. Used by the
        registry's capability-truthfulness test and anything that needs
        to introspect a provider without constructing one.
        """
        return self.provider_cls_loader()

    def tracking_url(self, tracking_number: str | None) -> str | None:
        if not tracking_number or not self.tracking_url_template:
            return None
        return self.tracking_url_template.format(tracking_number=tracking_number)

    def map_status(self, raw: str | None) -> ShipmentStatus | None:
        """Carrier status → NUMU status, or None if unrecognised.

        Returns None rather than guessing: an unmapped status must be
        visible in the logs, not silently coerced to IN_TRANSIT.
        """
        if not raw:
            return None
        return self.status_map.get(raw.strip().upper())

    def as_dict(self, *, include_credentials: bool = True) -> dict[str, Any]:
        """Serialisable form for the hub's carrier catalog."""
        out: dict[str, Any] = {
            "slug": self.slug,
            "name_en": self.name_en,
            "name_ar": self.name_ar,
            "tier": self.tier,
            "brand_color": self.brand_color,
            "is_default": self.is_default,
            "is_selectable": self.is_selectable,
            "tracking_url_template": self.tracking_url_template,
            "capabilities": self.capabilities.as_dict(),
            "can_verify": self.verification_operation is not None,
        }
        if include_credentials:
            out["credential_fields"] = [f.as_dict() for f in self.credential_fields]
        return out


# ── Provider factories ──────────────────────────────────────────────
#
# Imports are deferred so importing the registry (which the settings
# route, the hub catalog and the webhook router all do) never drags in
# every carrier SDK.


async def _bosta_factory(store_settings: dict) -> Any:
    from src.infrastructure.external_services.bosta.shipping_service import (
        get_bosta_service_for_store,
    )

    return await get_bosta_service_for_store(store_settings)


async def _mylerz_factory(store_settings: dict) -> Any:
    from src.infrastructure.external_services.mylerz import (
        get_mylerz_service_for_store,
    )

    return await get_mylerz_service_for_store(store_settings)


async def _jt_factory(store_settings: dict) -> Any:
    from src.infrastructure.external_services.jt import get_jt_service_for_store

    return await get_jt_service_for_store(store_settings)


def _bosta_cls() -> type:
    from src.infrastructure.external_services.bosta.shipping_service import (
        BostaShippingService,
    )

    return BostaShippingService


def _mylerz_cls() -> type:
    from src.infrastructure.external_services.mylerz.shipping_service import (
        MylerzShippingService,
    )

    return MylerzShippingService


def _jt_cls() -> type:
    from src.infrastructure.external_services.jt.shipping_service import (
        JTShippingService,
    )

    return JTShippingService


def _parser(slug: str):
    """Lazily fetch a carrier's webhook parser."""

    def _load():
        from src.infrastructure.webhooks.carrier_parsers import PARSERS

        return PARSERS[slug]

    return _load


# ── Shared credential shapes ────────────────────────────────────────

_WEBHOOK_SECRET = CredentialField(
    key="webhook_secret",
    label_en="Webhook secret",
    label_ar="مفتاح الويب هوك",
    required=False,
    help_en="Used to verify status callbacks. Leave blank if unused.",
    help_ar="بيتأكد إن تحديثات الشحن جاية من الشركة فعلاً. سيبه فاضي لو مش مستخدمه.",
)


# ── The registry ────────────────────────────────────────────────────

CARRIERS: dict[str, CarrierSpec] = {
    "bosta": CarrierSpec(
        slug="bosta",
        name_en="Bosta",
        name_ar="بوسطة",
        tier="native",
        brand_color="#E30613",
        is_default=True,
        capabilities=ProviderCapabilities(
            supports_cod=True,
            supports_labels=True,
            supports_pickup=True,
            supports_return=True,
            supports_cancel=True,
            supports_live_rates=True,
            supports_webhooks=True,
            supports_tracking=True,
            supports_city_lookup=True,
            supports_delivery_update=True,
        ),
        factory=_bosta_factory,
        provider_cls_loader=_bosta_cls,
        verification_operation="get_cities",
        webhook_signature_header="x-bosta-signature",
        webhook_parser_loader=_parser("bosta"),
        tracking_url_template=(
            "https://bosta.co/tracking-shipment/?tracking_number={tracking_number}"
        ),
        credential_fields=(
            CredentialField(
                key="api_key", label_en="API key", label_ar="مفتاح الـ API"
            ),
            CredentialField(
                key="business_id",
                label_en="Business ID",
                label_ar="رقم الحساب التجاري",
                secret=False,
            ),
            _WEBHOOK_SECRET,
        ),
        status_map={
            "PENDING_PICKUP": ShipmentStatus.CREATED,
            "PICKED_UP": ShipmentStatus.PICKED_UP,
            "IN_WAREHOUSE": ShipmentStatus.IN_TRANSIT,
            "IN_TRANSIT": ShipmentStatus.IN_TRANSIT,
            "OUT_FOR_DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
            "DELIVERED": ShipmentStatus.DELIVERED,
            "RETURNED": ShipmentStatus.RETURNED,
            "CANCELLED": ShipmentStatus.CANCELLED,
            "DELIVERY_FAILED": ShipmentStatus.FAILED,
        },
    ),
    "mylerz": CarrierSpec(
        slug="mylerz",
        name_en="Mylerz",
        name_ar="مايلرز",
        tier="native",
        brand_color="#FB4F14",
        # Only the four base methods exist today. Capabilities stay
        # narrow until P4 implements the rest — declaring them now would
        # make the hub offer actions that 501.
        capabilities=ProviderCapabilities(
            supports_cod=True,
            supports_tracking=True,
            supports_webhooks=True,
        ),
        factory=_mylerz_factory,
        provider_cls_loader=_mylerz_cls,
        webhook_signature_header="x-mylerz-signature",
        webhook_parser_loader=_parser("mylerz"),
        tracking_url_template="https://mylerz.com/track/{tracking_number}",
        credential_fields=(
            CredentialField(
                key="api_key", label_en="API key", label_ar="مفتاح الـ API"
            ),
            CredentialField(
                key="merchant_id",
                label_en="Merchant ID",
                label_ar="رقم التاجر",
                secret=False,
            ),
            _WEBHOOK_SECRET,
        ),
        status_map={
            "PICKED_UP": ShipmentStatus.PICKED_UP,
            "IN_TRANSIT": ShipmentStatus.IN_TRANSIT,
            "OUT_FOR_DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
            "DELIVERED": ShipmentStatus.DELIVERED,
            "RETURNED": ShipmentStatus.RETURNED,
            "FAILED": ShipmentStatus.FAILED,
            "CANCELLED": ShipmentStatus.CANCELLED,
        },
    ),
    "jt": CarrierSpec(
        slug="jt",
        name_en="J&T Express",
        name_ar="جيه آند تي",
        tier="native",
        brand_color="#D80D18",
        capabilities=ProviderCapabilities(
            supports_cod=True,
            supports_tracking=True,
            supports_webhooks=True,
        ),
        factory=_jt_factory,
        provider_cls_loader=_jt_cls,
        webhook_signature_header="x-jt-signature",
        webhook_parser_loader=_parser("jt"),
        tracking_url_template=(
            "https://www.jtexpress-eg.com/trajectoryQuery?waybillNo={tracking_number}"
        ),
        credential_fields=(
            CredentialField(
                key="api_key", label_en="API key", label_ar="مفتاح الـ API"
            ),
            CredentialField(
                key="customer_code",
                label_en="Customer code",
                label_ar="كود العميل",
                secret=False,
            ),
            _WEBHOOK_SECRET,
        ),
        status_map={
            "PICKUP": ShipmentStatus.PICKED_UP,
            "PICKED_UP": ShipmentStatus.PICKED_UP,
            "IN_TRANSIT": ShipmentStatus.IN_TRANSIT,
            "TRANSIT": ShipmentStatus.IN_TRANSIT,
            "OUT_FOR_DELIVERY": ShipmentStatus.OUT_FOR_DELIVERY,
            "DELIVERING": ShipmentStatus.OUT_FOR_DELIVERY,
            "DELIVERED": ShipmentStatus.DELIVERED,
            "SIGNED": ShipmentStatus.DELIVERED,
            "RETURNED": ShipmentStatus.RETURNED,
            "REJECTED": ShipmentStatus.RETURNED,
            "FAILED": ShipmentStatus.FAILED,
            "PROBLEM": ShipmentStatus.FAILED,
            "CANCELLED": ShipmentStatus.CANCELLED,
            "VOIDED": ShipmentStatus.CANCELLED,
        },
    ),
}


# ── Accessors ───────────────────────────────────────────────────────


def all_carriers(*, selectable_only: bool = False) -> list[CarrierSpec]:
    specs = list(CARRIERS.values())
    if selectable_only:
        specs = [s for s in specs if s.is_selectable]
    return specs


def carrier_slugs(*, selectable_only: bool = False) -> tuple[str, ...]:
    return tuple(s.slug for s in all_carriers(selectable_only=selectable_only))


def get_spec(slug: str | None) -> CarrierSpec | None:
    """Look up a spec, or None. Case- and whitespace-tolerant."""
    if not slug:
        return None
    return CARRIERS.get(slug.strip().lower())


def default_carrier() -> str:
    for spec in CARRIERS.values():
        if spec.is_default:
            return spec.slug
    raise RuntimeError("No default carrier in the registry")


def catalog(*, include_credentials: bool = True) -> list[dict[str, Any]]:
    """The hub's carrier list. Never touches credentials or the network."""
    return [
        spec.as_dict(include_credentials=include_credentials)
        for spec in all_carriers(selectable_only=True)
    ]


# ── Startup validation ──────────────────────────────────────────────


def validate_registry() -> None:
    """Fail fast on a malformed registry, at import/startup.

    Mirrors ``email_template_registry.validate_registry()``. A bad entry
    should break the process at boot, not at the moment a merchant tries
    to book a shipment.
    """
    if not CARRIERS:
        raise AssertionError("Carrier registry is empty")

    defaults = [s.slug for s in CARRIERS.values() if s.is_default]
    if len(defaults) != 1:
        raise AssertionError(
            f"Registry needs exactly one default carrier, found {defaults}"
        )

    for slug, spec in CARRIERS.items():
        if slug != spec.slug:
            raise AssertionError(f"Registry key '{slug}' != spec.slug '{spec.slug}'")
        if slug != slug.lower() or not slug.isascii():
            raise AssertionError(f"Carrier slug must be lowercase ASCII: '{slug}'")
        if not spec.name_en or not spec.name_ar:
            raise AssertionError(f"Carrier '{slug}' is missing a bilingual name")
        if spec.tier not in ("native", "aggregator", "manual"):
            raise AssertionError(f"Carrier '{slug}' has unknown tier '{spec.tier}'")

        # A tracking template must actually interpolate, or every
        # shopper gets the same dead link.
        if spec.tracking_url_template:
            if "{tracking_number}" not in spec.tracking_url_template:
                raise AssertionError(
                    f"Carrier '{slug}' tracking template has no "
                    f"{{tracking_number}} placeholder"
                )
            if not spec.tracking_url_template.startswith("https://"):
                raise AssertionError(
                    f"Carrier '{slug}' tracking template must be https"
                )

        # Claiming tracking without a way to show it strands the shopper.
        if spec.capabilities.supports_tracking and not spec.tracking_url_template:
            raise AssertionError(
                f"Carrier '{slug}' claims tracking but has no tracking URL"
            )

        # Webhook support is meaningless without somewhere to put the
        # shared secret and something to map the payload onto.
        if spec.capabilities.supports_webhooks:
            if not spec.status_map:
                raise AssertionError(
                    f"Carrier '{slug}' claims webhooks but has an empty status map"
                )
            if not any(f.key == "webhook_secret" for f in spec.credential_fields):
                raise AssertionError(
                    f"Carrier '{slug}' claims webhooks but has no webhook_secret field"
                )
            # Without these the generic route silently ignores every
            # callback from this carrier — a failure mode with no error.
            if spec.webhook_parser_loader is None:
                raise AssertionError(
                    f"Carrier '{slug}' claims webhooks but has no payload parser"
                )
            if not spec.webhook_signature_header:
                raise AssertionError(
                    f"Carrier '{slug}' claims webhooks but has no signature header"
                )

        if spec.verification_operation:
            from src.application.services.carrier_resolver import (
                KNOWN_OPERATIONS,
            )

            if spec.verification_operation not in KNOWN_OPERATIONS:
                raise AssertionError(
                    f"Carrier '{slug}' verifies with unknown operation "
                    f"'{spec.verification_operation}'"
                )

        keys = [f.key for f in spec.credential_fields]
        if len(keys) != len(set(keys)):
            raise AssertionError(f"Carrier '{slug}' has duplicate credential fields")
        for cred in spec.credential_fields:
            if not cred.label_en or not cred.label_ar:
                raise AssertionError(
                    f"Carrier '{slug}' credential '{cred.key}' needs bilingual labels"
                )

        for raw, mapped in spec.status_map.items():
            if raw != raw.upper():
                raise AssertionError(
                    f"Carrier '{slug}' status key '{raw}' must be UPPERCASE — "
                    f"map_status() uppercases before lookup"
                )
            if not isinstance(mapped, ShipmentStatus):
                raise AssertionError(
                    f"Carrier '{slug}' maps '{raw}' to a non-ShipmentStatus"
                )


validate_registry()
