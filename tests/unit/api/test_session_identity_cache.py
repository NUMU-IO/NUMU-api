"""Guest identity is looked up on any step, at most once per miss window,
and once found rides every later event from that browser."""

import asyncio
import uuid

from src.api.v1.routes.storefront import tracking

STORE = uuid.uuid4()


class _Cache:
    store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set(self, key, value, expire=None):
        self.store[key] = value


def _run(monkeypatch, step, db_email=None):
    lookups = []

    async def _lookup(user_data, fingerprint, store_id, session):
        lookups.append(step)
        if db_email:
            user_data["email"] = db_email

    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", _Cache
    )
    monkeypatch.setattr(tracking, "_enrich_user_data_from_session", _lookup)
    user = {}
    asyncio.run(tracking._resolve_session_identity(user, "fp-1", STORE, None, step))
    return user, lookups


def test_browsing_looks_up_once_then_remembers_the_miss(monkeypatch):
    _Cache.store = {}
    assert _run(monkeypatch, "page_view")[1] == ["page_view"]
    assert _run(monkeypatch, "product_view")[1] == []  # remembered miss
    assert _run(monkeypatch, "checkout_started")[1] == ["checkout_started"]


def test_found_identity_rides_later_browsing_events(monkeypatch):
    _Cache.store = {}
    _run(monkeypatch, "checkout_started", db_email="a@b.co")
    user, lookups = _run(monkeypatch, "page_view")
    assert user["email"] == "a@b.co"
    assert lookups == []


def test_a_newsletter_email_merges_into_what_is_known(monkeypatch):
    _Cache.store = {f"capi_identity:{STORE}:fp-1": {"phone": "+201000000000"}}
    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", _Cache
    )
    asyncio.run(
        tracking.remember_session_identity(STORE, "fp-1", {"email": "a@b.co", "x": 1})
    )
    assert _Cache.store[f"capi_identity:{STORE}:fp-1"] == {
        "phone": "+201000000000",
        "email": "a@b.co",
    }
