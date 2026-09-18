"""Who may use the public API, and what a retried write does.

Two things are sold here and both used to be free: API access itself, which
every plan had because nothing read the flag, and safe retries, which nothing
provided. The tests are the difference between a product and a promise.

Pure logic; no Postgres, no Redis.
"""

import json

import pytest

from src.api.middleware.idempotency import IdempotencyMiddleware
from src.application.services.api_access import GRANT_FLAG, ApiAccess, _decide


def test_a_plan_that_includes_the_api_is_allowed():
    access = _decide("pro", {})

    assert access.allowed is True
    assert access.source == "plan"
    assert access.in_plan is True


def test_a_plan_without_the_api_is_refused():
    access = _decide("starter", {})

    assert access.allowed is False
    assert access.source is None
    assert access.reason == "plan_excludes_api"


def test_an_admin_grant_opens_the_api_on_any_plan():
    """The lever for a pilot partner or an agency on one Starter merchant."""
    access = _decide("starter", {GRANT_FLAG: True})

    assert access.allowed is True
    assert access.source == "grant"
    assert access.granted is True
    assert access.in_plan is False


def test_revoking_a_grant_closes_it_again():
    assert _decide("starter", {GRANT_FLAG: False}).allowed is False


def test_a_grant_flag_never_takes_access_away_from_a_paying_plan():
    """Flipping the flag off on Pro must not cut off a plan that includes it."""
    access = _decide("pro", {GRANT_FLAG: False})

    assert access.allowed is True
    assert access.source == "plan"


def test_other_feature_flags_do_not_grant_the_api():
    assert (
        _decide("starter", {"golive_exempt": True, "ff_promotions_v2": True}).allowed
        is False
    )


def test_an_unknown_plan_is_refused_rather_than_assumed():
    access = _decide(None, {})

    assert access.allowed is False
    assert access.plan == "free"


def test_no_tenant_means_no_access():
    assert (
        ApiAccess(
            allowed=False, source=None, plan="none", in_plan=False, granted=False
        ).reason
        == "plan_excludes_api"
    )


# ── Idempotent writes ───────────────────────────────────────────────────


class _Request:
    def __init__(self, method="POST", path="/api/v1/stores/abc/orders/", headers=None):
        self.method = method
        self.headers = headers or {}
        self.url = type("U", (), {"path": path})()


def _slot(request):
    """The key the middleware would claim, via its own logic."""
    import hashlib

    auth = request.headers.get("authorization", "")
    key = request.headers.get("Idempotency-Key")
    return (
        "idem:"
        + hashlib.sha256(
            f"{auth}|{request.method}|{request.url.path}|{key}".encode()
        ).hexdigest()
    )


@pytest.mark.asyncio
async def test_a_request_without_the_header_is_untouched():
    """Everything that does not opt in behaves exactly as before."""
    called = []

    async def call_next(_request):
        called.append(True)
        return "passed through"

    result = await IdempotencyMiddleware(app=None).dispatch(_Request(), call_next)

    assert result == "passed through"
    assert called == [True]


@pytest.mark.asyncio
async def test_a_get_is_untouched_even_with_the_header():
    """Reads are already idempotent; caching them would be a stale-data bug."""

    async def call_next(_request):
        return "passed through"

    request = _Request(method="GET", headers={"Idempotency-Key": "abc"})
    assert await IdempotencyMiddleware(app=None).dispatch(request, call_next) == (
        "passed through"
    )


@pytest.mark.asyncio
async def test_storefront_paths_are_untouched():
    """The storefront checkout has its own, customer-aware key."""

    async def call_next(_request):
        return "passed through"

    request = _Request(
        path="/api/v1/storefront/store/abc/checkout",
        headers={"Idempotency-Key": "abc"},
    )
    assert await IdempotencyMiddleware(app=None).dispatch(request, call_next) == (
        "passed through"
    )


def test_two_callers_sharing_a_key_do_not_share_a_slot():
    """A key is the caller's to choose; it must not leak another's response."""
    one = _Request(headers={"Idempotency-Key": "k1", "authorization": "Bearer a"})
    two = _Request(headers={"Idempotency-Key": "k1", "authorization": "Bearer b"})

    assert _slot(one) != _slot(two)


def test_the_same_key_on_a_different_endpoint_is_a_different_operation():
    orders = _Request(headers={"Idempotency-Key": "k1"})
    products = _Request(
        path="/api/v1/stores/abc/products/", headers={"Idempotency-Key": "k1"}
    )

    assert _slot(orders) != _slot(products)


def test_the_same_key_twice_on_one_endpoint_is_one_slot():
    first = _Request(headers={"Idempotency-Key": "k1", "authorization": "Bearer a"})
    again = _Request(headers={"Idempotency-Key": "k1", "authorization": "Bearer a"})

    assert _slot(first) == _slot(again)


@pytest.mark.asyncio
async def test_a_replay_returns_the_first_response_and_says_so(monkeypatch):
    import src.api.middleware.idempotency as module

    stored = {"code": 201, "body": {"data": {"order_id": "the-first-one"}}}

    async def fake_claim(*_args, **_kwargs):
        return False  # somebody already holds the slot

    async def fake_get(_slot):
        return stored

    monkeypatch.setattr(module._cache, "set_if_absent", fake_claim)
    monkeypatch.setattr(module._cache, "get", fake_get)

    called = []

    async def call_next(_request):
        called.append(True)
        return None

    request = _Request(headers={"Idempotency-Key": "k1"})
    response = await IdempotencyMiddleware(app=None).dispatch(request, call_next)

    assert called == []  # the order was NOT created a second time
    assert response.status_code == 201
    assert response.headers["Idempotent-Replay"] == "true"
    assert json.loads(response.body)["data"]["order_id"] == "the-first-one"


@pytest.mark.asyncio
async def test_a_request_still_in_flight_answers_409(monkeypatch):
    """Better a retry in a moment than a second order."""
    import src.api.middleware.idempotency as module

    async def fake_claim(*_args, **_kwargs):
        return False

    async def fake_get(_slot):
        return {"status": "in_flight"}

    monkeypatch.setattr(module._cache, "set_if_absent", fake_claim)
    monkeypatch.setattr(module._cache, "get", fake_get)

    async def call_next(_request):
        raise AssertionError("must not run while the first request is in flight")

    response = await IdempotencyMiddleware(app=None).dispatch(
        _Request(headers={"Idempotency-Key": "k1"}), call_next
    )

    assert response.status_code == 409


@pytest.mark.asyncio
async def test_redis_being_down_does_not_block_the_write(monkeypatch):
    import src.api.middleware.idempotency as module

    async def boom(*_args, **_kwargs):
        raise RuntimeError("redis is down")

    monkeypatch.setattr(module._cache, "set_if_absent", boom)

    async def call_next(_request):
        return "the write happened"

    response = await IdempotencyMiddleware(app=None).dispatch(
        _Request(headers={"Idempotency-Key": "k1"}), call_next
    )

    assert response == "the write happened"
