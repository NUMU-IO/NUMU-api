"""Meta, WhatsApp and Resend webhooks must be signed.

Each used to skip verification when its secret was unset, Meta never tried
the Instagram app secret when the DB held the Facebook one, and Resend
compared a hex HMAC of the body against Svix's base64 signature of
``{id}.{timestamp}.{body}``, so genuine Resend deliveries never verified.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from src.api.v1.routes.webhooks import meta, resend, whatsapp
from src.config import settings
from src.infrastructure.external_services.meta.signature import (
    verify_x_hub_signature,
)

BODY = b'{"object": "instagram", "entry": []}'


class _Request:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def body(self) -> bytes:
        return self._body


def _x_hub(secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), BODY, hashlib.sha256).hexdigest()


def _no_meta_secret(monkeypatch, module):
    async def _creds(_db):
        return SimpleNamespace(app_secret="", webhook_verify_token="")

    monkeypatch.setattr(module, "get_meta_platform_credentials", _creds)
    for name in ("meta_app_secret", "meta_ig_app_secret", "whatsapp_app_secret"):
        monkeypatch.setattr(settings, name, None)


def test_instagram_secret_is_tried_alongside_the_db_app_secret(monkeypatch):
    monkeypatch.setattr(settings, "meta_ig_app_secret", "ig-secret")

    assert verify_x_hub_signature(BODY, _x_hub("ig-secret"), app_secret="fb-secret")
    assert verify_x_hub_signature(BODY, _x_hub("fb-secret"), app_secret="fb-secret")
    assert not verify_x_hub_signature(BODY, _x_hub("other"), app_secret="fb-secret")


@pytest.mark.asyncio
async def test_meta_refuses_everything_without_a_secret(monkeypatch):
    _no_meta_secret(monkeypatch, meta)

    response = await meta.receive_webhook(
        _Request(BODY), db=object(), x_hub_signature_256=_x_hub("anything")
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_whatsapp_refuses_everything_without_a_secret(monkeypatch):
    _no_meta_secret(monkeypatch, whatsapp)

    with pytest.raises(HTTPException) as exc:
        await whatsapp.whatsapp_callback(
            _Request(BODY), db=object(), x_hub_signature_256=_x_hub("anything")
        )

    assert exc.value.status_code == 401


def _svix_headers(secret: bytes, body: bytes, timestamp: int | None = None) -> dict:
    msg_id, ts = "msg_1", str(timestamp or int(time.time()))
    sig = base64.b64encode(
        hmac.new(secret, f"{msg_id}.{ts}.".encode() + body, hashlib.sha256).digest()
    ).decode()
    return {"svix-id": msg_id, "svix-timestamp": ts, "svix-signature": f"v1,{sig}"}


def test_resend_verifies_svix_signatures(monkeypatch):
    key = b"resend-signing-key"
    monkeypatch.setattr(
        settings, "resend_webhook_secret", "whsec_" + base64.b64encode(key).decode()
    )

    assert resend._verify_signature(BODY, _svix_headers(key, BODY))
    assert not resend._verify_signature(BODY, _svix_headers(b"wrong", BODY))
    assert not resend._verify_signature(BODY, {})
    stale = _svix_headers(key, BODY, timestamp=int(time.time()) - 3600)
    assert not resend._verify_signature(BODY, stale), "replays are refused"


def test_resend_refuses_everything_without_a_secret(monkeypatch):
    monkeypatch.setattr(settings, "resend_webhook_secret", None)

    assert not resend._verify_signature(BODY, _svix_headers(b"k", BODY))
