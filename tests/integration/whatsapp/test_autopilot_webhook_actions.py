"""Webhook routing tests for COD Autopilot inbound actions (004).

Verifies the dispatch layer contracts without a DB: quick-reply button
payloads route to the right Autopilot handler with the right kwargs, the
merchant free-text branch feeds ``handle_digest_text_reply``, and a
handler exception never escapes (the webhook owes Meta a 200).

The handler INTERNALS (phone matching, idempotency, state transitions)
are covered by the service/state-machine unit tests and the staging
smoke test in quickstart.md §7.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from src.api.v1.routes.webhooks.whatsapp import (
    _process_confirm_replies,
    _process_digest_text_replies,
)

_ORDER_ID = "11111111-1111-1111-1111-111111111111"


def _button_value(payload: str, sender: str = "201001234567") -> dict:
    return {
        "messages": [{"type": "button", "from": sender, "button": {"payload": payload}}]
    }


def _text_value(body: str, sender: str = "201001234567") -> dict:
    return {"messages": [{"type": "text", "from": sender, "text": {"body": body}}]}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["dlvyes", "dlvnot", "dlvref"])
async def test_delivery_check_buttons_route_to_handler(monkeypatch, action):
    called: dict = {}

    async def _fake(session, *, action, payload, from_phone):
        called.update(action=action, payload=payload, from_phone=from_phone)
        return True

    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_delivery_response",
        _fake,
    )
    payload = f"{action}:cairostyle/{_ORDER_ID}"
    await _process_confirm_replies(AsyncMock(), _button_value(payload))

    assert called["action"] == action
    assert called["payload"] == payload
    assert called["from_phone"] == "201001234567"


@pytest.mark.asyncio
async def test_shipall_button_routes_to_shipall_handler(monkeypatch):
    called: dict = {}

    async def _fake(session, *, payload, from_phone):
        called.update(payload=payload, from_phone=from_phone)
        return True

    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_shipall", _fake
    )
    payload = f"shipall:cairostyle/{_ORDER_ID}"
    await _process_confirm_replies(AsyncMock(), _button_value(payload))

    assert called["payload"] == payload


@pytest.mark.asyncio
async def test_confirm_payloads_still_route_to_confirm(monkeypatch):
    """Adding Autopilot actions must not disturb the existing COD
    confirm-request routing."""
    called: dict = {}

    async def _fake(session, *, payload, from_phone):
        called["payload"] = payload
        return True

    monkeypatch.setattr(
        "src.application.services.order_confirmation_service."
        "confirm_order_from_whatsapp",
        _fake,
    )
    payload = f"confirm:cairostyle/{_ORDER_ID}"
    await _process_confirm_replies(AsyncMock(), _button_value(payload))
    assert called["payload"] == payload


@pytest.mark.asyncio
async def test_handler_exception_is_swallowed(monkeypatch):
    """A crashing handler must not propagate — Meta needs its 200."""

    async def _boom(session, *, action, payload, from_phone):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_delivery_response",
        _boom,
    )
    await _process_confirm_replies(
        AsyncMock(), _button_value(f"dlvyes:x/{_ORDER_ID}")
    )  # no raise


@pytest.mark.asyncio
async def test_text_messages_feed_digest_reply_handler(monkeypatch):
    called: dict = {}

    async def _fake(session, *, text, from_phone):
        called.update(text=text, from_phone=from_phone)
        return True

    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_digest_text_reply",
        _fake,
    )
    await _process_digest_text_replies(AsyncMock(), _text_value("except 2, 5"))
    assert called["text"] == "except 2, 5"
    assert called["from_phone"] == "201001234567"


@pytest.mark.asyncio
async def test_non_text_messages_skip_digest_branch(monkeypatch):
    fake = AsyncMock()
    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_digest_text_reply",
        fake,
    )
    await _process_digest_text_replies(
        AsyncMock(), _button_value(f"dlvyes:x/{_ORDER_ID}")
    )
    fake.assert_not_called()


@pytest.mark.asyncio
async def test_digest_text_handler_exception_is_swallowed(monkeypatch):
    async def _boom(session, *, text, from_phone):
        raise RuntimeError("boom")

    monkeypatch.setattr(
        "src.application.services.cod_autopilot_service.handle_digest_text_reply",
        _boom,
    )
    await _process_digest_text_replies(AsyncMock(), _text_value("anything"))  # no raise
