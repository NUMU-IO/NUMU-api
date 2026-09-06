"""P1 acceptance: add a carrier by registry entry alone.

The phase's stated acceptance criterion:

    Add a throwaway "dummy" carrier to the registry and create, track,
    label, and cancel a shipment through it, **and enable it in store
    settings**, without touching any route file.

That is the test that the layer is real. Everything else in P1 is
plumbing; if this fails, adding a carrier still means editing routes and
the phase did not achieve its point.

The dummy is registered at runtime and removed afterwards, so it never
appears in the real catalog.
"""

from typing import Any

import pytest

from src.application.services import carrier_registry as reg
from src.application.services.carrier_registry import (
    CarrierSpec,
    CredentialField,
    validate_registry,
)
from src.core.entities.shipment import ShipmentStatus
from src.core.interfaces.services.shipping_provider import ProviderCapabilities

SLUG = "dummyexpress"


class DummyProvider:
    """A carrier that supports everything, implemented in ~20 lines.

    Deliberately does NOT subclass anything — the layer must work off the
    registry declaration plus duck-typed methods, exactly as Bosta,
    Mylerz and J&T do today.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[str] = []

    async def create_shipment(self, **kwargs: Any) -> Any:
        self.calls.append("create_shipment")
        return type(
            "Label",
            (),
            {
                "tracking_number": "DUM-1",
                "label_url": "https://example.test/awb",
                "carrier": SLUG,
                "service": "standard",
            },
        )()

    async def track_shipment(self, carrier: str, tracking_number: str) -> Any:
        self.calls.append("track_shipment")
        return type("T", (), {"status": "DELIVERED", "events": []})()

    async def cancel_shipment(self, tracking_number: str) -> bool:
        self.calls.append("cancel_shipment")
        return True

    async def print_awb(self, tracking_number: str) -> bytes:
        self.calls.append("print_awb")
        return b"%PDF-1.4 dummy"

    async def request_return(self, tracking_number: str, reason: str = "") -> str:
        self.calls.append("request_return")
        return "DUM-RET-1"

    async def validate_address(self, address: Any) -> tuple[bool, Any]:
        return True, None


async def _dummy_factory(store_settings: dict) -> DummyProvider:
    return DummyProvider(**(store_settings or {}))


def _dummy_parser():
    from src.core.interfaces.services.shipping_provider import WebhookEvent

    def parse(data: dict) -> Any:
        tracking = data.get("waybill")
        if not tracking:
            return None
        return WebhookEvent(
            tracking_number=str(tracking),
            status=None,
            raw_status=str(data.get("state") or ""),
        )

    return parse


DUMMY_SPEC = CarrierSpec(
    slug=SLUG,
    name_en="Dummy Express",
    name_ar="دمي إكسبريس",
    tier="native",
    capabilities=ProviderCapabilities(
        supports_cod=True,
        supports_labels=True,
        supports_cancel=True,
        supports_return=True,
        supports_tracking=True,
        supports_webhooks=True,
    ),
    factory=_dummy_factory,
    provider_cls_loader=lambda: DummyProvider,
    tracking_url_template="https://dummy.test/track/{tracking_number}",
    credential_fields=(
        CredentialField(key="api_key", label_en="API key", label_ar="مفتاح"),
        CredentialField(
            key="webhook_secret",
            label_en="Webhook secret",
            label_ar="مفتاح الويب هوك",
            required=False,
        ),
    ),
    status_map={
        "DONE": ShipmentStatus.DELIVERED,
        "BACK": ShipmentStatus.RETURNED,
    },
    webhook_signature_header="x-dummy-signature",
    webhook_parser_loader=_dummy_parser,
)


@pytest.fixture
def dummy_carrier():
    """Register the dummy for one test, then remove it."""
    reg.CARRIERS[SLUG] = DUMMY_SPEC
    try:
        yield DUMMY_SPEC
    finally:
        reg.CARRIERS.pop(SLUG, None)


class TestAcceptance:
    """One registry entry must be enough."""

    def test_registry_still_validates_with_the_new_carrier(self, dummy_carrier):
        validate_registry()

    def test_it_appears_in_the_catalog(self, dummy_carrier):
        from src.application.services.carrier_resolver import carrier_catalog

        entry = next(c for c in carrier_catalog() if c["slug"] == SLUG)
        assert entry["name_ar"] == "دمي إكسبريس"
        assert entry["capabilities"]["supports_labels"] is True
        assert [f["key"] for f in entry["credential_fields"]] == [
            "api_key",
            "webhook_secret",
        ]

    @pytest.mark.asyncio
    async def test_create_track_label_cancel_all_resolve(self, dummy_carrier):
        """The four operations the acceptance criterion names."""
        from src.application.services.carrier_resolver import (
            capability,
            service_for_carrier,
        )

        service = await service_for_carrier(SLUG, {})
        assert isinstance(service, DummyProvider)

        label = await capability(service, "create_shipment", SLUG)()
        assert label.tracking_number == "DUM-1"

        tracking = await capability(service, "track_shipment", SLUG)(SLUG, "DUM-1")
        assert tracking.status == "DELIVERED"

        pdf = await capability(service, "print_awb", SLUG)("DUM-1")
        assert pdf.startswith(b"%PDF")

        assert await capability(service, "cancel_shipment", SLUG)("DUM-1") is True

        assert service.calls == [
            "create_shipment",
            "track_shipment",
            "print_awb",
            "cancel_shipment",
        ]

    @pytest.mark.asyncio
    async def test_a_shipment_resolves_by_its_own_carrier(self, dummy_carrier):
        from src.application.services.carrier_resolver import service_for_shipment

        shipment = type("S", (), {"carrier": SLUG})()
        assert isinstance(await service_for_shipment(shipment, {}), DummyProvider)

    def test_tracking_url_comes_from_the_spec(self, dummy_carrier):
        from src.application.services.carrier_resolver import tracking_url_for

        assert tracking_url_for(SLUG, "DUM-1") == "https://dummy.test/track/DUM-1"

    def test_undeclared_capabilities_are_refused(self, dummy_carrier):
        """It never declared pickups, so the route must 501 not crash."""
        from src.application.services.carrier_resolver import (
            CarrierCapabilityError,
            supports,
        )

        assert supports(SLUG, "create_pickup") is False
        with pytest.raises(CarrierCapabilityError):
            from src.application.services.carrier_resolver import capability

            capability(DummyProvider(), "create_pickup", SLUG)

    def test_it_is_enableable_in_store_settings(self, dummy_carrier):
        """The half the plan originally missed — settings, not just shipments."""
        from src.api.v1.routes.stores.settings import (
            _get_default_shipping_settings,
            shipping_carrier_keys,
        )

        assert SLUG in shipping_carrier_keys()
        defaults = _get_default_shipping_settings()
        assert SLUG in defaults
        assert defaults[SLUG] == {
            "enabled": False,
            "is_configured": False,
            "last_configured": None,
        }

    def test_webhooks_resolve_and_map(self, dummy_carrier):
        from src.application.services.carrier_resolver import map_carrier_status

        event = dummy_carrier.parse_webhook({"waybill": "DUM-1", "state": "DONE"})
        assert event.tracking_number == "DUM-1"
        assert event.status is ShipmentStatus.DELIVERED
        assert map_carrier_status(SLUG, "BACK") is ShipmentStatus.RETURNED

    def test_credentials_use_the_shared_helper(self, dummy_carrier):
        from src.application.services.carrier_credentials import (
            describe_credentials,
            validate_credentials,
        )

        cleaned, missing = validate_credentials(SLUG, {"api_key": " k ", "junk": "x"})
        assert cleaned == {"api_key": "k"}  # unknown key dropped
        assert missing == []

        _, missing = validate_credentials(SLUG, {})
        assert missing == ["api_key"]  # webhook_secret is optional

        described = describe_credentials({}, SLUG)
        assert described["is_configured"] is False
        assert set(described["fields"]) == {"api_key", "webhook_secret"}

    def test_no_route_file_mentions_this_carrier(self, dummy_carrier):
        """The actual criterion: zero route changes.

        If adding a carrier required editing a route, its slug would have
        to appear in one. Nothing outside the test knows this slug exists.
        """
        import pathlib

        root = pathlib.Path(__file__).resolve().parents[2] / "src" / "api"
        hits = [
            p.relative_to(root).as_posix()
            for p in root.rglob("*.py")
            if SLUG in p.read_text(encoding="utf-8")
        ]
        assert hits == [], f"Adding a carrier should touch no route file; found {hits}"


class TestCleanup:
    def test_dummy_is_gone_afterwards(self):
        """The fixture must not leak into the real catalog."""
        assert SLUG not in reg.CARRIERS
        validate_registry()
