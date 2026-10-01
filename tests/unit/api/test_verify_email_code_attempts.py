"""The 6-digit email code allows five wrong guesses, then burns the code."""

import pytest
from fastapi import HTTPException

from src.api.v1.routes import auth
from src.api.v1.schemas.public.auth import VerifyEmailCodeRequest


class FakeCache:
    store: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def set_if_absent(self, key, value, expire=None):
        return self.store.setdefault(key, value) == value

    async def increment(self, key, amount=1):
        self.store[key] = self.store.get(key, 0) + amount
        return self.store[key]

    async def delete(self, key):
        return self.store.pop(key, None) is not None


async def test_wrong_codes_count_down_then_lock(monkeypatch):
    FakeCache.store = {"email_verify_code:u1": "123456"}
    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", FakeCache
    )

    async def guess():
        with pytest.raises(HTTPException) as exc:
            await auth.verify_email_code(
                VerifyEmailCodeRequest(code="000000"), user_id="u1", user_repo=None
            )
        return exc.value.detail

    left = [(await guess())["attempts_left"] for _ in range(4)]
    assert left == [4, 3, 2, 1]

    locked = await guess()
    assert locked["code"] == "VERIFICATION_CODE_LOCKED"
    assert "email_verify_code:u1" not in FakeCache.store

    assert (await guess())["code"] == "VERIFICATION_CODE_EXPIRED"
