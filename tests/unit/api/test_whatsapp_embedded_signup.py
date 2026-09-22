from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src.api.v1.routes.stores.whatsapp import (
    _graph_api_base,
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

    incomplete = await get_signup_config(store=object(), db=db)
    assert incomplete.data.enabled is False

    monkeypatch.setattr(settings, "meta_config_id", "config-123")
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
    )

    assert request.waba_id == "waba-123"
    assert request.phone_number_id == "phone-456"


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
