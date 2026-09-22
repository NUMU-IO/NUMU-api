from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from src.api.v1.routes.admin.platform_config import MetaCredentialsRequest
from src.api.v1.routes.stores.whatsapp import (
    _graph_api_base,
    _register_signup_phone,
    _select_signup_phone,
    get_signup_config,
)
from src.api.v1.schemas.stores.whatsapp import EmbeddedSignupRequest
from src.config import settings


@pytest.mark.asyncio
async def test_signup_config_only_enables_complete_meta_configuration(monkeypatch):
    db = AsyncMock()
    result = Mock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result
    monkeypatch.setattr(settings, "meta_app_id", "app-123")
    monkeypatch.setattr(settings, "meta_app_secret", "secret-123")
    monkeypatch.setattr(settings, "meta_config_id", None)
    monkeypatch.setattr(settings, "meta_phone_registration_pin", None)

    incomplete = await get_signup_config(store=object(), db=db)
    assert incomplete.data.enabled is False

    monkeypatch.setattr(settings, "meta_config_id", "config-123")
    still_incomplete = await get_signup_config(store=object(), db=db)
    assert still_incomplete.data.enabled is False

    monkeypatch.setattr(settings, "meta_phone_registration_pin", "123456")
    complete = await get_signup_config(store=object(), db=db)
    assert complete.data.enabled is True
    assert complete.data.app_id == "app-123"
    assert complete.data.config_id == "config-123"
    assert complete.data.graph_api_version == settings.meta_graph_api_version


def test_signup_request_accepts_meta_session_selection():
    request = EmbeddedSignupRequest(
        code="one-time-code",
        waba_id="waba-123",
        phone_number_id="phone-456",
        business_id="business-789",
    )

    assert request.waba_id == "waba-123"
    assert request.phone_number_id == "phone-456"
    assert request.business_id == "business-789"


def test_selected_phone_must_belong_to_selected_waba():
    phones = [
        {
            "id": "phone-1",
            "verified_name": "NUMU Test Store",
            "display_phone_number": "+201000000000",
        }
    ]

    phone_id, phone = _select_signup_phone(phones, "phone-1")
    assert phone_id == "phone-1"
    assert phone["verified_name"] == "NUMU Test Store"

    with pytest.raises(HTTPException) as exc:
        _select_signup_phone(phones, "another-business-phone")
    assert exc.value.status_code == 400


def test_graph_api_base_uses_configured_version(monkeypatch):
    monkeypatch.setattr(settings, "meta_graph_api_version", "v25.0")
    assert _graph_api_base() == "https://graph.facebook.com/v25.0"


def test_meta_registration_pin_requires_exactly_six_digits():
    assert (
        MetaCredentialsRequest(
            meta_phone_registration_pin="123456"
        ).meta_phone_registration_pin
        == "123456"
    )

    for invalid in ("12345", "1234567", "12345x"):
        with pytest.raises(ValidationError):
            MetaCredentialsRequest(meta_phone_registration_pin=invalid)


@pytest.mark.asyncio
async def test_signup_registers_selected_phone_with_cloud_api_pin():
    client = AsyncMock()
    client.post.return_value = Mock(status_code=200)

    await _register_signup_phone(
        client,
        "https://graph.facebook.com/v25.0",
        "phone-456",
        "access-token",
        "123456",
    )

    client.post.assert_awaited_once_with(
        "https://graph.facebook.com/v25.0/phone-456/register",
        json={"messaging_product": "whatsapp", "pin": "123456"},
        headers={"Authorization": "Bearer access-token"},
        timeout=30.0,
    )
