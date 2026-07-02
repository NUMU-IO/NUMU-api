"""Unit tests for ``enqueue_tiktok_capi_purchase`` — TikTok CompletePayment
dispatcher used by every payment/COD webhook.

Pure-Python: the lazy imports (StoreRepository, resolve_tiktok_pixels,
tiktok_capi_send_event) are monkeypatched at their source modules.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.application.services.tiktok_capi_purchase_dispatcher import (
    enqueue_tiktok_capi_purchase,
)

PIXEL_ID = "C4A2B1D3E4F5"


def _make_store(*, api_enabled: bool = True, pixel_id: str | None = PIXEL_ID):
    return SimpleNamespace(
        id=uuid4(),
        settings={
            "tracking": {
                "tiktok": {
                    "pixel_id": pixel_id,
                    "pixel_enabled": True,
                    "api_enabled": api_enabled,
                }
            }
        },
    )


def _make_order():
    return SimpleNamespace(
        id=uuid4(),
        store_id=uuid4(),
        line_items=[
            {
                "product_id": "p1",
                "product_name": "Scarf",
                "quantity": 2,
                "unit_price": 12_500,
            },
        ],
        shipping_address={
            "email": "buyer@example.com",
            "phone": "+201234567890",
            "first_name": "Sara",
            "last_name": "Ali",
            "city": "Cairo",
            "country": "EG",
        },
        total=25_000,
        currency="EGP",
        paid_at=datetime(2026, 5, 16, 10, 0, tzinfo=UTC),
        customer_id=None,
        metadata={"ip_address": "192.0.2.1", "user_agent": "UA/1", "ttclid": "TT9"},
    )


@pytest.fixture
def patched(monkeypatch):
    store_repo_cls = MagicMock()
    store_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)
    send_task = MagicMock()
    send_task.delay = MagicMock()

    import src.infrastructure.messaging.tasks.tiktok_capi as task_module
    import src.infrastructure.repositories.store_repository as store_module

    monkeypatch.setattr(store_module, "StoreRepository", store_repo_cls)
    monkeypatch.setattr(task_module, "tiktok_capi_send_event", send_task)
    return store_repo_cls, send_task


class TestGate:
    async def test_no_op_when_store_missing(self, patched):
        store_repo_cls, send_task = patched
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=None)
        await enqueue_tiktok_capi_purchase(MagicMock(), _make_order())
        send_task.delay.assert_not_called()

    async def test_no_op_when_api_disabled(self, patched):
        store_repo_cls, send_task = patched
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=_make_store(api_enabled=False)
        )
        await enqueue_tiktok_capi_purchase(MagicMock(), _make_order())
        send_task.delay.assert_not_called()

    async def test_no_op_when_pixel_missing(self, patched):
        store_repo_cls, send_task = patched
        store_repo_cls.return_value.get_by_id = AsyncMock(
            return_value=_make_store(pixel_id=None)
        )
        await enqueue_tiktok_capi_purchase(MagicMock(), _make_order())
        send_task.delay.assert_not_called()


class TestFire:
    async def test_fires_complete_payment_with_order_id_dedup(self, patched):
        store_repo_cls, send_task = patched
        store = _make_store()
        store_repo_cls.return_value.get_by_id = AsyncMock(return_value=store)
        order = _make_order()

        await enqueue_tiktok_capi_purchase(MagicMock(), order)

        send_task.delay.assert_called_once()
        kwargs = send_task.delay.call_args.kwargs
        assert kwargs["event_name"] == "CompletePayment"
        assert kwargs["event_id"] == str(order.id)  # dedup contract
        assert kwargs["pixel_id"] == PIXEL_ID
        assert kwargs["action_source"] == "web"
        # custom_data carries value in major units + currency + order_id.
        assert kwargs["custom_data"]["currency"] == "EGP"
        assert kwargs["custom_data"]["order_id"] == str(order.id)
        # ttclid from order metadata threads into user_data.
        assert kwargs["user_data"].get("ttclid") == "TT9"
