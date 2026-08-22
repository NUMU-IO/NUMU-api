"""Unit tests for the shared Shopify COD-to-prepaid nudge service."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.application.services import shopify_nudge_service as svc


class FakeWaService:
    """Captures the MessageContent instead of hitting Meta."""

    def __init__(self, *, enabled: bool = True, success: bool = True) -> None:
        self.enabled = enabled
        self._success = success
        self.sent: list = []

    async def send_message(self, content):
        self.sent.append(content)
        return SimpleNamespace(
            success=self._success,
            message_id="wamid.FAKE" if self._success else None,
            error_message=None if self._success else "template_rejected",
        )


@pytest.mark.asyncio
async def test_send_uses_cod_recovery_offer_template_and_pay_payload():
    wa = FakeWaService()
    result = await svc.send_conversion_nudge(
        phone="+201001234567",
        customer_name="Sara",
        order_number="1042",
        store_name="Demo Store",
        amount_cents=25050,
        currency="EGP",
        payment_session_id="abc-123",
        language="en",
        wa_service=wa,
    )
    assert result.sent is True
    assert result.message_id == "wamid.FAKE"

    content = wa.sent[0]
    from src.core.interfaces.services.messaging_service import MessageType

    assert content.type == MessageType.COD_RECOVERY_OFFER
    params = content.template_params
    assert params["pay_payload"] == "shopify/abc-123"
    assert params["total"] == "250.50 EGP"
    assert params["promo"] == svc.DEFAULT_PROMO["en"]
    # Meta rejects blank variables — every param must be non-empty.
    assert all(str(v).strip() for v in params.values())


@pytest.mark.asyncio
async def test_send_defaults_to_arabic_promo_and_language():
    wa = FakeWaService()
    await svc.send_conversion_nudge(
        phone="+201001234567",
        customer_name="",
        order_number="",
        store_name="",
        amount_cents=1000,
        currency="EGP",
        payment_session_id="s1",
        wa_service=wa,
    )
    content = wa.sent[0]
    assert content.recipient.language == "ar"
    assert content.template_params["promo"] == svc.DEFAULT_PROMO["ar"]
    # Empty inputs are replaced with safe non-empty fallbacks.
    assert all(str(v).strip() for v in content.template_params.values())


@pytest.mark.asyncio
async def test_send_reports_failure_reason():
    wa = FakeWaService(success=False)
    result = await svc.send_conversion_nudge(
        phone="+201001234567",
        customer_name="Sara",
        order_number="1042",
        store_name="Demo",
        amount_cents=1000,
        currency="EGP",
        payment_session_id="s2",
        wa_service=wa,
    )
    assert result.sent is False
    assert result.error == "template_rejected"


@pytest.mark.asyncio
async def test_send_short_circuits_when_whatsapp_disabled():
    wa = FakeWaService(enabled=False)
    result = await svc.send_conversion_nudge(
        phone="+201001234567",
        customer_name="Sara",
        order_number="1042",
        store_name="Demo",
        amount_cents=1000,
        currency="EGP",
        payment_session_id="s3",
        wa_service=wa,
    )
    assert result.sent is False
    assert result.error == "whatsapp_disabled"
    assert wa.sent == []


def test_payment_page_url_prefers_explicit_setting(monkeypatch):
    monkeypatch.setattr(
        svc,
        "get_settings",
        lambda: SimpleNamespace(
            shopify_payment_page_base=lambda: "https://pay.example.com/p"
        ),
    )
    assert svc.payment_page_url("sid-1") == "https://pay.example.com/p/sid-1"


def test_store_display_name_fallback_chain():
    assert svc.store_display_name("demo.myshopify.com", "Demo Store") == "Demo Store"
    assert svc.store_display_name("demo.myshopify.com", None) == "demo"
    assert svc.store_display_name(None, None) == "your store"


def test_settings_payment_page_base_fallback_chain():
    from src.config.settings import Settings

    s = Settings(
        shopify_payment_page_url="https://x.example/pp/",
        shopify_app_url="https://app.example",
    )
    assert s.shopify_payment_page_base() == "https://x.example/pp"

    s = Settings(shopify_payment_page_url="", shopify_app_url="https://app.example/")
    assert s.shopify_payment_page_base() == "https://app.example/p"

    s = Settings(shopify_payment_page_url="", shopify_app_url="")
    assert s.shopify_payment_page_base() == "https://shopify.numueg.app/p"
