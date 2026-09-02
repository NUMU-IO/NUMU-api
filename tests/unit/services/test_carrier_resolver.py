"""Regression tests for P0 — the wrong-carrier bug.

Before this, every carrier action except create called Bosta
unconditionally: cancelling a Mylerz shipment issued a request to
Bosta's API, and an unrecognised carrier slug silently booked a real
Bosta delivery.

These tests assert on **which provider class** is resolved, not on
response shape — a test that only checks a 200 would still pass while
talking to the wrong carrier. See
``docs/Plans/Shipping/SHIPPING-UNIFIED-LAYER.md`` § P0.
"""

from unittest.mock import AsyncMock, patch

import pytest

from src.application.services.carrier_resolver import (
    DEFAULT_CARRIER,
    SUPPORTED_CARRIERS,
    CarrierCapabilityError,
    UnknownCarrierError,
    capability,
    service_for_carrier,
    service_for_shipment,
    tracking_url_for,
    validate_carrier,
)


class _FakeShipment:
    """Minimal stand-in — the resolver only reads `.carrier`."""

    def __init__(self, carrier: str) -> None:
        self.carrier = carrier


class TestValidateCarrier:
    """An unknown carrier must never resolve to a default."""

    @pytest.mark.parametrize("slug", SUPPORTED_CARRIERS)
    def test_accepts_supported_carriers(self, slug):
        assert validate_carrier(slug) == slug

    def test_normalizes_case_and_whitespace(self):
        assert validate_carrier("  BOSTA ") == "bosta"

    @pytest.mark.parametrize("slug", ["bostaa", "aramex", "shipblu", "", "  "])
    def test_rejects_unknown_carrier(self, slug):
        """The core P0 bug: these used to silently become Bosta."""
        with pytest.raises(UnknownCarrierError):
            validate_carrier(slug)

    def test_rejects_none(self):
        with pytest.raises(UnknownCarrierError):
            validate_carrier(None)

    def test_error_names_the_offending_carrier(self):
        with pytest.raises(UnknownCarrierError) as exc:
            validate_carrier("bostaa")
        assert "bostaa" in str(exc.value)


class TestTrackingUrl:
    """A wrong tracking link is worse than no link."""

    def test_known_carriers_get_their_own_url(self):
        assert "bosta.co" in tracking_url_for("bosta", "ABC123")
        assert "mylerz.com" in tracking_url_for("mylerz", "ABC123")
        assert "jtexpress-eg.com" in tracking_url_for("jt", "ABC123")

    def test_tracking_number_is_interpolated(self):
        assert "ABC123" in tracking_url_for("bosta", "ABC123")

    def test_unknown_carrier_returns_none_not_a_bosta_url(self):
        """Regression: this used to fall back to a Bosta tracking link."""
        result = tracking_url_for("shipblu", "ABC123")
        assert result is None

    def test_missing_tracking_number_returns_none(self):
        assert tracking_url_for("bosta", None) is None
        assert tracking_url_for("bosta", "") is None

    @pytest.mark.parametrize("slug", [s for s in SUPPORTED_CARRIERS if s != "bosta"])
    def test_no_carrier_ever_gets_a_bosta_url(self, slug):
        assert "bosta.co" not in (tracking_url_for(slug, "ABC123") or "")


class TestServiceResolution:
    """The heart of P0: dispatch on the shipment's own carrier."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("slug", "expected_cls"),
        [
            ("bosta", "BostaShippingService"),
            ("mylerz", "MylerzShippingService"),
            ("jt", "JTShippingService"),
        ],
    )
    async def test_resolves_the_matching_provider(self, slug, expected_cls):
        service = await service_for_carrier(slug, {})
        assert type(service).__name__ == expected_cls

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("slug", "expected_cls"),
        [
            ("bosta", "BostaShippingService"),
            ("mylerz", "MylerzShippingService"),
            ("jt", "JTShippingService"),
        ],
    )
    async def test_shipment_resolves_by_its_own_carrier(self, slug, expected_cls):
        """Regression: a Mylerz shipment used to resolve BostaShippingService."""
        service = await service_for_shipment(_FakeShipment(slug), {})
        assert type(service).__name__ == expected_cls

    @pytest.mark.asyncio
    async def test_non_bosta_shipment_never_resolves_bosta(self):
        for slug in ("mylerz", "jt"):
            service = await service_for_shipment(_FakeShipment(slug), {})
            assert "Bosta" not in type(service).__name__

    @pytest.mark.asyncio
    async def test_unknown_carrier_raises_instead_of_defaulting(self):
        """The bug this phase exists to kill."""
        with pytest.raises(UnknownCarrierError):
            await service_for_carrier("bostaa", {})

    @pytest.mark.asyncio
    async def test_unknown_carrier_makes_no_outbound_call(self):
        """Resolution must fail before any provider is constructed."""
        with patch(
            "src.infrastructure.external_services.bosta.shipping_service"
            ".get_bosta_service_for_store",
            new=AsyncMock(),
        ) as bosta_factory:
            with pytest.raises(UnknownCarrierError):
                await service_for_carrier("aramex", {})
            bosta_factory.assert_not_called()

    @pytest.mark.asyncio
    async def test_shipment_with_stale_carrier_raises(self):
        """A persisted shipment naming a carrier we dropped is a 409, not Bosta."""
        with pytest.raises(UnknownCarrierError):
            await service_for_shipment(_FakeShipment("retired_carrier"), {})


class TestCapabilityGuard:
    """Mylerz and J&T implement only the four base methods."""

    @pytest.mark.asyncio
    async def test_bosta_supports_pickups_and_awb(self):
        service = await service_for_carrier("bosta", {})
        assert capability(service, "print_awb", "bosta") is not None
        assert capability(service, "create_pickup", "bosta") is not None

    @pytest.mark.asyncio
    @pytest.mark.parametrize("slug", ["mylerz", "jt"])
    @pytest.mark.parametrize(
        "operation", ["print_awb", "create_pickup", "get_cities", "update_delivery"]
    )
    async def test_unsupported_operations_raise_cleanly(self, slug, operation):
        """Must raise a translatable domain error, not AttributeError."""
        service = await service_for_carrier(slug, {})
        with pytest.raises(CarrierCapabilityError) as exc:
            capability(service, operation, slug)
        assert slug in str(exc.value)
        assert operation in str(exc.value)

    @pytest.mark.asyncio
    async def test_all_carriers_support_the_base_contract(self):
        for slug in SUPPORTED_CARRIERS:
            service = await service_for_carrier(slug, {})
            for operation in ("create_shipment", "track_shipment", "get_rates"):
                assert capability(service, operation, slug) is not None


class TestInvariants:
    """Guardrails so a future edit can't quietly reintroduce the bug."""

    def test_default_carrier_is_supported(self):
        assert DEFAULT_CARRIER in SUPPORTED_CARRIERS

    def test_every_supported_carrier_has_a_tracking_url(self):
        for slug in SUPPORTED_CARRIERS:
            assert tracking_url_for(slug, "X") is not None, (
                f"{slug} has no tracking URL template — it would silently "
                f"return None to shoppers"
            )

    def test_jt_is_supported_here(self):
        """J&T is creatable but missing from the settings route (P1.6).

        If this fails because someone removed J&T, they fixed the
        inconsistency backwards — the settings route is the wrong side.
        """
        assert "jt" in SUPPORTED_CARRIERS
