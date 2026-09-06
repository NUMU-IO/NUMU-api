"""Tests for the Mylerz and J&T providers.

Both shipped with **no tests at all** (defect 13). They are thin — four
methods each — but they book real deliveries and collect real COD, and
the one bug found while writing these was live in production.

**The bug:** both put the carrier's raw timestamp *string* into
``TrackingEvent.timestamp``, which is typed ``datetime``. Dataclasses
don't validate, so it looked fine until the tracking route called
``.isoformat()`` on it — a 500. It stayed hidden while every carrier
action resolved to Bosta (which parses); the moment tracking began
dispatching on the shipment's real carrier, tracking any Mylerz or J&T
shipment started failing.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.core.interfaces.services.shipping_service import (
    Parcel,
    ShippingAddress,
    parse_carrier_timestamp,
)
from src.infrastructure.external_services.jt.shipping_service import JTShippingService
from src.infrastructure.external_services.mylerz.shipping_service import (
    MylerzShippingService,
)

CAIRO = ShippingAddress(
    name="Sender",
    street1="1 Test St",
    city="Cairo",
    country="Egypt",
    phone="+201000000000",
)
ALEX = ShippingAddress(
    name="Receiver",
    street1="2 Test Ave",
    city="Alexandria",
    country="Egypt",
    phone="+201111111111",
)
PARCEL = Parcel(length=30, width=20, height=15, weight=1.0)


def _response(payload, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.text = str(payload)
    return resp


def _client(resp):
    """Patchable async httpx client returning one canned response."""
    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.get = AsyncMock(return_value=resp)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


class TestTimestampParsing:
    """The contract says datetime; carriers send anything."""

    def test_passes_datetime_through(self):
        now = datetime.now(UTC)
        assert parse_carrier_timestamp(now) is now

    @pytest.mark.parametrize(
        "raw",
        [
            "2026-09-02T10:00:00",
            "2026-09-02T10:00:00Z",
            "2026-09-02T10:00:00+02:00",
            "2026-09-02 10:00:00",
            "2026-09-02",
            "02/09/2026 10:00:00",
        ],
    )
    def test_accepts_the_formats_carriers_actually_send(self, raw):
        assert isinstance(parse_carrier_timestamp(raw), datetime)

    @pytest.mark.parametrize("raw", ["", "   ", None, "not a date", {}, []])
    def test_never_raises_on_junk(self, raw):
        """One unparseable entry must not lose the whole tracking history."""
        assert isinstance(parse_carrier_timestamp(raw), datetime)

    def test_accepts_epoch_seconds_and_millis(self):
        secs = parse_carrier_timestamp(1_756_800_000)
        millis = parse_carrier_timestamp(1_756_800_000_000)
        assert secs.year == millis.year == 2025

    def test_result_always_supports_isoformat(self):
        """The exact call the tracking route makes."""
        for raw in ("2026-09-02", "junk", 0, None):
            parse_carrier_timestamp(raw).isoformat()


class TestMylerz:
    def setup_method(self):
        self.svc = MylerzShippingService(
            api_key="k", merchant_id="m", base_url="https://api.mylerz.test"
        )

    @pytest.mark.asyncio
    async def test_create_shipment_returns_the_barcode(self):
        resp = _response({"Barcode": "MYL-1", "AWBUrl": "https://x/awb.pdf"})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            label = await self.svc.create_shipment(
                from_address=CAIRO, to_address=ALEX, parcel=PARCEL, rate_id="r"
            )
        assert label.tracking_number == "MYL-1"
        assert label.carrier == "mylerz"

    @pytest.mark.asyncio
    async def test_cod_is_sent_in_major_units(self):
        """Storage is cents; Mylerz expects EGP. Off-by-100 charges the
        customer 100x — the same class of bug that once stored a 15.00 COD
        fee as 1500.00."""
        resp = _response({"Barcode": "MYL-1"})
        ctx = _client(resp)
        with patch("httpx.AsyncClient", return_value=ctx):
            await self.svc.create_shipment(
                from_address=CAIRO,
                to_address=ALEX,
                parcel=PARCEL,
                rate_id="r",
                cod_amount=25000,  # 250.00 EGP
            )
        body = ctx.__aenter__.return_value.post.await_args.kwargs["json"]
        assert body["CODAmount"] == 250.0
        assert body["PaymentType"] == "COD"

    @pytest.mark.asyncio
    async def test_prepaid_when_no_cod(self):
        ctx = _client(_response({"Barcode": "MYL-1"}))
        with patch("httpx.AsyncClient", return_value=ctx):
            await self.svc.create_shipment(
                from_address=CAIRO, to_address=ALEX, parcel=PARCEL, rate_id="r"
            )
        assert (
            ctx.__aenter__.return_value.post.await_args.kwargs["json"]["PaymentType"]
            == "PREPAID"
        )

    @pytest.mark.asyncio
    async def test_create_raises_on_error_response(self):
        with patch(
            "httpx.AsyncClient", return_value=_client(_response({"e": 1}, status=400))
        ):
            with pytest.raises(ValueError):
                await self.svc.create_shipment(
                    from_address=CAIRO, to_address=ALEX, parcel=PARCEL, rate_id="r"
                )

    @pytest.mark.asyncio
    async def test_tracking_timestamps_are_datetimes(self):
        """Regression: these were raw strings and the route 500'd."""
        resp = _response({
            "CurrentStatus": "DELIVERED",
            "TrackingLogs": [
                {
                    "Status": "PICKED_UP",
                    "Description": "d",
                    "Location": "Cairo",
                    "Date": "2026-09-01 09:00:00",
                },
                {
                    "Status": "DELIVERED",
                    "Description": "d",
                    "Location": "Alex",
                    "Date": "bad-date",
                },
            ],
        })
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            info = await self.svc.track_shipment("mylerz", "MYL-1")

        assert len(info.events) == 2
        for event in info.events:
            assert isinstance(event.timestamp, datetime)
            event.timestamp.isoformat()  # what the route does

    @pytest.mark.asyncio
    async def test_estimated_delivery_is_a_datetime_or_none(self):
        """Same latent 500 — the route calls .isoformat() on this too."""
        resp = _response({
            "CurrentStatus": "IN_TRANSIT",
            "TrackingLogs": [],
            "EstimatedDelivery": "2026-09-05T12:00:00Z",
        })
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            info = await self.svc.track_shipment("mylerz", "MYL-1")
        assert isinstance(info.estimated_delivery, datetime)
        info.estimated_delivery.isoformat()

    @pytest.mark.asyncio
    async def test_missing_estimated_delivery_stays_none(self):
        resp = _response({"CurrentStatus": "IN_TRANSIT", "TrackingLogs": []})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            info = await self.svc.track_shipment("mylerz", "MYL-1")
        assert info.estimated_delivery is None

    @pytest.mark.asyncio
    async def test_tracking_raises_on_error(self):
        with patch(
            "httpx.AsyncClient", return_value=_client(_response({}, status=404))
        ):
            with pytest.raises(ValueError):
                await self.svc.track_shipment("mylerz", "nope")

    def test_headers_require_an_api_key(self):
        with pytest.raises(ValueError):
            MylerzShippingService(api_key="", base_url="https://x")._get_headers()


