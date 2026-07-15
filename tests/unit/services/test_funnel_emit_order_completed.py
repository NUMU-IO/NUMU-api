"""Unit tests for funnel_emit_service.emit_order_completed.

Covers the InstaPay proof-approval paths (manual + OCR auto-approve),
which have no gateway webhook to emit ``order_completed``.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from src.application.services.funnel_emit_service import emit_order_completed
from src.core.entities.order import PaymentStatus


class _StubFunnelRepo:
    def __init__(self, raise_on_create: bool = False):
        self.created: list[dict] = []
        self._raise = raise_on_create

    async def create(self, **kwargs):
        if self._raise:
            raise RuntimeError("db down")
        self.created.append(kwargs)


def _order(payment_status=PaymentStatus.PAID, tenant_id=uuid4(), fingerprint="fp-1"):
    return SimpleNamespace(
        id=uuid4(),
        order_number="ORD-1001",
        tenant_id=tenant_id,
        store_id=uuid4(),
        customer_id=uuid4(),
        payment_status=payment_status,
        session_fingerprint=fingerprint,
        total=25000,
    )


@pytest.mark.asyncio
async def test_emits_for_fully_paid_order():
    repo = _StubFunnelRepo()
    order = _order()

    await emit_order_completed(order, repo, payment_method="instapay")

    assert len(repo.created) == 1
    row = repo.created[0]
    assert row["step"] == "order_completed"
    assert row["session_fingerprint"] == "fp-1"
    assert row["step_data"]["payment_method"] == "instapay"
    assert row["step_data"]["total"] == 25000


@pytest.mark.asyncio
async def test_skips_deposit_payment_that_leaves_order_pending():
    # COD-with-deposit: payment_status stays PENDING and the COD checkout
    # path already emitted order_completed — no double count.
    repo = _StubFunnelRepo()
    order = _order(payment_status=PaymentStatus.PENDING)

    await emit_order_completed(order, repo, payment_method="instapay")

    assert repo.created == []


@pytest.mark.asyncio
async def test_skips_order_without_tenant_id():
    repo = _StubFunnelRepo()
    order = _order(tenant_id=None)

    await emit_order_completed(order, repo, payment_method="instapay")

    assert repo.created == []


@pytest.mark.asyncio
async def test_fail_open_on_repo_error():
    # Funnel analytics must never block a payment confirmation.
    repo = _StubFunnelRepo(raise_on_create=True)
    order = _order()

    await emit_order_completed(order, repo, payment_method="instapay")  # no raise
