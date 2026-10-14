"""Processing a refund touches the refund's own order, never the path's.

The use case checks the refund belongs to the store, but the restock and
network-reputation steps loaded the path's `order_id`, which nothing checked:
a merchant could restock (or flag) another store's order by putting its id in
the URL of their own refund.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from src.api.dependencies import get_db, get_order_repository, get_store_repository
from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.repositories import (
    get_network_reputation_repository,
    get_refund_repository,
)
from src.api.v1.routes.stores import refunds as refunds_module
from src.application.dto.refund import RefundDTO
from src.main import app

STORE_A = uuid4()
ORDER_A = uuid4()
ORDER_B = uuid4()
REFUND_A = uuid4()


class _Orders:
    def __init__(self):
        self.loaded = []

    async def get_by_id(self, order_id):
        self.loaded.append(order_id)
        return None


class _Refunds:
    async def get_by_id(self, refund_id):
        return None


class _ProcessRefund:
    def __init__(self, *args):
        pass

    async def execute(self, refund_id, store_id, user_id, payment_service):
        now = datetime.now(UTC)
        return RefundDTO(
            id=REFUND_A,
            order_id=ORDER_A,
            store_id=STORE_A,
            refund_number="R-1",
            refund_type="full",
            status="completed",
            reason="other",
            reason_note=None,
            amount=100,
            currency="EGP",
            payment_provider=None,
            payment_id=None,
            provider_refund_id=None,
            requested_by=None,
            approved_by=None,
            rejected_by=None,
            processed_at=now,
            completed_at=now,
            rejected_at=None,
            failure_reason=None,
            created_at=now,
            updated_at=now,
        )


async def _no_db():
    yield None


@pytest.fixture
def orders(monkeypatch):
    orders = _Orders()
    monkeypatch.setattr(refunds_module, "ProcessRefundUseCase", _ProcessRefund)
    app.dependency_overrides[get_db] = _no_db
    app.dependency_overrides[get_current_user_id] = lambda: uuid4()
    app.dependency_overrides[get_order_repository] = lambda: orders
    app.dependency_overrides[get_refund_repository] = lambda: _Refunds()
    app.dependency_overrides[get_store_repository] = lambda: object()
    app.dependency_overrides[get_network_reputation_repository] = lambda: object()
    yield orders
    app.dependency_overrides.clear()


def test_restock_uses_the_refunds_order_not_the_path(orders):
    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/stores/{STORE_A}/orders/{ORDER_B}/refunds/{REFUND_A}"
            "/process?restock_items=true"
        )

    assert response.status_code == 200, response.text[:200]
    assert orders.loaded and set(orders.loaded) == {ORDER_A}
