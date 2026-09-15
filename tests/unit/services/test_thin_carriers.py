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

import base64
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import urlencode

import pytest

from src.application.services.carrier_registry import get_spec
from src.core.entities.shipment import ShipmentStatus
from src.core.interfaces.services.shipping_provider import CarrierApiError
from src.core.interfaces.services.shipping_service import (
    Parcel,
    ShippingAddress,
    parse_carrier_timestamp,
)
from src.infrastructure.external_services.jt.shipping_service import (
    JTShippingService,
    _location_cache,
    md5_base64,
)
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


JT_LOCATIONS = {
    "code": "1",
    "msg": "success",
    "data": [
        {"prov": "القاهرة", "city": "مدينة نصر", "area": "الحي السابع"},
        {"prov": "القاهرة", "city": "مدينة نصر", "area": "الحي العاشر"},
        {"prov": "الإسكندرية", "city": "سيدي جابر", "area": "سموحة"},
    ],
}
SMOUHA = ShippingAddress(
    name="Receiver",
    street1="12 شارع فوزي معاذ",
    street2="سموحة",
    city="سيدي جابر",
    state="Alexandria",
    country="Egypt",
    phone="+201111111111",
)


class TestJT:
    """J&T Express Egypt, JMS open platform (open.jtjms-eg.com)."""

    def setup_method(self):
        _location_cache.clear()
        self.svc = JTShippingService(
            api_account="292508153084379141",
            private_key="pk",
            customer_code="J0086024138",
            customer_password="KO6w29g2",
            sender_phone="+201000000000",
            sender_governorate="Cairo",
            sender_city="مدينة نصر",
            sender_area="الحي العاشر",
            sender_street="1 Abbas El Akkad",
            base_url="https://api.jt.test",
        )

    @staticmethod
    def _posts(*payloads):
        client = MagicMock()
        client.post = AsyncMock(side_effect=[_response(p) for p in payloads])
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=client)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx, client

    def test_business_digest_matches_the_documented_example(self):
        """J&T doc: MD5('KO6w29g2' + 'jadada236t2') uppercased is 4AF43B07…"""
        expected = md5_base64("J0086024138" + "4AF43B0704D20349725BF0BBB64051BB" + "pk")
        assert self.svc._business_digest() == expected

    @pytest.mark.asyncio
    async def test_create_signs_the_request_and_maps_jt_areas(self):
        ctx, client = self._posts(
            JT_LOCATIONS,
            {
                "code": "1",
                "msg": "success",
                "data": {"billCode": "UEG1", "txlogisticId": "ORD-1-AB12CD"},
            },
        )
        with patch("httpx.AsyncClient", return_value=ctx):
            label = await self.svc.create_shipment(
                from_address=CAIRO,
                to_address=SMOUHA,
                parcel=PARCEL,
                rate_id="r",
                cod_amount=25000,
                order_reference="ORD-1",
            )

        assert label.tracking_number == "UEG1"
        assert label.carrier_shipment_id == "ORD-1-AB12CD"
        call = client.post.await_args_list[-1]
        assert call.args[0] == "https://api.jt.test/order/addOrder"
        content = call.kwargs["data"]["bizContent"]
        assert call.kwargs["headers"]["digest"] == md5_base64(content + "pk")
        biz = json.loads(content)
        assert biz["digest"] == self.svc._business_digest()
        assert (
            biz["receiver"]["prov"],
            biz["receiver"]["city"],
            biz["receiver"]["area"],
        ) == (
            "الإسكندرية",
            "سيدي جابر",
            "سموحة",
        )
        assert biz["sender"]["area"] == "الحي العاشر"
        assert biz["receiver"]["mobile"] == "01111111111"
        assert biz["itemsValue"] == "250.00"

    @pytest.mark.asyncio
    async def test_unknown_city_fails_before_booking(self):
        ctx, client = self._posts(JT_LOCATIONS)
        nowhere = ShippingAddress(
            name="R", street1="x", city="Atlantis", state="Giza", country="Egypt"
        )
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(ValueError, match="Atlantis"):
                await self.svc.create_shipment(
                    from_address=CAIRO, to_address=nowhere, parcel=PARCEL
                )
        assert client.post.await_count == 1

    @pytest.mark.asyncio
    async def test_error_code_raises_even_on_http_200(self):
        ctx, _ = self._posts({"code": "145003010", "msg": "API account does not exist"})
        with patch("httpx.AsyncClient", return_value=ctx):
            with pytest.raises(CarrierApiError) as exc:
                await self.svc.get_cities()
        assert exc.value.is_auth_failure

    @pytest.mark.asyncio
    async def test_tracking_reports_the_latest_scan(self):
        ctx, _ = self._posts({
            "code": "1",
            "msg": "success",
            "data": [
                {
                    "billCode": "UEG1",
                    "details": [
                        {"scanTime": "2026-09-09 12:26:25", "scanType": "Signing scan"},
                        {"scanTime": "2026-09-09 12:23:09", "scanType": "Pickup scan"},
                        {"scanTime": "", "scanType": "Sending scan"},
                    ],
                }
            ],
        })
        with patch("httpx.AsyncClient", return_value=ctx):
            info = await self.svc.track_shipment("jt", "UEG1")

        assert info.status == "Signing scan"
        assert get_spec("jt").map_status(info.status) is ShipmentStatus.DELIVERED
        for event in info.events:
            event.timestamp.isoformat()

    @pytest.mark.asyncio
    async def test_empty_tracking_is_unknown_not_an_error(self):
        ctx, _ = self._posts({"code": "1", "msg": "success", "data": []})
        with patch("httpx.AsyncClient", return_value=ctx):
            info = await self.svc.track_shipment("jt", "UEG1")
        assert info.status == "unknown"
        assert info.events == []

    @pytest.mark.asyncio
    async def test_cancel_sends_the_customer_order_number(self):
        ctx, client = self._posts({"code": "1", "msg": "success", "data": {}})
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await self.svc.cancel_shipment("ORD-1-AB12CD") is True
        biz = json.loads(client.post.await_args.kwargs["data"]["bizContent"])
        assert biz["txlogisticId"] == "ORD-1-AB12CD"

    @pytest.mark.asyncio
    async def test_print_awb_decodes_the_pdf(self):
        pdf = b"%PDF-1.4 label"
        ctx, client = self._posts({
            "code": "1",
            "msg": "success",
            "data": {"base64EncodeContent": base64.b64encode(pdf).decode()},
        })
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await self.svc.print_awb("UEG1") == pdf
        biz = json.loads(client.post.await_args.kwargs["data"]["bizContent"])
        assert biz["printSize"] == 0, "J&T sandbox rejects printOrder without printSize"

    @pytest.mark.asyncio
    async def test_account_without_location_permission_still_books(self):
        """Sandbox accounts answer getLocation with 145003012 (no permission)."""
        ctx, client = self._posts(
            {"code": "145003012", "msg": "API account has no interface permissions"},
            {"code": "1", "msg": "success", "data": {"billCode": "UEG2"}},
        )
        with patch("httpx.AsyncClient", return_value=ctx):
            assert await self.svc.get_cities() == []
            label = await self.svc.create_shipment(
                from_address=CAIRO, to_address=SMOUHA, parcel=PARCEL
            )
        assert label.tracking_number == "UEG2"
        biz = json.loads(client.post.await_args.kwargs["data"]["bizContent"])
        assert biz["receiver"]["prov"] == "الإسكندرية"
        assert biz["receiver"]["city"] == "سيدي جابر"

    @pytest.mark.asyncio
    async def test_credentials_are_verified_by_a_cancel_that_finds_nothing(self):
        """Live sandbox 2026-09-15: right creds 999002000, wrong key 145003030,
        wrong password 145003031, wrong account 145003010."""
        ctx, _ = self._posts({"code": "999002000", "msg": "数据未找到"})
        with patch("httpx.AsyncClient", return_value=ctx):
            await self.svc.verify_credentials()

        for code in ("145003030", "145003031", "145003010"):
            ctx, _ = self._posts({"code": code, "msg": "rejected"})
            with patch("httpx.AsyncClient", return_value=ctx):
                with pytest.raises(CarrierApiError) as exc:
                    await self.svc.verify_credentials()
            assert exc.value.is_auth_failure, code

    def test_push_signature_is_checked_against_the_private_key(self):
        content = json.dumps({"billCode": "UEG1", "details": []})
        raw = urlencode({"bizContent": content}).encode()
        assert self.svc.verify_webhook_signature(raw, md5_base64(content + "pk"))
        assert self.svc.verify_webhook_signature(raw, md5_base64(content + "x")) is None
        assert (
            JTShippingService().verify_webhook_signature(raw, md5_base64(content))
            is None
        )

    @pytest.mark.asyncio
    async def test_calls_require_credentials(self):
        with pytest.raises(ValueError):
            await JTShippingService().get_cities()


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