class TestJT:
    def setup_method(self):
        self.svc = JTShippingService(
            api_key="k", customer_code="c", base_url="https://api.jt.test"
        )

    @pytest.mark.asyncio
    async def test_create_shipment_returns_the_billcode(self):
        resp = _response({"code": "1", "data": {"billCode": "JT-1"}})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            label = await self.svc.create_shipment(
                from_address=CAIRO, to_address=ALEX, parcel=PARCEL, rate_id="r"
            )
        assert label.tracking_number == "JT-1"
        assert label.carrier == "jt"

    @pytest.mark.asyncio
    async def test_cod_switches_paytype_and_uses_major_units(self):
        ctx = _client(_response({"code": "1", "data": {"billCode": "JT-1"}}))
        with patch("httpx.AsyncClient", return_value=ctx):
            await self.svc.create_shipment(
                from_address=CAIRO,
                to_address=ALEX,
                parcel=PARCEL,
                rate_id="r",
                cod_amount=25000,
            )
        body = ctx.__aenter__.return_value.post.await_args.kwargs["json"]
        assert body["payType"] == "CC"
        assert body["goodsValue"] == "250.0"

    @pytest.mark.asyncio
    async def test_api_level_error_raises_even_on_http_200(self):
        """J&T signals failure with code != '1' inside a 200 response."""
        resp = _response({"code": "0", "msg": "bad customer code"})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            with pytest.raises(ValueError, match="bad customer code"):
                await self.svc.create_shipment(
                    from_address=CAIRO, to_address=ALEX, parcel=PARCEL, rate_id="r"
                )

    @pytest.mark.asyncio
    async def test_tracking_timestamps_are_datetimes(self):
        """Regression: these were raw strings and the route 500'd."""
        resp = _response({
            "code": "1",
            "data": [
                {
                    "lastStatus": "SIGNED",
                    "details": [
                        {
                            "scanType": "PICKUP",
                            "desc": "d",
                            "scanCity": "Cairo",
                            "scanTime": "2026-09-01 09:00:00",
                        },
                        {
                            "scanType": "SIGNED",
                            "desc": "d",
                            "scanCity": "Alex",
                            "scanTime": "",
                        },
                    ],
                }
            ],
        })
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            info = await self.svc.track_shipment("jt", "JT-1")

        assert len(info.events) == 2
        for event in info.events:
            assert isinstance(event.timestamp, datetime)
            event.timestamp.isoformat()

    @pytest.mark.asyncio
    async def test_empty_tracking_is_unknown_not_an_error(self):
        resp = _response({"code": "1", "data": []})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            info = await self.svc.track_shipment("jt", "JT-1")
        assert info.status == "unknown"
        assert info.events == []

    def test_headers_require_an_api_key(self):
        with pytest.raises(ValueError):
            JTShippingService(api_key="", base_url="https://x")._get_headers()


