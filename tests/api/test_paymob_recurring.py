"""Tests for backend-005 Paymob recurring billing.

Covers ``PaymobRecurringBillingService`` in isolation against fake
``IPaymentService`` + fake ``SecretsManager`` implementations. The
DB-side wiring (subscribe.py / cancel_subscription.py) is exercised
in integration tests against a real session.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest

from src.application.services.paymob_recurring_billing_service import (
    PaymobRecurringBillingService,
    RecurringChargeFailure,
    RecurringChargeSuccess,
)
from src.core.interfaces.services.payment_service import (
    PaymentProvider,
    PaymentResult,
    RefundResult,
)

# ─────────────────────────────────────────────────────────────────────
# Fakes
# ─────────────────────────────────────────────────────────────────────


class _FakeSecretsManager:
    """In-memory encrypt/decrypt that round-trips JSON dicts."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict | bytes]] = []

    async def encrypt(self, data: dict, key_id: str) -> bytes:
        self.calls.append(("encrypt", data))
        import json

        return json.dumps({"key_id": key_id, "data": data}).encode("utf-8")

    async def decrypt(self, encrypted: bytes, key_id: str) -> dict:
        self.calls.append(("decrypt", encrypted))
        import json

        payload = json.loads(encrypted.decode("utf-8"))
        if payload.get("key_id") != key_id:
            raise ValueError("key_id mismatch")
        return payload["data"]


class _FakePaymobService:
    """Records charge_saved_token calls + returns scripted results."""

    def __init__(self, *, success: bool = True, error: str | None = None) -> None:
        self.success = success
        self.error = error
        self.charges: list[dict[str, Any]] = []

    @property
    def provider(self) -> PaymentProvider:
        return PaymentProvider.PAYMOB

    async def charge_saved_token(
        self,
        card_token: str,
        amount: int,
        currency: str,
        order_id: str,
    ) -> PaymentResult:
        self.charges.append({
            "card_token": card_token,
            "amount": amount,
            "currency": currency,
            "order_id": order_id,
        })
        if self.success:
            return PaymentResult(success=True, payment_id=f"tx-{order_id}")
        return PaymentResult(
            success=False,
            error_message=self.error or "card_declined",
            error_code="declined",
        )

    # Unused interface methods — required by IPaymentService but not
    # called by the recurring service. Implementing as raise-on-call
    # so any accidental coupling shows up immediately.
    async def create_payment_intent(self, *args, **kwargs) -> Any:
        raise AssertionError("create_payment_intent should not be called")

    async def confirm_payment(self, *args, **kwargs) -> PaymentResult:
        raise AssertionError("confirm_payment should not be called")

    async def capture_payment(self, *args, **kwargs) -> PaymentResult:
        raise AssertionError("capture_payment should not be called")

    async def cancel_payment(self, *args, **kwargs) -> PaymentResult:
        raise AssertionError("cancel_payment should not be called")

    async def refund_payment(self, *args, **kwargs) -> RefundResult:
        raise AssertionError("refund_payment should not be called")

    async def get_payment_status(self, *args, **kwargs) -> str:
        raise AssertionError("get_payment_status should not be called")

    def verify_webhook_signature(self, *args, **kwargs) -> dict | None:
        raise AssertionError("verify_webhook_signature should not be called")


# ─────────────────────────────────────────────────────────────────────
# encrypt_card_token / decrypt round-trip
# ─────────────────────────────────────────────────────────────────────


class TestTokenStorage:
    @pytest.mark.asyncio
    async def test_encrypt_round_trips_through_decrypt(self):
        secrets = _FakeSecretsManager()
        service = PaymobRecurringBillingService(
            paymob_service=_FakePaymobService(),
            secrets_manager=secrets,
        )

        ciphertext = await service.encrypt_card_token("raw-card-token-123", "v1")
        # The output is a base64 string suitable for a TEXT column.
        assert isinstance(ciphertext, str)
        assert "raw-card-token-123" not in ciphertext

        recovered = await service._decrypt_card_token(ciphertext, "v1")
        assert recovered == "raw-card-token-123"


