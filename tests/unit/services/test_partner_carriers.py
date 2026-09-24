"""Partner shipping apps as carriers: signed calls, graceful rates,
answer validation, tracking pushes scoped to the app's own shipments."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.v1.routes.stores import shipments as shipments_route
from src.api.v1.schemas.tenant.shipment import CarrierEventRequest
from src.application.services import partner_carriers as pc
from src.application.services import shipping_resolver as sr
from src.application.services.shipment_status_sync import order_steps
from src.core.entities.order import OrderStatus
from src.core.entities.shipment import Shipment, ShipmentStatus
from src.core.entities.shipping_rate import RateType, ShippingRate
from src.core.entities.shipping_zone import ShippingZone
from src.core.interfaces.services.shipping_service import Parcel, ShippingAddress

SECRET = "cs_test_secret"
STORE = uuid4()
TENANT = uuid4()


def _partner(**config) -> pc.PartnerCarrier:
    return pc.PartnerCarrier(
        carrier="app:fast-ship",
        store_id=STORE,
        name={"ar": "شحن سريع", "en": "Fast Ship"},
        icon_url=None,
        config={
            "create_shipment_url": "https://partner.example.com/ship",
            "rates_url": "https://partner.example.com/rates",
            **config,
        },
        secret=SECRET,
    )


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(pc, "assert_webhook_target", lambda url: None)
    store: dict = {}

    async def get(key):
        return store.get(key)

    async def set_(key, value, expire=None):
        store[key] = value
        return True

    monkeypatch.setattr(pc, "_cache", SimpleNamespace(get=get, set=set_))


def _serve(monkeypatch, handler):
    seen: list[httpx.Request] = []

    async def wrapped(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return await handler(request)

    monkeypatch.setattr(pc, "_transport", httpx.MockTransport(wrapped))
    return seen


# ─── Signed request ────────────────────────────────────────────────


async def test_create_shipment_is_signed_like_a_webhook(monkeypatch):
    async def ok(request):
        return httpx.Response(
            200,
            json={
                "tracking_number": "FS123",
                "label_url": "https://partner.example.com/l/FS123.pdf",
                "tracking_url": "https://partner.example.com/t/FS123",
            },
        )

    seen = _serve(monkeypatch, ok)
    label = await _partner().create_shipment(
        from_address=ShippingAddress(name="S", street1="x", city="Cairo", country="EG"),
        to_address=ShippingAddress(name="C", street1="y", city="Giza", country="EG"),
        parcel=Parcel(length=1, width=1, height=1, weight=1),
        rate_id="app:fast-ship_standard",
        cod_amount=15000,
        order_reference="ORD-1",
    )

    request = seen[0]
    body = request.content
    ts = request.headers["X-NUMU-Timestamp"]
    expected = hmac.new(
        SECRET.encode(), f"{ts}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert request.headers["X-NUMU-Signature-V1"] == f"t={ts},v1={expected}"
    assert request.headers["X-NUMU-Event"] == "carrier.shipment.create"
    payload = json.loads(body)
    assert payload["service_code"] == "standard"
    assert payload["cod_amount_cents"] == 15000
    assert payload["store_id"] == str(STORE)
    assert label.tracking_number == "FS123"
    assert label.tracking_url == "https://partner.example.com/t/FS123"


async def test_create_shipment_rejects_a_non_https_label(monkeypatch):
    async def bad(request):
        return httpx.Response(
            200, json={"tracking_number": "FS1", "label_url": "http://x.example/l"}
        )

    _serve(monkeypatch, bad)
    with pytest.raises(ValidationError):
        await _partner().create_shipment(
            from_address=ShippingAddress(name="S", street1="x", city="C", country="EG"),
            to_address=ShippingAddress(name="C", street1="y", city="G", country="EG"),
            parcel=Parcel(length=1, width=1, height=1, weight=1),
            rate_id="app:fast-ship_standard",
        )


# ─── Checkout rates ────────────────────────────────────────────────


def _zone() -> ShippingZone:
    return ShippingZone(
        id=uuid4(),
        tenant_id=TENANT,
        store_id=STORE,
        name="Cairo",
        governorate_codes=["EG-C"],
        cod_enabled=True,
        cod_fee_cents=0,
        is_active=True,
        estimated_days_min=1,
        estimated_days_max=3,
    )


def _rate(zone, rate_type, config, label) -> ShippingRate:
    return ShippingRate(
        id=uuid4(),
        tenant_id=TENANT,
        zone_id=zone.id,
        rate_type=rate_type,
        label=label,
        config=config,
        is_active=True,
        sort_order=0,
    )


class _Repo:
    def __init__(self, zone, rates):
        self.zone, self.rates = zone, rates
        self.session = None

    async def get_zone_for_governorate(self, store_id, code):
        return self.zone

    async def list_rates_by_zone(self, zone_id, include_inactive=False):
        return self.rates

    async def get_rate(self, rate_id):
        return next((r for r in self.rates if r.id == rate_id), None)

    async def get_zone(self, zone_id):
        return self.zone


def _resolver(monkeypatch, with_flat=True):
    zone = _zone()
    app_rate = _rate(
        zone,
        RateType.CARRIER_API,
        {"carrier": "app:fast-ship", "service_code": "standard"},
        "Fast Ship",
    )
    rates = [app_rate]
    if with_flat:
        rates.append(_rate(zone, RateType.FLAT, {"amount_cents": 5000}, "Flat"))

    async def load(db, store_id, carrier):
        return _partner() if carrier == "app:fast-ship" else None

    monkeypatch.setattr(pc, "load_partner_carrier", load)
    return sr.ShippingResolver(_Repo(zone, rates)), app_rate


async def _options(resolver):
    return await resolver.resolve_options(
        store_id=STORE,
        governorate_code="EG-C",
        cart_subtotal_cents=20000,
        cart_weight_g=500,
    )


async def test_live_rates_merge_with_zone_rates(monkeypatch):
    async def quote(request):
        return httpx.Response(
            200,
            json={
                "rates": [
                    {
                        "service_code": "standard",
                        "amount_cents": 4200,
                        "days_min": 1,
                        "days_max": 2,
                    }
                ]
            },
        )

    seen = _serve(monkeypatch, quote)
    resolver, app_rate = _resolver(monkeypatch)
    out = await _options(resolver)

    by_label = {o.label: o.amount_cents for o in out.options}
    assert by_label == {"Fast Ship": 4200, "Flat": 5000}
    assert seen[0].headers["X-NUMU-Event"] == "carrier.rates"

    again = await resolver.resolve_one(
        store_id=STORE,
        rate_id=app_rate.id,
        governorate_code="EG-C",
        cart_subtotal_cents=20000,
        cart_weight_g=500,
    )
    assert again.amount_cents == 4200
    assert len(seen) == 1, "the checkout re-check is served from the cart cache"


async def test_timeout_omits_the_carrier_not_the_checkout(monkeypatch):
    async def slow(request):
        await asyncio.sleep(1)
        return httpx.Response(200, json={"rates": []})

    monkeypatch.setattr(pc, "RATES_TIMEOUT", 0.05)
    _serve(monkeypatch, slow)
    resolver, _ = _resolver(monkeypatch)
    out = await _options(resolver)
    assert [o.label for o in out.options] == ["Flat"]


async def test_schema_invalid_rates_are_rejected(monkeypatch):
    async def invalid(request):
        return httpx.Response(
            200, json={"rates": [{"service_code": "standard", "amount_cents": -1}]}
        )

    _serve(monkeypatch, invalid)
    resolver, _ = _resolver(monkeypatch)
    out = await _options(resolver)
    assert [o.label for o in out.options] == ["Flat"]


async def test_omitted_app_rate_never_becomes_free_shipping(monkeypatch):
    async def down(request):
        return httpx.Response(503)

    _serve(monkeypatch, down)
    resolver, _ = _resolver(monkeypatch, with_flat=False)
    out = await _options(resolver)
    assert out.options == []


# ─── Tracking push ─────────────────────────────────────────────────


class _Shipments:
    session = None

    def __init__(self, shipments):
        self.shipments = shipments

    async def get_for_carrier_for_update(self, store_id, carrier, tracking):
        return next(
            (
                s
                for s in self.shipments
                if s.store_id == store_id
                and s.carrier == carrier
                and s.tracking_number == tracking
            ),
            None,
        )

    async def update(self, shipment):
        return shipment


def _shipment(carrier: str) -> Shipment:
    return Shipment(
        store_id=STORE,
        tenant_id=TENANT,
        order_id=uuid4(),
        carrier=carrier,
        tracking_number="FS123",
        status=ShipmentStatus.CREATED,
        cod_amount=15000,
    )


def _request(app_slug):
    return SimpleNamespace(state=SimpleNamespace(pat={"app_slug": app_slug}))


@pytest.fixture
def calls(monkeypatch):
    seen: dict = {}

    async def apply(**kwargs):
        seen["apply"] = kwargs
        kwargs["shipment"].update_status(kwargs["status"])
        return kwargs["status"]

    async def sync(session, **kwargs):
        seen["sync"] = kwargs
        return []

    monkeypatch.setattr(shipments_route, "apply_carrier_status", apply)
    monkeypatch.setattr(shipments_route, "sync_order_status", sync)
    return seen


async def test_push_flows_through_the_shared_status_path(calls):
    shipment = _shipment("app:fast-ship")
    store = SimpleNamespace(id=STORE, owner_id=uuid4())
    out = await shipments_route.push_carrier_event(
        CarrierEventRequest(
            tracking_number="FS123",
            status=ShipmentStatus.DELIVERED,
            label_url="https://partner.example.com/l.pdf",
            cod_collected=True,
        ),
        _request("fast-ship"),
        store,
        _Shipments([shipment]),
    )
    assert calls["apply"]["carrier"] == "app:fast-ship"
    assert calls["apply"]["status"] is ShipmentStatus.DELIVERED
    assert calls["sync"]["order_id"] == shipment.order_id
    assert calls["sync"]["status"] is ShipmentStatus.DELIVERED
    assert out.data.awb_url == "https://partner.example.com/l.pdf"
    assert out.data.cod_collected is True


async def test_push_cannot_touch_another_carriers_shipment(calls):
    store = SimpleNamespace(id=STORE, owner_id=uuid4())
    body = CarrierEventRequest(tracking_number="FS123", status="delivered")
    for carrier in ("app:other-ship", "bosta"):
        with pytest.raises(HTTPException) as exc:
            await shipments_route.push_carrier_event(
                body, _request("fast-ship"), store, _Shipments([_shipment(carrier)])
            )
        assert exc.value.status_code == 404
    assert "apply" not in calls


async def test_push_needs_an_app_token(calls):
    store = SimpleNamespace(id=STORE, owner_id=uuid4())
    with pytest.raises(HTTPException) as exc:
        await shipments_route.push_carrier_event(
            CarrierEventRequest(tracking_number="FS123", status="delivered"),
            SimpleNamespace(state=SimpleNamespace()),
            store,
            _Shipments([_shipment("app:fast-ship")]),
        )
    assert exc.value.status_code == 403


def test_order_steps_follow_the_courier():
    def order(status, cancellable=False):
        return SimpleNamespace(status=status, can_be_cancelled=cancellable)

    assert order_steps(order(OrderStatus.CONFIRMED), ShipmentStatus.PICKED_UP) == [
        OrderStatus.PROCESSING,
        OrderStatus.SHIPPED,
    ]
    assert order_steps(order(OrderStatus.SHIPPED), ShipmentStatus.DELIVERED) == [
        OrderStatus.DELIVERED
    ]
    assert order_steps(order(OrderStatus.SHIPPED), ShipmentStatus.RETURNED) == [
        OrderStatus.RETURNED
    ]
    assert order_steps(
        order(OrderStatus.CONFIRMED, cancellable=True), ShipmentStatus.CANCELLED
    ) == [OrderStatus.CANCELLED]
    assert order_steps(order(OrderStatus.DELIVERED), ShipmentStatus.IN_TRANSIT) == []