class TestNeitherCarrierInventsAPrice:
    """P4: `_default_rates` is gone.

    Both providers used to answer a failed quote with a hardcoded number —
    50 EGP for Mylerz, 45 for J&T — returned as `carrier="mylerz"` /
    `carrier="jt"`. A merchant reading that saw NUMU's guess wearing the
    carrier's name, and at checkout a shopper would have been charged it.

    A carrier that cannot quote returns nothing, and the resolver falls
    back to the merchant's own configured rate — a number they chose.
    """

    @pytest.mark.parametrize(
        ("svc", "carrier"),
        [
            (
                MylerzShippingService(
                    api_key="k", merchant_id="m", base_url="https://x"
                ),
                "mylerz",
            ),
            (
                JTShippingService(api_key="k", customer_code="c", base_url="https://x"),
                "jt",
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_failed_quote_returns_nothing(self, svc, carrier):
        with patch(
            "httpx.AsyncClient", return_value=_client(_response({}, status=500))
        ):
            assert await svc.get_rates(CAIRO, ALEX, PARCEL) == []

    @pytest.mark.parametrize(
        "svc",
        [
            MylerzShippingService(api_key="k", merchant_id="m", base_url="https://x"),
            JTShippingService(api_key="k", customer_code="c", base_url="https://x"),
        ],
    )
    @pytest.mark.asyncio
    async def test_a_transport_failure_returns_nothing(self, svc):
        client = MagicMock()
        client.post = AsyncMock(side_effect=OSError("no route to host"))
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await svc.get_rates(CAIRO, ALEX, PARCEL) == []

    @pytest.mark.asyncio
    async def test_jt_api_level_error_on_http_200_returns_nothing(self):
        """J&T answers 200 with `code != "1"` on failure."""
        svc = JTShippingService(api_key="k", customer_code="c", base_url="https://x")
        resp = _response({"code": "0", "msg": "invalid customer"})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            assert await svc.get_rates(CAIRO, ALEX, PARCEL) == []

    def test_the_fallback_is_gone_from_the_source(self):
        import inspect

        from src.infrastructure.external_services.jt import shipping_service as jt_mod
        from src.infrastructure.external_services.mylerz import (
            shipping_service as my_mod,
        )

        for mod in (jt_mod, my_mod):
            assert "_default_rates" not in inspect.getsource(mod)

    @pytest.mark.parametrize("slug", ["mylerz", "jt"])
    def test_the_registry_agrees_they_cannot_quote(self, slug):
        """The code and the declaration have to say the same thing, or the
        resolver would call `get_rates` and get an empty list at checkout."""
        from src.application.services.carrier_registry import get_spec

        assert get_spec(slug).capabilities.supports_live_rates is False

    @pytest.mark.asyncio
    async def test_a_real_quote_still_comes_through(self):
        """Removing the fallback must not remove the feature."""
        svc = MylerzShippingService(api_key="k", merchant_id="m", base_url="https://x")
        resp = _response({"rates": [{"service_type": "express", "price": 72.5}]})
        with patch("httpx.AsyncClient", return_value=_client(resp)):
            rates = await svc.get_rates(CAIRO, ALEX, PARCEL)
        assert len(rates) == 1
        assert rates[0].amount == 7250  # major units → cents
        assert rates[0].service == "express"
