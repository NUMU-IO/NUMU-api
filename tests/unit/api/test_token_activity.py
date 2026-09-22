"""The per-token request trail: records token requests, newest first, capped."""

from types import SimpleNamespace

import pytest

from src.api.middleware import token_activity


class _FakeRedis:
    def __init__(self):
        self.lists: dict[str, list[str]] = {}

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

    async def execute(self):
        for op in self.ops:
            op()


def _request(path, pat, query=""):
    return SimpleNamespace(
        state=SimpleNamespace(pat=pat) if pat else SimpleNamespace(),
        method="GET",
        url=SimpleNamespace(path=path, query=query),
        headers={"user-agent": "node"},
        client=SimpleNamespace(host="1.2.3.4"),
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
