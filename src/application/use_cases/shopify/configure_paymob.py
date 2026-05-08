"""Configure a merchant's Paymob credentials (backend-018).

Sprint-1 audit: ``POST /shopify/{store_id}/settings/paymob`` was a
stub that flipped ``paymob_connected=True`` without validating the
key or persisting credentials. Merchants saw "Connected" then their
payment recovery silently failed because no credentials had actually
landed.

This use case validates the merchant's Paymob secret key against
Paymob's live intention API with a $0.01 test charge, encrypts the
credentials via the existing ``secrets_manager``, persists them on
``StoreModel.settings.payment.paymob.encrypted_credentials``, and only
then flips ``paymob_connected=True``.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.store import StoreModel

logger = logging.getLogger(__name__)


PAYMOB_INTENTION_TEST_URL = "https://accept.paymob.com/v1/intention/"


@dataclass(frozen=True)
class ConfigureSuccess:
    """Validation passed; credentials persisted."""


@dataclass(frozen=True)
class ConfigureFailure:
    """Paymob rejected the validation; nothing persisted."""

    reason: str
    status_code: int | None = None


ConfigureResult = ConfigureSuccess | ConfigureFailure


async def configure_paymob_credentials(
    *,
    session: AsyncSession,
    store_id: UUID,
    secret_key: str,
    public_key: str,
    hmac_secret: str,
    card_integration_id: str,
    wallet_integration_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ConfigureResult:
    """Validate + persist a merchant's Paymob credential set.

    Validation: a $0.01 test ``intention`` POST with ``is_test: true``.
    Paymob returns a 200/201 with a ``client_secret`` on success; any
    other response is a configuration failure (most often a wrong
    secret_key or wrong integration_id).

    Persistence: encrypts the full credential dict via the platform
    ``secrets_manager`` and stores the base64 envelope at
    ``store.settings.payment.paymob.encrypted_credentials`` along with
    the active ``encryption_key_id``. Format matches the existing
    ``get_merchant_paymob_credentials`` reader in
    ``infrastructure/external_services/paymob/payment_service.py``.
    """
    payload = {
        "amount": 1,  # $0.01 — smallest legal Paymob test charge
        "currency": "EGP",
        "payment_methods": [int(card_integration_id)],
        "billing_data": {
            "first_name": "Numu",
            "last_name": "Test",
            "email": "test@numu.local",
            "phone_number": "+201000000000",
            "country": "EG",
            "city": "Cairo",
            "street": "NA",
            "building": "NA",
            "apartment": "NA",
            "floor": "NA",
            "postal_code": "NA",
            "state": "NA",
            "shipping_method": "NA",
        },
        "is_test": True,
        "special_reference": f"numu-validation-{store_id}",
    }

    owns_client = http_client is None
    client = http_client or httpx.AsyncClient(timeout=httpx.Timeout(10.0))
    try:
        try:
            response = await client.post(
                PAYMOB_INTENTION_TEST_URL,
                json=payload,
                headers={"Authorization": f"Token {secret_key}"},
            )
        except httpx.TimeoutException as exc:
            return ConfigureFailure(reason=f"paymob_timeout: {exc}")
        except httpx.HTTPError as exc:
            return ConfigureFailure(reason=f"paymob_unreachable: {exc}")

        if response.status_code not in (200, 201):
            # Paymob returns a useful error body; surface a slice of
            # it back so merchants know what to fix.
            try:
                detail = response.json()
            except ValueError:
                detail = {"raw": response.text[:200]}
            return ConfigureFailure(
                reason=f"paymob_rejected: {detail}",
                status_code=response.status_code,
            )

        data = response.json()
        if not data.get("client_secret") and not data.get("intention_detail"):
            return ConfigureFailure(
                reason="paymob_response_missing_intention",
                status_code=response.status_code,
            )
    finally:
        if owns_client:
            await client.aclose()

    # Validation passed — encrypt + persist.
    from src.infrastructure.external_services.secrets.secrets_manager import (
        get_secrets_manager,
    )

    secrets = get_secrets_manager()
    key_id = await secrets.get_current_key_id()
    encrypted = await secrets.encrypt(
        {
            "secret_key": secret_key,
            "public_key": public_key,
            "hmac_secret": hmac_secret,
            "card_integration_id": card_integration_id,
            "wallet_integration_id": wallet_integration_id,
        },
        key_id,
    )
    encrypted_b64 = base64.b64encode(encrypted).decode("ascii")

    store_q = await session.execute(select(StoreModel).where(StoreModel.id == store_id))
    store = store_q.scalar_one_or_none()
    if store is None:
        return ConfigureFailure(reason="store_not_found")

    settings = dict(store.settings or {})
    payment = dict(settings.get("payment") or {})
    payment["paymob"] = {
        "encrypted_credentials": encrypted_b64,
        "encryption_key_id": key_id,
    }
    settings["payment"] = payment
    store.settings = settings

    await session.flush()
    logger.info(
        "paymob_credentials_persisted",
        extra={"store_id": str(store_id), "key_id": key_id},
    )
    return ConfigureSuccess()
