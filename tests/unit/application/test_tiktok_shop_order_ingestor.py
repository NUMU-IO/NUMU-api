"""Unit tests for ``TikTokShopOrderIngestor`` — maps a TikTok Shop order dict
into a native NUMU order (mirrors the OrderImportService pattern).

Fake repos (AsyncMock) — no DB. Pins: dedup skip, status mapping, totals,
metadata (source=tiktok_shop + external_order_id), and existing-customer reuse.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.application.services.tiktok_shop_order_ingestor import (
    TikTokShopOrderIngestor,
)
from src.core.entities.order import OrderStatus


def _store():
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        slug="teststore",
        default_currency=SimpleNamespace(value="EGP"),
    )


def _tiktok_order(status: str = "DELIVERED"):
    return {
        "id": "TT-ORDER-1",
        "status": status,
        "buyer_email": "buyer@example.com",
        "recipient_address": {
            "name": "Sara Ali",
            "phone_number": "+201234567890",
            "full_address": "12 Nile St",
            "district_info": [{"address_name": "Cairo"}],
            "region_code": "EG",
        },
        "payment": {
            "currency": "EGP",
            "total_amount": "250.00",
            "shipping_fee": "20.00",
        },
        "line_items": [
            {
                "product_name": "Scarf",
                "seller_sku": "SKU1",
                "sale_price": "115.00",
                "quantity": 2,
            },
        ],
    }


def _repos(*, exists=False, existing_customer=True):
    order_repo = SimpleNamespace(
        exists_by_external_id=AsyncMock(return_value=exists),
        get_next_order_number=AsyncMock(return_value="ORD-000001"),
        create=AsyncMock(side_effect=lambda o: o),
    )
    cust = SimpleNamespace(id=uuid4()) if existing_customer else None
    customer_repo = SimpleNamespace(
        get_by_email=AsyncMock(return_value=cust),
        create=AsyncMock(return_value=SimpleNamespace(id=uuid4())),
    )
    return order_repo, customer_repo


class TestDedup:
    async def test_duplicate_skips_without_create(self):
        order_repo, customer_repo = _repos(exists=True)
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        result = await ing.ingest(store=_store(), tiktok_order=_tiktok_order())
        assert result is None
        order_repo.create.assert_not_called()

    async def test_missing_order_id_skips(self):
        order_repo, customer_repo = _repos()
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        result = await ing.ingest(store=_store(), tiktok_order={"status": "DELIVERED"})
        assert result is None
        order_repo.create.assert_not_called()


class TestHappyPath:
    async def test_creates_order_with_channel_metadata(self):
        order_repo, customer_repo = _repos()
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        await ing.ingest(store=_store(), tiktok_order=_tiktok_order())

        order_repo.create.assert_awaited_once()
        created = order_repo.create.call_args.args[0]
        assert created.metadata["source"] == "tiktok_shop"
        assert created.metadata["external_order_id"] == "TT-ORDER-1"
        assert created.status == OrderStatus.DELIVERED
        assert created.payment_method == "tiktok_shop"

    async def test_totals_from_payload(self):
        order_repo, customer_repo = _repos()
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        await ing.ingest(store=_store(), tiktok_order=_tiktok_order())
        created = order_repo.create.call_args.args[0]
        # 2 × 115.00 = 230.00 subtotal (23000 cents); total = grand 250.00 (25000).
        assert created.subtotal == 23_000
        assert created.total == 25_000
        assert created.shipping_cost == 2_000
        assert len(created.line_items) == 1
        assert created.line_items[0].unit_price == 11_500

    async def test_reuses_existing_customer(self):
        order_repo, customer_repo = _repos(existing_customer=True)
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        await ing.ingest(store=_store(), tiktok_order=_tiktok_order())
        customer_repo.create.assert_not_called()

    async def test_creates_customer_when_absent(self):
        order_repo, customer_repo = _repos(existing_customer=False)
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        await ing.ingest(store=_store(), tiktok_order=_tiktok_order())
        customer_repo.create.assert_awaited_once()

    @pytest.mark.parametrize(
        "tt_status,expected",
        [
            ("UNPAID", OrderStatus.PENDING),
            ("AWAITING_SHIPMENT", OrderStatus.CONFIRMED),
            ("IN_TRANSIT", OrderStatus.SHIPPED),
            ("DELIVERED", OrderStatus.DELIVERED),
            ("CANCELLED", OrderStatus.CANCELLED),
            ("SOMETHING_NEW", OrderStatus.CONFIRMED),  # safe default
        ],
    )
    async def test_status_mapping(self, tt_status, expected):
        order_repo, customer_repo = _repos()
        ing = TikTokShopOrderIngestor(order_repo, customer_repo)
        await ing.ingest(store=_store(), tiktok_order=_tiktok_order(status=tt_status))
        created = order_repo.create.call_args.args[0]
        assert created.status == expected
