"""Shared signup defences — bot and throwaway-inbox checks.

These guarded the demo endpoint only. Registration — the door that mints
a permanent tenant, a subdomain and a DNS record — had neither, so the
checks moved to a shared helper. The tests below pin what makes sharing
safe: the captcha is required per-endpoint, an unconfigured secret skips
it, and a verification that cannot complete is rejected rather than
waved through.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.utils import signup_guard
from src.api.utils.signup_guard import (
    guard_public_signup,
    is_disposable_email,
    verify_turnstile_token,
)


def _req(ip: str | None = "1.2.3.4"):
    return SimpleNamespace(client=SimpleNamespace(host=ip) if ip else None)


def test_disposable_domains_are_matched_case_insensitively():
    assert is_disposable_email("SomeOne@Mailinator.com") is True
    assert is_disposable_email("founder@gmail.com") is False
    # A plus-address at a real provider is a real merchant, not a bot.
    assert is_disposable_email("founder+numu@gmail.com") is False


def test_malformed_address_is_not_treated_as_disposable():
    assert is_disposable_email("no-at-sign") is False


@pytest.mark.asyncio
async def test_turnstile_passes_when_no_secret_is_configured(monkeypatch):
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", None, False)
    assert await verify_turnstile_token(None, None) is True


@pytest.mark.asyncio
async def test_turnstile_fails_closed_on_a_missing_token(monkeypatch):
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", "s3cret", False)
    assert await verify_turnstile_token(None, "1.2.3.4") is False


@pytest.mark.asyncio
async def test_turnstile_fails_closed_when_cloudflare_is_unreachable(monkeypatch):
    """Documents a real trade-off rather than asserting a nicety.

    A verification that cannot complete is rejected, so a Cloudflare
    outage closes whichever door has the captcha armed. That is why
    ``require_turnstile`` is per-endpoint: registration can stay open
    while the demo door carries the check.
    """
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", "s3cret", False)

    class _Boom:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            raise RuntimeError("network down")

        async def __aexit__(self, *a):
            return False

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _Boom)
    assert await verify_turnstile_token("token", "1.2.3.4") is False


@pytest.mark.asyncio
async def test_guard_rejects_a_throwaway_inbox(monkeypatch):
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", None, False)
    with pytest.raises(HTTPException) as exc:
        await guard_public_signup(
            email="bot@mailinator.com", turnstile_token=None, http_request=_req()
        )
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_guard_can_skip_the_captcha_for_register(monkeypatch):
    """Registration runs with require_turnstile=False until the landing
    page ships a token — otherwise every signup 422s the moment a secret
    exists in the environment."""
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", "s3cret", False)

    # Would fail with the captcha required...
    with pytest.raises(HTTPException):
        await guard_public_signup(
            email="founder@gmail.com", turnstile_token=None, http_request=_req()
        )

    # ...and passes without it, while still screening the address.
    await guard_public_signup(
        email="founder@gmail.com",
        turnstile_token=None,
        http_request=_req(),
        require_turnstile=False,
    )


@pytest.mark.asyncio
async def test_guard_handles_a_request_without_a_client(monkeypatch):
    monkeypatch.setattr(signup_guard.settings, "turnstile_secret_key", None, False)
    await guard_public_signup(
        email="founder@gmail.com", turnstile_token=None, http_request=_req(ip=None)
    )
