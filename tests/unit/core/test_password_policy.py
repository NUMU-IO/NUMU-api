"""The password policy: 8 characters minimum, not a known-breached password."""

import pytest

from src.application.services import password_policy
from src.core.exceptions import ValidationError
from src.core.validators.password import validate_password


@pytest.mark.parametrize("password", ["partner1", "كلمة سر طويلة", "PARTNERXX"])
def test_eight_characters_of_anything_pass(password):
    validate_password(password)


def test_short_passwords_fail():
    with pytest.raises(ValidationError):
        validate_password("Partne1")


async def test_breached_password_is_refused(monkeypatch):
    async def breached(_password):
        return True

    monkeypatch.setattr(password_policy, "_is_breached", breached)
    with pytest.raises(ValidationError, match="breach"):
        await password_policy.enforce_password_policy("password123")


async def test_breach_service_outage_fails_open(monkeypatch):
    class Down:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            raise password_policy.httpx.ConnectError("down")

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(password_policy.httpx, "AsyncClient", Down)
    assert await password_policy._is_breached("anything at all") is False
