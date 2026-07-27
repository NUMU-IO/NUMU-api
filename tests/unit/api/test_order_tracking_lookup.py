"""Unit tests for the guest order-lookup endpoint (POST /track/lookup).

The security contract is the point of this suite: every miss — unknown
order number, wrong phone, wrong email, order in another store — must be
the SAME 404, because order numbers are short and sequential and any
distinguishable response turns them into an enumeration oracle.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse

from src.api.middleware.rate_limit import (
    TRACK_LOOKUP_PER_ORDER_PER_MINUTE,
    TRACK_LOOKUP_PER_STORE_PER_MINUTE,
    _get_client_ip,
    enforce_track_lookup_budgets,
    rate_limit_exceeded_response,
)
from src.api.v1.routes.storefront.order_tracking import (
    OrderLookupRequest,
    _normalise_order_number,
    _phone_key,
    lookup_order_for_tracking,
)
from src.config import settings
from src.core.entities.order import FulfillmentStatus, OrderStatus, PaymentStatus


def _order(*, store_id, customer_id, phone="+201098433918"):
    return SimpleNamespace(
        id=uuid4(),
        store_id=store_id,
        customer_id=customer_id,
        order_number="ORD-1042",
        status=OrderStatus.SHIPPED,
        payment_status=PaymentStatus.PAID,
        fulfillment_status=FulfillmentStatus.FULFILLED,
        currency="EGP",
        subtotal=25000,
        shipping_cost=5000,
        tax_amount=0,
        discount_amount=0,
        total=30000,
        payment_method="cod",
        tracking_number="BOSTA-77",
        tracking_url=None,
        shipping_method="Standard Shipping",
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        paid_at=None,
        fulfilled_at=None,
        shipped_at=None,
        delivered_at=None,
        cancelled_at=None,
        line_items=[
            SimpleNamespace(
                product_id=uuid4(),
                product_name="Abaya",
                quantity=1,
                unit_price=25000,
            ),
        ],
        shipping_address=SimpleNamespace(
            first_name="Sara",
            last_name="A",
            phone=phone,
            city="Cairo",
            state="Cairo",
            country="EG",
        ),
    )


def _store(store_id):
    return SimpleNamespace(
        id=store_id,
        name="Vionne",
        subdomain="vionne",
        custom_domain=None,
        logo_url=None,
    )


def _repos(*, order, store, customer=None):
    """Repos wired so `get_by_order_number` only answers for the store it
    was seeded with — mirroring the real WHERE store_id AND order_number."""
    order_repo = AsyncMock()

    async def _by_number(store_id, order_number):
        if order is None:
            return None
        if store_id != order.store_id or order_number != order.order_number:
            return None
        return order

    order_repo.get_by_order_number = AsyncMock(side_effect=_by_number)
    store_repo = AsyncMock()
    store_repo.get_by_id = AsyncMock(return_value=store)
    product_repo = AsyncMock()
    product_repo.get_by_ids = AsyncMock(return_value=[])
    customer_repo = AsyncMock()
    customer_repo.get_by_id = AsyncMock(return_value=customer)
    return order_repo, store_repo, product_repo, customer_repo


async def _lookup(sid, payload, repos):
    order_repo, store_repo, product_repo, customer_repo = repos
    return await lookup_order_for_tracking(
        sid, payload, order_repo, store_repo, product_repo, customer_repo
    )


# ------------------------------------------------------------------ #
# Helpers
# ------------------------------------------------------------------ #


class TestPhoneKey:
    """Every way an Egyptian customer writes their own number must
    collapse to one comparable form — otherwise the ownership check 404s
    a legitimate customer."""

    def test_egyptian_forms_all_collapse(self):
        canonical = "01098433918"
        for raw in (
            "+201098433918",
            "00201098433918",
            "201098433918",
            "01098433918",
            "1098433918",
            "+20 109 843 3918",
            "010-9843-3918",
        ):
            assert _phone_key(raw) == canonical, raw

    def test_different_numbers_stay_different(self):
        assert _phone_key("+201098433918") != _phone_key("+201098433919")

    def test_blank_and_none_are_none(self):
        assert _phone_key(None) is None
        assert _phone_key("   ") is None
        assert _phone_key("--") is None

    def test_non_egyptian_number_is_self_consistent(self):
        assert _phone_key("+966501234567") == _phone_key("00966501234567")


class TestRateLimitTier:
    """The endpoint must resolve to its own strict middleware tier. Left on
    the general anon tier (60/min) an attacker gets ~86k guesses a day
    against a short, sequential number space."""

    def test_mounted_path_matches_the_strict_matcher(self):
        from src.api.middleware.rate_limit import _is_track_beacon, _is_track_lookup

        path = f"/api/v1/storefront/store/{uuid4()}/track/lookup"
        assert _is_track_lookup(path)
        # And must NOT be swept into the noisy 600/min analytics beacon tier.
        assert not _is_track_beacon(path)

    def test_beacon_path_does_not_match_the_lookup_matcher(self):
        from src.api.middleware.rate_limit import _is_track_lookup

        assert not _is_track_lookup(f"/api/v1/storefront/store/{uuid4()}/track")


class TestOrderNumberNormalisation:
    def test_hash_prefix_and_whitespace_stripped(self):
        assert _normalise_order_number("  #ORD-1042 ") == "ORD-1042"
        assert _normalise_order_number("# ORD-1042") == "ORD-1042"
        assert _normalise_order_number("ORD-1042") == "ORD-1042"


class TestRequestValidation:
    """422 (a pydantic ValidationError on the body model) when the caller
    supplies both keys or neither — one key must actually authorise."""

    def test_phone_only_ok(self):
        assert OrderLookupRequest(order_number="ORD-1", phone="0100").phone == "0100"

    def test_email_only_ok(self):
        payload = OrderLookupRequest(order_number="ORD-1", email="a@b.com")
        assert payload.email == "a@b.com"
        assert payload.phone is None

    def test_both_keys_rejected(self):
        with pytest.raises(ValidationError):
            OrderLookupRequest(order_number="ORD-1", phone="0100", email="a@b.com")

    def test_neither_key_rejected(self):
        with pytest.raises(ValidationError):
            OrderLookupRequest(order_number="ORD-1")

    def test_blank_key_counts_as_absent(self):
        """A form field the customer left untouched posts as "" — treating
        that as "supplied" would skip verification entirely."""
        with pytest.raises(ValidationError):
            OrderLookupRequest(order_number="ORD-1", phone="  ", email="")


# ------------------------------------------------------------------ #
# Endpoint
# ------------------------------------------------------------------ #


@pytest.mark.asyncio
async def test_phone_match_returns_tracking_payload_with_order_id():
    sid, cid = uuid4(), uuid4()
    order = _order(store_id=sid, customer_id=cid)
    repos = _repos(order=order, store=_store(sid))

    resp = await _lookup(
        sid,
        OrderLookupRequest(order_number="#ORD-1042", phone="01098433918"),
        repos,
    )

    assert resp.data.order_id == str(order.id)
    assert resp.data.order_number == "ORD-1042"
    assert resp.data.status == "shipped"
    assert resp.data.total == 30000
    assert resp.data.tracking_number == "BOSTA-77"
    assert resp.data.store.subdomain == "vionne"
    # Sanitised: the phone we verified against is never echoed back.
    assert resp.data.shipping_address.city == "Cairo"
    assert not hasattr(resp.data.shipping_address, "phone")


@pytest.mark.asyncio
async def test_email_match_returns_tracking_payload():
    sid, cid = uuid4(), uuid4()
    order = _order(store_id=sid, customer_id=cid)
    customer = SimpleNamespace(id=cid, email="Sara@Example.com")
    repos = _repos(order=order, store=_store(sid), customer=customer)

    resp = await _lookup(
        sid,
        OrderLookupRequest(order_number="ORD-1042", email="  SARA@example.COM "),
        repos,
    )

    assert resp.data.order_id == str(order.id)


@pytest.mark.asyncio
async def test_unknown_order_number_is_404():
    sid = uuid4()
    order = _order(store_id=sid, customer_id=uuid4())
    repos = _repos(order=order, store=_store(sid))

    with pytest.raises(HTTPException) as ei:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-9999", phone="01098433918"),
            repos,
        )
    assert ei.value.status_code == 404
    assert ei.value.detail == "Order not found."


@pytest.mark.asyncio
async def test_wrong_phone_is_the_same_404_as_unknown_order():
    """The enumeration guard: a real order number with the wrong phone must
    be indistinguishable from a number that doesn't exist at all."""
    sid = uuid4()
    order = _order(store_id=sid, customer_id=uuid4())
    repos = _repos(order=order, store=_store(sid))

    with pytest.raises(HTTPException) as wrong_key:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-1042", phone="01111111111"),
            repos,
        )
    with pytest.raises(HTTPException) as unknown:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-0001", phone="01098433918"),
            repos,
        )

    assert wrong_key.value.status_code == unknown.value.status_code == 404
    assert wrong_key.value.detail == unknown.value.detail


