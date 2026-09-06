"""Tests for the generic carrier webhook route and its parsers (P1.5).

``webhooks/{bosta,jt,mylerz}.py`` were three near-identical files whose
bodies had drifted — only Bosta's mapped ``IN_WAREHOUSE``, and each
decided independently which statuses were terminal. One route now serves
every carrier, with only the payload shape, signature header and status
vocabulary declared per carrier in the registry.

The parsers are total by design: a carrier retrying a body we cannot read
will never succeed, so a malformed payload must be reported rather than
raised.
"""

import pytest

from src.application.services.carrier_registry import carrier_slugs, get_spec
from src.core.entities.shipment import ShipmentStatus
from src.infrastructure.webhooks.carrier_parsers import (
    PARSERS,
    parse_bosta,
    parse_jt,
    parse_mylerz,
)

BOSTA_BODY = {
    "delivery": {
        "trackingNumber": "BST-123",
        "state": {"value": "DELIVERED"},
        "cod": {"amount": 250.0},
        "businessReference": "ORD-1",
    }
}

MYLERZ_BODY = {"Barcode": "MYL-123", "Status": "DELIVERED", "CODAmount": 250.0}

JT_BODY = {"billCode": "JT-123", "scanType": "SIGNED", "codAmount": 250.0}


class TestParsersExtractTheRightFields:
    def test_bosta_reads_the_nested_delivery_object(self):
        e = parse_bosta(BOSTA_BODY)
        assert e.tracking_number == "BST-123"
        assert e.raw_status == "DELIVERED"
        assert e.cod_amount == 250.0
        assert e.cod_collected is True

    def test_mylerz_reads_pascal_case(self):
        e = parse_mylerz(MYLERZ_BODY)
        assert e.tracking_number == "MYL-123"
        assert e.raw_status == "DELIVERED"

    def test_jt_reads_billcode_and_scantype(self):
        e = parse_jt(JT_BODY)
        assert e.tracking_number == "JT-123"
        assert e.raw_status == "SIGNED"

    @pytest.mark.parametrize(
        ("parser", "body"),
        [
            (parse_mylerz, {"barcode": "x", "status": "DELIVERED"}),
            (parse_jt, {"billcode": "x", "status": "DELIVERED"}),
            (parse_bosta, {"delivery": {"tracking_number": "x", "state": "DELIVERED"}}),
        ],
    )
    def test_alternate_spellings_are_accepted(self, parser, body):
        """Carriers are inconsistent between their docs and what they send."""
        e = parser(body)
        assert e is not None
        assert e.tracking_number == "x"


class TestParsersAreTotal:
    """A body we cannot read must be reported, never raised."""

    @pytest.mark.parametrize("parser", [parse_bosta, parse_mylerz, parse_jt])
    @pytest.mark.parametrize(
        "body",
        [{}, {"unexpected": 1}, {"delivery": None}, {"delivery": "nope"}, None, []],
    )
    def test_unreadable_bodies_return_none(self, parser, body):
        assert parser(body) is None

    @pytest.mark.parametrize("parser", [parse_bosta, parse_mylerz, parse_jt])
    def test_no_tracking_number_returns_none(self, parser):
        assert parser({"status": "DELIVERED"}) is None

    def test_non_numeric_cod_does_not_raise(self):
        e = parse_mylerz({"Barcode": "X", "Status": "DELIVERED", "CODAmount": "abc"})
        assert e.cod_amount is None
        assert e.cod_collected is False

    def test_missing_cod_is_not_collected(self):
        assert (
            parse_mylerz({"Barcode": "X", "Status": "DELIVERED"}).cod_collected is False
        )


class TestRegistryIntegration:
    """parse_webhook must map the status through the carrier's own map."""

    @pytest.mark.parametrize(
        ("slug", "body"),
        [("bosta", BOSTA_BODY), ("mylerz", MYLERZ_BODY), ("jt", JT_BODY)],
    )
    def test_status_is_mapped_to_numu_vocabulary(self, slug, body):
        event = get_spec(slug).parse_webhook(body)
        assert event.status is ShipmentStatus.DELIVERED
        assert event.is_mapped

    def test_jt_signed_maps_to_delivered(self):
        """J&T says SIGNED where the others say DELIVERED."""
        assert get_spec("jt").parse_webhook(JT_BODY).status is ShipmentStatus.DELIVERED

    def test_unknown_status_is_unmapped_not_guessed(self):
        body = {"Barcode": "X", "Status": "TELEPORTED"}
        event = get_spec("mylerz").parse_webhook(body)
        assert event is not None
        assert event.status is None
        assert event.is_mapped is False
        # Raw value is preserved so the gap is visible in logs.
        assert event.raw_status == "TELEPORTED"

    def test_bosta_in_warehouse_maps_everywhere_now(self):
        """Only Bosta's old handler knew this status; it's declared now."""
        body = {"delivery": {"trackingNumber": "X", "state": {"value": "IN_WAREHOUSE"}}}
        assert get_spec("bosta").parse_webhook(body).status is ShipmentStatus.IN_TRANSIT

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_every_webhook_carrier_has_a_parser_and_header(self, slug):
        spec = get_spec(slug)
        if not spec.capabilities.supports_webhooks:
            pytest.skip("no webhooks")
        assert spec.webhook_parser_loader is not None, (
            f"{slug} claims webhooks but has no parser — the generic route "
            f"would silently ignore every callback"
        )
        assert spec.webhook_signature_header, f"{slug} has no signature header"

    @pytest.mark.parametrize("slug", carrier_slugs())
    def test_parser_is_registered_for_every_carrier(self, slug):
        spec = get_spec(slug)
        if spec.capabilities.supports_webhooks:
            assert slug in PARSERS


class TestGenericRoute:
    """The route must never fail a carrier retry it cannot satisfy."""

    @pytest.fixture(scope="class")
    def app(self):
        from fastapi import FastAPI

        from src.api.v1.routes.webhooks.shipping import router

        application = FastAPI()
        application.include_router(router)
        return application

    def test_route_is_registered_for_any_carrier(self, app):
        from fastapi.routing import APIRoute

        paths = {r.path for r in app.routes if isinstance(r, APIRoute)}
        assert "/{carrier}" in paths

    def test_one_route_serves_every_carrier(self, app):
        """The point of the phase: no route file per carrier."""
        from fastapi.routing import APIRoute

        posts = [
            r for r in app.routes if isinstance(r, APIRoute) and "POST" in r.methods
        ]
        assert len(posts) == 1