# ─────────────────────────────────────────────────────────────────────
# charge_subscription
# ─────────────────────────────────────────────────────────────────────


class TestChargeSubscription:
    @pytest.mark.asyncio
    async def test_success_returns_recurring_charge_success(self):
        secrets = _FakeSecretsManager()
        paymob = _FakePaymobService(success=True)
        service = PaymobRecurringBillingService(
            paymob_service=paymob, secrets_manager=secrets
        )

        encrypted = await service.encrypt_card_token("token-abc", "v1")

        tenant_id = uuid4()
        result = await service.charge_subscription(
            tenant_id=tenant_id,
            amount_cents=999,
            currency="EGP",
            encrypted_card_token=encrypted,
            key_id="v1",
            idempotency_ref="renewal-1",
        )

        assert isinstance(result, RecurringChargeSuccess)
        assert result.transaction_id == "tx-renewal-1"
        assert paymob.charges == [
            {
                "card_token": "token-abc",
                "amount": 999,
                "currency": "EGP",
                "order_id": "renewal-1",
            }
        ]

    @pytest.mark.asyncio
    async def test_failure_returns_recurring_charge_failure(self):
        secrets = _FakeSecretsManager()
        paymob = _FakePaymobService(success=False, error="insufficient_funds")
        service = PaymobRecurringBillingService(
            paymob_service=paymob, secrets_manager=secrets
        )

        encrypted = await service.encrypt_card_token("token", "v1")

        result = await service.charge_subscription(
            tenant_id=uuid4(),
            amount_cents=999,
            currency="EGP",
            encrypted_card_token=encrypted,
            key_id="v1",
            idempotency_ref="renewal-2",
        )

        assert isinstance(result, RecurringChargeFailure)
        assert result.reason == "insufficient_funds"
        assert result.error_code == "declined"

    @pytest.mark.asyncio
    async def test_zero_amount_short_circuits_without_calling_paymob(self):
        paymob = _FakePaymobService(success=True)
        service = PaymobRecurringBillingService(
            paymob_service=paymob, secrets_manager=_FakeSecretsManager()
        )

        result = await service.charge_subscription(
            tenant_id=uuid4(),
            amount_cents=0,
            currency="EGP",
            encrypted_card_token="ignored",
            key_id="v1",
            idempotency_ref="zero",
        )

        assert isinstance(result, RecurringChargeFailure)
        assert result.reason == "amount_must_be_positive"
        assert paymob.charges == []

    @pytest.mark.asyncio
    async def test_negative_amount_rejected(self):
        paymob = _FakePaymobService(success=True)
        service = PaymobRecurringBillingService(
            paymob_service=paymob, secrets_manager=_FakeSecretsManager()
        )

        result = await service.charge_subscription(
            tenant_id=uuid4(),
            amount_cents=-1,
            currency="EGP",
            encrypted_card_token="ignored",
            key_id="v1",
            idempotency_ref="neg",
        )

        assert isinstance(result, RecurringChargeFailure)
        assert paymob.charges == []

    @pytest.mark.asyncio
    async def test_idempotency_ref_passed_as_paymob_order_id(self):
        """The Paymob ``order_id`` is the only de-dup signal upstream
        — re-running the renewal task with the same ref must reach
        Paymob with the same order_id, so they reject the dup."""
        paymob = _FakePaymobService(success=True)
        service = PaymobRecurringBillingService(
            paymob_service=paymob, secrets_manager=_FakeSecretsManager()
        )
        encrypted = await service.encrypt_card_token("t", "v1")

        await service.charge_subscription(
            tenant_id=uuid4(),
            amount_cents=100,
            currency="EGP",
            encrypted_card_token=encrypted,
            key_id="v1",
            idempotency_ref="renewal-tenant-x-2026-05-08",
        )

        assert paymob.charges[0]["order_id"] == "renewal-tenant-x-2026-05-08"