@pytest.mark.asyncio
async def test_wrong_email_is_404():
    sid, cid = uuid4(), uuid4()
    order = _order(store_id=sid, customer_id=cid)
    customer = SimpleNamespace(id=cid, email="sara@example.com")
    repos = _repos(order=order, store=_store(sid), customer=customer)

    with pytest.raises(HTTPException) as ei:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-1042", email="attacker@evil.com"),
            repos,
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_order_without_a_stored_phone_cannot_be_unlocked_by_phone():
    """A blank stored phone must not compare equal to a blank supplied one
    (the request model already rejects blanks, but the key comparison is
    the last line of defence if that ever loosens)."""
    sid = uuid4()
    order = _order(store_id=sid, customer_id=uuid4(), phone=None)
    repos = _repos(order=order, store=_store(sid))

    with pytest.raises(HTTPException) as ei:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-1042", phone="01098433918"),
            repos,
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_another_stores_order_number_is_404():
    """store_id from the path is authoritative — the repo query is scoped
    to it, so a neighbouring tenant's order number resolves to nothing."""
    sid = uuid4()
    order = _order(store_id=uuid4(), customer_id=uuid4())  # belongs elsewhere
    repos = _repos(order=order, store=_store(sid))

    with pytest.raises(HTTPException) as ei:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-1042", phone="01098433918"),
            repos,
        )
    assert ei.value.status_code == 404


