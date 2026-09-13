"""The `force` flag behind POST /orders/{id}/resend-whatsapp.

A resend is the one caller that must get past the send-once idempotency
check. `force` has to survive both forks of handle_order_created_whatsapp —
the COD tap-to-confirm path and the passive-confirmation path — or the
endpoint silently no-ops on any order that was already messaged.
"""

from uuid import uuid4

import src.infrastructure.events.handlers.whatsapp_notification_handler as handler
from src.core.events.order_events import OrderCreatedEvent


def _event() -> OrderCreatedEvent:
    return OrderCreatedEvent(
        order_id=uuid4(),
        order_number="ORD-000001",
        store_id=uuid4(),
        customer_id=uuid4(),
        total=165000,
        currency="EGP",
    )


async def test_force_forwarded_to_cod_confirm_path(monkeypatch) -> None:
    seen: dict = {}

    async def fake_cod(session, event, force=False):
        seen["force"] = force
        return True

    monkeypatch.setattr(handler, "_maybe_send_cod_confirm_request", fake_cod)
    await handler.handle_order_created_whatsapp(_event(), force=True)
    assert seen["force"] is True


async def test_force_forwarded_to_passive_confirmation_path(monkeypatch) -> None:
    seen: dict = {}

    async def fake_cod(session, event, force=False):
        return False

    async def fake_resolve(session, **kwargs):
        seen["force"] = kwargs.get("force")
        return None

    monkeypatch.setattr(handler, "_maybe_send_cod_confirm_request", fake_cod)
    monkeypatch.setattr(handler, "_resolve_send_context", fake_resolve)
    await handler.handle_order_created_whatsapp(_event(), force=True)
    assert seen["force"] is True


async def test_default_send_stays_idempotent(monkeypatch) -> None:
    seen: dict = {}

    async def fake_cod(session, event, force=False):
        seen["force"] = force
        return True

    monkeypatch.setattr(handler, "_maybe_send_cod_confirm_request", fake_cod)
    await handler.handle_order_created_whatsapp(_event())
    assert seen["force"] is False
