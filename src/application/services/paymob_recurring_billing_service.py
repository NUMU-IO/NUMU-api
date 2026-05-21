"""Paymob recurring billing service (backend-005).

Wraps the existing ``PaymobPaymentService.charge_saved_token`` so the
subscription/renewal flow has a typed, isolated entrypoint that the
Celery beat task and the ``SubscribeUseCase`` can both call.

Why a separate service:
  * The renewal Celery task and the subscribe use case share charging
    semantics but live in different layers; pushing the token-decrypt
    + charge call through one entrypoint keeps the secrets-manager
    interaction in one place.
  * Stateless. Tests inject fake ``IPaymentService`` + fake secrets
    manager; nothing else needs mocking.

Paymob, as of 2026-Q1, exposes no native subscription resource in
MENA. "Recurring" here means "charge the saved card token again at
the next period boundary," matching their documented tokenized-pay
flow.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from src.core.interfaces.services.payment_service import PaymentResult

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────
# Result types
# ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class RecurringChargeSuccess:
    """Successful recurring charge."""

    transaction_id: str


@dataclass(frozen=True)
class RecurringChargeFailure:
    """Failed recurring charge — caller decides retry / dunning."""

    reason: str
    error_code: str | None = None


RecurringChargeResult = RecurringChargeSuccess | RecurringChargeFailure


# ──────────────────────────────────────────────────────────────────────
# Secrets manager protocol — keeps tests free of cryptography deps
# ──────────────────────────────────────────────────────────────────────


class _SecretsManagerLike(Protocol):
    async def encrypt(self, data: dict, key_id: str) -> bytes: ...  # pragma: no cover

    async def decrypt(
        self, encrypted: bytes, key_id: str
    ) -> dict: ...  # pragma: no cover


class _TokenChargingService(Protocol):
    """Subset of ``PaymobPaymentService`` we depend on.

    ``IPaymentService`` does not declare ``charge_saved_token`` because
    not every provider supports it. We define a narrow Protocol so the
    recurring service stays testable + providers without a saved-token
    flow can't accidentally be passed in.
    """

    async def charge_saved_token(
        self,
        card_token: str,
        amount: int,
        currency: str,
        order_id: str,
    ) -> PaymentResult: ...  # pragma: no cover


# ──────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────


class PaymobRecurringBillingService:
    """Charge a tenant's stored Paymob card token.

    Stateless — every call decrypts the token, hits Paymob, and
    returns the typed result. Persisting the new state (transaction
    id, retry count, next renewal) is the caller's responsibility.

    Args:
        paymob_service: A ``_TokenChargingService`` (in production a
            ``PaymobPaymentService``) for the platform's own billing
            — *not* a merchant's instance. Numu charges merchants on
            its own Paymob account.
        secrets_manager: Provides ``encrypt(...)``/``decrypt(...)``.
            Defaults to the singleton via ``get_secrets_manager()``.
    """

    def __init__(
        self,
        paymob_service: _TokenChargingService,
        secrets_manager: _SecretsManagerLike | None = None,
    ) -> None:
        self._paymob = paymob_service
        if secrets_manager is None:
            from src.infrastructure.external_services.secrets.secrets_manager import (
                get_secrets_manager,
            )

            secrets_manager = get_secrets_manager()
        self._secrets = secrets_manager

    # ── token persistence helpers ─────────────────────────────────────

    async def encrypt_card_token(self, raw_token: str, key_id: str) -> str:
        """Return the base64-encoded encrypted blob for storage.

        Keeps the encryption envelope in one place so the model only
        ever sees a string (TEXT column), and decryption happens in
        ``charge_subscription``.
        """
        import base64

        encrypted = await self._secrets.encrypt({"token": raw_token}, key_id)
        return base64.b64encode(encrypted).decode("ascii")

    async def _decrypt_card_token(self, ciphertext: str, key_id: str) -> str:
        import base64

        raw = await self._secrets.decrypt(base64.b64decode(ciphertext), key_id)
        return str(raw["token"])

    # ── core entrypoint ───────────────────────────────────────────────

    async def charge_subscription(
        self,
        *,
        tenant_id: UUID,
        amount_cents: int,
        currency: str,
        encrypted_card_token: str,
        key_id: str,
        idempotency_ref: str,
    ) -> RecurringChargeResult:
        """Charge the merchant's stored card token for one period.

        ``idempotency_ref`` is forwarded to Paymob as ``order_id`` so
        re-running the renewal task before the webhook reconciles
        does not produce a duplicate charge.
        """
        if amount_cents <= 0:
            return RecurringChargeFailure(reason="amount_must_be_positive")

        try:
            token = await self._decrypt_card_token(encrypted_card_token, key_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(
                "recurring_token_decrypt_failed",
                extra={"tenant_id": str(tenant_id)},
            )
            return RecurringChargeFailure(
                reason=f"token_decrypt_failed: {exc}",
                error_code="decrypt_failed",
            )

        result = await self._paymob.charge_saved_token(
            card_token=token,
            amount=amount_cents,
            currency=currency,
            order_id=idempotency_ref,
        )

        if result.success and result.payment_id:
            return RecurringChargeSuccess(transaction_id=result.payment_id)

        return RecurringChargeFailure(
            reason=result.error_message or "charge_failed",
            error_code=result.error_code,
        )
