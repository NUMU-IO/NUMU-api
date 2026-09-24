"""The per-token request trail: records token requests, newest first, capped.

Partner App requests also reach the app's own log: no IP, user agent or query,
a 429 from the rate limiter is still attributed, a Redis failure never reaches
the request, and entries past the retention window are trimmed.
"""

import json
import time
from types import SimpleNamespace

import pytest

from src.api.middleware import token_activity


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}
        self.hashes: dict[str, dict[str, int]] = {}

    def pipeline(self, transaction=False):
        return _FakePipe(self)

    async def lrange(self, key, start, stop):
        return self.lists.get(key, [])[start : stop + 1]


class _FakePipe:
    def __init__(self, redis):
        self.redis, self.ops = redis, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def lpush(self, key, value):
        self.ops.append(lambda: self.redis.lists.setdefault(key, []).insert(0, value))

    def ltrim(self, key, start, stop):
        self.ops.append(
            lambda: self.redis.lists.__setitem__(
                key, self.redis.lists[key][start : stop + 1]
            )
        )

    def expire(self, key, seconds):
        pass

    def hincrby(self, key, field, n):
        def op():
            h = self.redis.hashes.setdefault(key, {})
            h[field] = h.get(field, 0) + n

        self.ops.append(op)

    async def execute(self):
        for op in self.ops:
            op()


def _request(path, pat, query="", token="", route=None):
    return SimpleNamespace(
        state=SimpleNamespace(pat=pat) if pat else SimpleNamespace(),
        method="GET",
        url=SimpleNamespace(path=path, query=query),
        headers={"user-agent": "node", "authorization": f"Bearer {token}"},
        client=SimpleNamespace(host="1.2.3.4"),
        scope={"route": SimpleNamespace(path=route)} if route else {},
    )


@pytest.fixture
def fake(monkeypatch):
    redis = _FakeRedis()

    async def client():
        return redis

    monkeypatch.setattr(
        token_activity, "_get_cache", lambda: SimpleNamespace(_get_client=client)
    )
    monkeypatch.setattr(token_activity, "_get_client_ip", lambda r: r.client.host)
    monkeypatch.setattr(token_activity, "KEEP", 3)
    return redis


async def test_records_token_requests_newest_first_and_capped(fake):
    pat = {"token_id": "t1"}
    for i in range(5):
        await token_activity.record_token_request(_request(f"/p{i}", pat), 200, 1.0)
    await token_activity.record_token_request(
        _request("/api/v1/stores/other", pat, "q=1"), 403, 2.0
    )

    trail = await token_activity.recent_requests("t1")

    assert [e["path"] for e in trail] == ["/api/v1/stores/other?q=1", "/p4", "/p3"]
    assert trail[0]["status"] == 403
    assert trail[0]["ip"] == "1.2.3.4"


async def test_ignores_requests_without_a_token(fake):
    await token_activity.record_token_request(_request("/x", None), 200, 1.0)
    assert fake.lists == {}


APP = {"token_id": "t9", "app_id": "a1", "store_id": "s1"}


async def test_app_requests_reach_the_app_log_without_pii(fake):
    await token_activity.record_token_request(
        _request(
            "/api/v1/stores/s1/orders/o1",
            APP,
            "phone=010",
            token="numu_app_x",
            route="/api/v1/stores/{store_id}/orders/{order_id}",
        ),
        404,
        30.0,
    )

    (raw,) = fake.lists["app_api_log:a1"]
    assert "010" not in raw and "1.2.3.4" not in raw and "node" not in raw
    entry = json.loads(raw)
    assert (entry["r"], entry["s"], entry["st"]) == (
        "/api/v1/stores/{store_id}/orders/{order_id}",
        404,
        "s1",
    )
    (counters,) = fake.hashes.values()
    assert counters == {"n": 1, "4xx": 1, "b1": 1}


async def test_a_rate_limited_app_request_is_still_attributed(fake):
    await token_activity.record_token_request(
        _request("/p", APP, token="numu_app_y"), 200, 1.0
    )
    await token_activity.record_token_request(
        _request("/p", None, token="numu_app_y"), 429, 1.0
    )
    await token_activity.record_token_request(
        _request("/p", None, token="numu_app_other"), 429, 1.0
    )

    assert [json.loads(e)["s"] for e in fake.lists["app_api_log:a1"]] == [429, 200]
    assert list(fake.hashes.values())[0]["429"] == 1


async def test_a_redis_failure_never_reaches_the_request(monkeypatch):
    async def broken():
        raise ConnectionError("redis down")

    monkeypatch.setattr(
        token_activity, "_get_cache", lambda: SimpleNamespace(_get_client=broken)
    )
    await token_activity.record_token_request(
        _request("/p", APP, token="numu_app_z"), 200, 1.0
    )


def test_p95_reads_the_bucket_edge():
    assert token_activity.p95_from_buckets({}) is None
    assert token_activity.p95_from_buckets({"b0": 95, "b3": 5}) == 25
    assert token_activity.p95_from_buckets({"b0": 90, "b3": 10}) == 250
    assert token_activity.p95_from_buckets({"b9": 1}) == 10000


class _TrimRedis:
    def __init__(self, lists):
        self.lists = lists

    async def scan_iter(self, match, count):
        for key in list(self.lists):
            if key.startswith(match.rstrip("*")):
                yield key

    async def llen(self, key):
        return len(self.lists[key])

    async def lindex(self, key, i):
        return self.lists[key][i]

    async def ltrim(self, key, start, stop):
        self.lists[key] = self.lists[key][start : stop + 1]

    async def delete(self, key):
        del self.lists[key]


async def test_retention_drops_entries_older_than_14_days():
    now = time.time()
    day = 86400

    def entries(*ages):
        return [json.dumps({"t": now - a * day}) for a in ages]

    redis = _TrimRedis({
        "app_api_log:a": entries(0, 1, 13, 15, 20),
        "app_api_log:b": entries(15, 30),
        "app_api_log:c": entries(0, 2),
        "api_token_log:t": entries(40),
    })

    assert await token_activity.trim_app_logs(redis, now) == 4
    assert len(redis.lists["app_api_log:a"]) == 3
    assert "app_api_log:b" not in redis.lists
    assert len(redis.lists["app_api_log:c"]) == 2
    assert len(redis.lists["api_token_log:t"]) == 1
