"""A customer reset code survives five guesses, not a million.

The 6-digit code had no attempt cap and its routes sat in the generic
60/min rate tier, so it could be brute-forced within its 15 minutes.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.api.middleware.rate_limit import _is_auth_endpoint
from src.api.v1.routes.storefront import public

STORE = uuid4()
EMAIL = "shopper@example.com"


class _Cache:
    data: dict = {}

    def __init__(self):
        pass

    async def get(self, key):
        raw = self.data.get(key)
        return None if raw is None else json.loads(raw)

    async def set(self, key, value, expire=None):
        self.data[key] = json.dumps(value)
        return True

    async def increment(self, key, amount=1):
        value = int(json.loads(self.data.get(key, "0"))) + amount
        self.data[key] = json.dumps(value)
        return value

    async def delete(self, key):
        return self.data.pop(key, None) is not None


class _Customers:
    def __init__(self):
        self.customer = SimpleNamespace(
            has_account=True, update_password=lambda h: setattr(self, "pw", h)
        )

    async def get_by_email(self, *_args):
        return self.customer

    async def update(self, customer):
        return customer


class _Passwords:
    def hash_password(self, raw):
        return f"hashed:{raw}"


@pytest.fixture
def cache(monkeypatch):
    _Cache.data = {}
    monkeypatch.setattr(
        "src.infrastructure.cache.redis_cache.RedisCacheService", _Cache
    )
    monkeypatch.setattr(
        "src.infrastructure.external_services.resend.email_service.ResendEmailService",
        lambda: None,
    )
    return _Cache


async def _reset(code: str, customers: _Customers):
    body = public.CustomerResetPasswordRequest(
        email=EMAIL, code=code, new_password="new-password-1"
    )
    return await public.customer_reset_password(
        STORE, body, customer_repo=customers, password_service=_Passwords()
    )


@pytest.mark.asyncio
async def test_code_is_burned_after_five_wrong_guesses(cache):
    customers = _Customers()
    await public.customer_forgot_password(
        STORE,
        public.CustomerForgotPasswordRequest(email=EMAIL),
        customer_repo=customers,
    )
    code = await cache().get(f"customer_password_reset:{STORE}:{EMAIL}")
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    for _ in range(5):
        with pytest.raises(HTTPException):
            await _reset(wrong, customers)

    with pytest.raises(HTTPException):
        await _reset(code, customers)
    assert not hasattr(customers, "pw"), "the real code must be dead by now"


@pytest.mark.asyncio
async def test_right_code_within_the_cap_resets(cache):
    customers = _Customers()
    await public.customer_forgot_password(
        STORE,
        public.CustomerForgotPasswordRequest(email=EMAIL),
        customer_repo=customers,
    )
    code = await cache().get(f"customer_password_reset:{STORE}:{EMAIL}")

    await _reset(code, customers)

    assert customers.pw == "hashed:new-password-1"


@pytest.mark.parametrize("suffix", ["forgot-password", "reset-password"])
def test_reset_routes_are_in_the_auth_rate_tier(suffix):
    assert _is_auth_endpoint(f"/api/v1/storefront/store/{STORE}/auth/{suffix}")
