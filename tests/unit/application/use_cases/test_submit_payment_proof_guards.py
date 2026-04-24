"""Tests for the pre-upload guard ladder in SubmitPaymentProofUseCase.

These tests exercise the cheap validation that runs *before* the R2
upload — the behaviour introduced by fixes #4, #7, #8 in the audit:
expired intent, cancelled order, non-instapay order, mismatched
customer. All of these should raise HTTPException without ever
calling storage_service.upload_file.

The heavier paths (auto-approval, R2 integrity-error cleanup, advisory
lock) are left for integration tests — they need a real DB or, at
minimum, meaningful session mocks that would dwarf the test itself.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.application.use_cases.payments.submit_payment_proof import (
    SubmitPaymentProofUseCase,
)
from src.core.entities.instapay import (
    InstapayIntent,
    InstapayIntentStatus,
)
from src.core.entities.order import OrderStatus, PaymentStatus
from src.infrastructure.external_services.instapay.auto_approval import (
    AutoApprovalConfig,
)


def _config() -> AutoApprovalConfig:
    return AutoApprovalConfig(
        threshold_cents=50_000,
        daily_cap_cents=500_000,
        daily_count_cap=10,
    )


def _order(
    *,
    customer_id: UUID,
    store_id: UUID = None,
    status: OrderStatus = OrderStatus.PENDING,
    payment_status: PaymentStatus = PaymentStatus.PENDING,
    payment_method: str = "instapay",
):
    return SimpleNamespace(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=store_id or uuid4(),
        customer_id=customer_id,
        order_number="NU-0001",
        status=status,
        payment_status=payment_status,
        payment_method=payment_method,
        total=10_000,
        currency="EGP",
        metadata={},
    )


def _intent(
    *, store_id: UUID, order_id: UUID, expires_in_min: int = 30
) -> InstapayIntent:
    now = datetime.now(UTC)
    return InstapayIntent(
        id=uuid4(),
        tenant_id=uuid4(),
        store_id=store_id,
        order_id=order_id,
        reference_code="NU-TESTXX",
        display_ipa="merchant@cib",
        amount_cents=10_000,
        expires_at=now + timedelta(minutes=expires_in_min),
        qr_payload="instapay://pay?...",
        status=InstapayIntentStatus.AWAITING_PAYMENT,
    )


def _build_use_case(*, order, intent=None):
    """Construct a use case with repo mocks hard-wired to return the given order/intent."""
    session = MagicMock()
    session.execute = AsyncMock()
    session.flush = AsyncMock()

    order_repo = MagicMock()
    order_repo.get_by_id = AsyncMock(return_value=order)
    order_repo.update = AsyncMock()

    intent_repo = MagicMock()
    intent_repo.get_by_order_id = AsyncMock(return_value=intent)
    intent_repo.update_status = AsyncMock()

    proof_repo = MagicMock()
    proof_repo.get_by_idempotency_key = AsyncMock(return_value=None)
    proof_repo.image_hash_exists = AsyncMock(return_value=False)
    proof_repo.transaction_ref_exists = AsyncMock(return_value=False)

    storage_service = MagicMock()
    storage_service.upload_file = AsyncMock()
    storage_service.delete_file = AsyncMock()
    storage_service.get_signed_url = AsyncMock(return_value="https://signed/url")

    uc = SubmitPaymentProofUseCase(
        session=session,
        order_repo=order_repo,
        intent_repo=intent_repo,
        proof_repo=proof_repo,
        storage_service=storage_service,
    )
    return uc, storage_service


@pytest.mark.asyncio
class TestGuardsShortCircuitBeforeUpload:
    """Every failing guard must raise before a single byte hits R2."""

    async def test_missing_order_raises_404(self):
        uc, storage = _build_use_case(order=None)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=uuid4(),
                order_id=uuid4(),
                customer_id=uuid4(),
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 404
        storage.upload_file.assert_not_called()

    async def test_different_customer_raises_403(self):
        customer_id = uuid4()
        order = _order(customer_id=uuid4())  # order belongs to someone else
        uc, storage = _build_use_case(order=order)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 403
        storage.upload_file.assert_not_called()

    async def test_already_paid_order_raises_409(self):
        customer_id = uuid4()
        order = _order(customer_id=customer_id, payment_status=PaymentStatus.PAID)
        uc, storage = _build_use_case(order=order)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 409
        storage.upload_file.assert_not_called()

    async def test_cancelled_order_raises_409(self):
        """Audit #7 — terminal orders must never accept new proofs."""
        customer_id = uuid4()
        order = _order(customer_id=customer_id, status=OrderStatus.CANCELLED)
        uc, storage = _build_use_case(order=order)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 409
        storage.upload_file.assert_not_called()

    async def test_non_instapay_order_raises_409(self):
        """Audit #8 — a Paymob order can't absorb an InstaPay proof."""
        customer_id = uuid4()
        order = _order(customer_id=customer_id, payment_method="paymob")
        uc, storage = _build_use_case(order=order)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 409
        storage.upload_file.assert_not_called()

    async def test_missing_intent_raises_404(self):
        customer_id = uuid4()
        order = _order(customer_id=customer_id)
        uc, storage = _build_use_case(order=order, intent=None)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 404
        storage.upload_file.assert_not_called()

    async def test_expired_intent_raises_410(self):
        """Audit #4 — don't waste R2 bandwidth on a dead intent."""
        customer_id = uuid4()
        order = _order(customer_id=customer_id)
        intent = _intent(store_id=order.store_id, order_id=order.id, expires_in_min=-1)
        uc, storage = _build_use_case(order=order, intent=intent)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 410
        storage.upload_file.assert_not_called()

    async def test_image_hash_dedup_short_circuits_before_upload(self):
        customer_id = uuid4()
        order = _order(customer_id=customer_id)
        intent = _intent(store_id=order.store_id, order_id=order.id)
        uc, storage = _build_use_case(order=order, intent=intent)
        uc.proof_repo.image_hash_exists = AsyncMock(return_value=True)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 409
        storage.upload_file.assert_not_called()

    async def test_transaction_ref_dedup_short_circuits_before_upload(self):
        customer_id = uuid4()
        order = _order(customer_id=customer_id)
        intent = _intent(store_id=order.store_id, order_id=order.id)
        uc, storage = _build_use_case(order=order, intent=intent)
        uc.proof_repo.transaction_ref_exists = AsyncMock(return_value=True)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                store_id=order.store_id,
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="BANK-REF-1",
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 409
        storage.upload_file.assert_not_called()

    async def test_empty_transaction_ref_raises_422(self):
        customer_id = uuid4()
        order = _order(customer_id=customer_id)
        intent = _intent(store_id=order.store_id, order_id=order.id)
        uc, storage = _build_use_case(order=order, intent=intent)
        with pytest.raises(HTTPException) as exc:
            await uc.execute(
                order_id=order.id,
                customer_id=customer_id,
                image_bytes=b"fake",
                image_content_type="image/png",
                transaction_ref="   ",  # whitespace-only
                auto_approval_config=_config(),
            )
        assert exc.value.status_code == 422
        storage.upload_file.assert_not_called()