@pytest.mark.asyncio
async def test_unknown_store_is_404():
    sid = uuid4()
    repos = _repos(order=None, store=None)

    with pytest.raises(HTTPException) as ei:
        await _lookup(
            sid,
            OrderLookupRequest(order_number="ORD-1042", phone="01098433918"),
            repos,
        )
    assert ei.value.status_code == 404


# ------------------------------------------------------------------ #
# Content-keyed budgets
# ------------------------------------------------------------------ #
#
# The per-IP tier is only as trustworthy as X-Forwarded-For. These budgets key
# on the store and the order number instead, so no header rotates out of them.


class _FakeRedis:
    """Only the two commands the limiter uses, so these tests exercise the real
    key building and window arithmetic rather than a stubbed-out limiter."""

    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        if self.fail:
            raise ConnectionError("redis unavailable")
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.ttls[key] = ttl


class _FakeCache:
    def __init__(self, redis: _FakeRedis):
        self._redis = redis

    async def _get_client(self) -> _FakeRedis:
        return self._redis


def _use_fake_redis(monkeypatch, *, fail: bool = False) -> _FakeRedis:
    """Point the limiter's module-level cache at an in-memory Redis.

    Also forces ``rate_limit_enabled`` on: a developer's .env may switch it
    off, and these tests are about what happens when it's on.
    """
    from src.api.middleware import rate_limit

    redis = _FakeRedis(fail=fail)
    monkeypatch.setattr(rate_limit, "_cache", _FakeCache(redis))
    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    return redis


@pytest.mark.asyncio
async def test_per_order_budget_throttles_the_attempt_after_the_allowance(monkeypatch):
    """Grinding phone numbers against one known order number is the attack the
    per-order budget exists for."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()
    order = _order(store_id=sid, customer_id=uuid4())
    repos = _repos(order=order, store=_store(sid))
    payload = OrderLookupRequest(order_number="ORD-1042", phone="01098433918")

    for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE):
        ok = await _lookup(sid, payload, repos)
        assert ok.data.order_id == str(order.id)

    throttled = await _lookup(sid, payload, repos)
    assert isinstance(throttled, JSONResponse)
    assert throttled.status_code == 429


@pytest.mark.asyncio
async def test_throttled_response_has_the_middleware_429_shape(monkeypatch):
    """Clients must not need a second parser for the route-level 429 — it is
    built by the same factory the middleware uses."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()
    repos = _repos(order=_order(store_id=sid, customer_id=uuid4()), store=_store(sid))
    payload = OrderLookupRequest(order_number="ORD-1042", phone="01098433918")

    for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE + 1):
        result = await _lookup(sid, payload, repos)

    assert isinstance(result, JSONResponse)
    body = json.loads(result.body)
    assert body.keys() == json.loads(rate_limit_exceeded_response(0).body).keys()
    assert body["success"] is False
    assert body["code"] == "RATE_LIMIT_EXCEEDED"
    assert isinstance(body["error"], str)
    assert result.headers["Retry-After"] == str(body["details"]["retry_after"])


