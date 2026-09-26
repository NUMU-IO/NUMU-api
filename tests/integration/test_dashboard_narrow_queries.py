"""The dashboard's column-only repository reads run against a real database."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from src.infrastructure.database.connection import AsyncSessionLocal
from src.infrastructure.repositories.order_repository import OrderRepository
from src.infrastructure.repositories.product_repository import ProductRepository
from src.infrastructure.repositories.variant_repository import VariantRepository

pytestmark = pytest.mark.asyncio


async def test_narrow_reads_execute_and_are_empty_for_an_unknown_store():
    store_id = uuid.uuid4()
    now = datetime.now(UTC)
    async with AsyncSessionLocal() as s:
        assert (
            await OrderRepository(s).get_profit_lines(
                store_id, now - timedelta(days=30), now
            )
            == []
        )
        assert await OrderRepository(s).count_by_status_for_store(store_id) == {}
        assert await ProductRepository(s).count_low_stock(store_id) == 0
        assert await ProductRepository(s).cost_cents_by_product(store_id) == {}
        assert await VariantRepository(s).cost_cents_by_variant(store_id) == []