@pytest.mark.asyncio
async def test_429_is_identical_for_a_real_and_an_unknown_order_number(monkeypatch):
    """The same no-oracle rule the 404 obeys: if the throttle only showed up
    for real order numbers, the 429 itself would confirm existence."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()
    repos = _repos(order=_order(store_id=sid, customer_id=uuid4()), store=_store(sid))

    async def _spend(order_number: str) -> JSONResponse:
        payload = OrderLookupRequest(order_number=order_number, phone="01098433918")
        for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE):
            try:
                await _lookup(sid, payload, repos)
            except HTTPException:
                pass  # a miss is a 404; it still spends the budget
        return await _lookup(sid, payload, repos)

    real = await _spend("ORD-1042")
    unknown = await _spend("ORD-9999")

    assert real.status_code == unknown.status_code == 429
    # `retry_after` counts down to the top of the minute, so it can legitimately
    # differ by a second between the two calls; everything a caller could read
    # existence from must match.
    real_body, unknown_body = json.loads(real.body), json.loads(unknown.body)
    del real_body["details"], unknown_body["details"]
    assert real_body == unknown_body


@pytest.mark.asyncio
async def test_budgets_are_spent_before_any_repository_read(monkeypatch):
    """A throttled attempt must not reach the database — that ordering is what
    keeps the 429 independent of whether the order exists."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()
    order_repo, store_repo, product_repo, customer_repo = _repos(
        order=_order(store_id=sid, customer_id=uuid4()), store=_store(sid)
    )
    repos = (order_repo, store_repo, product_repo, customer_repo)
    payload = OrderLookupRequest(order_number="ORD-1042", phone="01098433918")

    for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE + 1):
        await _lookup(sid, payload, repos)

    assert order_repo.get_by_order_number.await_count == (
        TRACK_LOOKUP_PER_ORDER_PER_MINUTE
    )
    assert store_repo.get_by_id.await_count == TRACK_LOOKUP_PER_ORDER_PER_MINUTE


@pytest.mark.asyncio
async def test_decorated_spellings_share_one_order_budget(monkeypatch):
    """Otherwise a '#' or a change of case buys another full allowance."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()

    for spelling in ("ORD-1042", "#ORD-1042", "  ord-1042 ", "#  ORD-1042", "ord-1042"):
        assert await enforce_track_lookup_budgets(sid, spelling) is None

    throttled = await enforce_track_lookup_budgets(sid, "ORD-1042")
    assert throttled is not None
    assert throttled.status_code == 429


@pytest.mark.asyncio
async def test_per_store_budget_caps_enumeration_across_order_numbers(monkeypatch):
    """Walking the number space gives every guess a fresh per-order budget, so
    the per-store budget is what actually bounds enumeration throughput."""
    _use_fake_redis(monkeypatch)
    sid = uuid4()

    for n in range(TRACK_LOOKUP_PER_STORE_PER_MINUTE):
        assert await enforce_track_lookup_budgets(sid, f"ORD-{n:06d}") is None

    throttled = await enforce_track_lookup_budgets(sid, "ORD-999999")
    assert throttled is not None
    assert throttled.status_code == 429
    # Scoped to the store under attack — a neighbouring tenant is unaffected.
    assert await enforce_track_lookup_budgets(uuid4(), "ORD-000001") is None


@pytest.mark.asyncio
async def test_a_spent_order_budget_does_not_drain_the_store_budget(monkeypatch):
    """One grinder on a single number must not take the form down for every
    other shopper of that store."""
    redis = _use_fake_redis(monkeypatch)
    sid = uuid4()

    for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE * 4):
        await enforce_track_lookup_budgets(sid, "ORD-1042")

    store_keys = [k for k in redis.counts if "track_lookup_store" in k]
    assert len(store_keys) == 1
    assert redis.counts[store_keys[0]] == TRACK_LOOKUP_PER_ORDER_PER_MINUTE


@pytest.mark.asyncio
async def test_order_number_cannot_forge_extra_key_segments(monkeypatch):
    """The number is free-form caller text; interpolating it raw would let a
    crafted value land in (or evict) a bucket that isn't its own."""
    redis = _use_fake_redis(monkeypatch)
    sid = uuid4()

    await enforce_track_lookup_budgets(sid, "ORD-1042")
    await enforce_track_lookup_budgets(sid, "ORD-1042:track_lookup_store:0")

    order_keys = [k for k in redis.counts if "track_lookup_order" in k]
    assert len(order_keys) == 2
    assert all("ORD-1042" not in key for key in order_keys)


@pytest.mark.asyncio
async def test_redis_unavailable_fails_open(monkeypatch):
    """Matching the middleware's policy: a Redis outage must not lock every
    customer out of tracking their own order."""
    _use_fake_redis(monkeypatch, fail=True)
    sid = uuid4()

    for _ in range(TRACK_LOOKUP_PER_ORDER_PER_MINUTE + 3):
        assert await enforce_track_lookup_budgets(sid, "ORD-1042") is None


@pytest.mark.asyncio
async def test_disabled_rate_limiting_spends_nothing(monkeypatch):
    redis = _use_fake_redis(monkeypatch)
    monkeypatch.setattr(settings, "rate_limit_enabled", False)

    assert await enforce_track_lookup_budgets(uuid4(), "ORD-1042") is None
    assert redis.counts == {}


# ------------------------------------------------------------------ #
# Which IP the per-IP tier buckets under
# ------------------------------------------------------------------ #


class TestTrustedProxyIps:
    """`X-Forwarded-For` picks the bucket, so believing it from anyone lets a
    caller shed the per-IP limit one header at a time."""

    @staticmethod
    def _request(*, peer: str | None, headers: dict[str, str] | None = None) -> Request:
        return Request({
            "type": "http",
            "method": "POST",
            "path": "/api/v1/storefront/store/x/track/lookup",
            "query_string": b"",
            "headers": [
                (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
            ],
            "client": (peer, 51234) if peer else None,
        })

    def test_unconfigured_still_trusts_the_header(self, monkeypatch):
        """The default MUST stay today's behaviour. The production edge
        topology is unverified, and 'trust nobody' would bucket every request
        under the load balancer's address — one bucket for the whole
        platform, on every rate-limited endpoint."""
        monkeypatch.setattr(settings, "trusted_proxy_ips", [])
        req = self._request(peer="203.0.113.9", headers={"X-Forwarded-For": "1.2.3.4"})
        assert _get_client_ip(req) == "1.2.3.4"

    def test_configured_ignores_the_header_from_an_untrusted_peer(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/8"])
        req = self._request(
            peer="203.0.113.9",
            headers={"X-Forwarded-For": "1.2.3.4", "X-Real-IP": "5.6.7.8"},
        )
        assert _get_client_ip(req) == "203.0.113.9"

    def test_configured_honours_the_header_from_a_trusted_peer(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/8"])
        req = self._request(
            peer="10.4.1.7", headers={"X-Forwarded-For": "1.2.3.4, 10.4.1.7"}
        )
        assert _get_client_ip(req) == "1.2.3.4"

    def test_a_bare_address_entry_is_honoured(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.4.1.7"])
        req = self._request(peer="10.4.1.7", headers={"X-Forwarded-For": "1.2.3.4"})
        assert _get_client_ip(req) == "1.2.3.4"

    def test_a_wholly_unparseable_list_falls_back_to_trusting(self, monkeypatch):
        """A typo in an ops env var must fail toward availability, not toward
        collapsing the platform onto a single bucket."""
        monkeypatch.setattr(settings, "trusted_proxy_ips", ["not-an-ip"])
        req = self._request(peer="203.0.113.9", headers={"X-Forwarded-For": "1.2.3.4"})
        assert _get_client_ip(req) == "1.2.3.4"

    def test_no_socket_peer_and_untrusted_headers_is_unknown(self, monkeypatch):
        monkeypatch.setattr(settings, "trusted_proxy_ips", ["10.0.0.0/8"])
        req = self._request(peer=None, headers={"X-Forwarded-For": "1.2.3.4"})
        assert _get_client_ip(req) == "unknown"


@pytest.mark.asyncio
async def test_lookup_and_uuid_route_share_one_response_builder():
    """Both endpoints must expose the same field set — proven by feeding
    the same order through both and comparing the payloads."""
    from src.api.v1.routes.storefront.order_tracking import track_order

    sid, cid = uuid4(), uuid4()
    order = _order(store_id=sid, customer_id=cid)
    order_repo, store_repo, product_repo, customer_repo = _repos(
        order=order, store=_store(sid)
    )
    order_repo.get_by_id = AsyncMock(return_value=order)

    via_uuid = await track_order(order.id, order_repo, store_repo, product_repo)
    via_lookup = await lookup_order_for_tracking(
        sid,
        OrderLookupRequest(order_number="ORD-1042", phone="01098433918"),
        order_repo,
        store_repo,
        product_repo,
        customer_repo,
    )

    assert via_uuid.data.model_dump() == via_lookup.data.model_dump()
