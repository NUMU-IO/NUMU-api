"""Use case: merchant creates a wallet top-up intent.

Three methods, one table:

* ``paymob_card`` / ``paymob_wallet`` — one Paymob Intention on NUMU's own
  (platform) account carries both card and mobile-wallet (Vodafone Cash
  etc.) integrations; the merchant is redirected to Paymob's hosted
  Unified Checkout and the platform webhook credits the wallet.
* ``instapay`` — reference code + QR against NUMU's platform IPA; the
  merchant transfers out-of-band and uploads a receipt
  (:mod:`submit_topup_proof`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.settings import get_settings
from src.core.entities.wallet import TopupIntentStatus, TopupMethod
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.wallet import WalletTopupIntentModel

logger = logging.getLogger(__name__)

MIN_TOPUP_CENTS = 5_000  # 50 EGP
MAX_TOPUP_CENTS = 5_000_000  # 50,000 EGP
INSTAPAY_EXPIRY_MINUTES = 30
PAYMOB_EXPIRY_HOURS = 24

_PAYMOB_UNIFIED_CHECKOUT = "https://accept.paymob.com/unifiedcheckout/"


@dataclass
class CreateTopupResult:
    intent: WalletTopupIntentModel
    # Paymob: hosted checkout redirect. InstaPay: None.
    checkout_url: str | None
    # InstaPay payload for the hub dialog. Paymob: None.
    instapay: dict | None


class CreateTopupUseCase:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.settings = get_settings()

    async def execute(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        method: TopupMethod,
        amount_cents: int,
    ) -> CreateTopupResult:
        if not self.settings.ff_wallet_topups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Wallet top-ups are not enabled yet.",
            )
        if not MIN_TOPUP_CENTS <= amount_cents <= MAX_TOPUP_CENTS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Top-up amount must be between {MIN_TOPUP_CENTS // 100} "
                    f"and {MAX_TOPUP_CENTS // 100} EGP."
                ),
            )

        if method in (TopupMethod.PAYMOB_CARD, TopupMethod.PAYMOB_WALLET):
            return await self._create_paymob(
                tenant_id=tenant_id,
                user_id=user_id,
                method=method,
                amount_cents=amount_cents,
            )
        return await self._create_instapay(
            tenant_id=tenant_id, user_id=user_id, amount_cents=amount_cents
        )

    # ------------------------------------------------------------------
    # Paymob (card + Vodafone Cash on the platform account)
    # ------------------------------------------------------------------

    async def _create_paymob(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        method: TopupMethod,
        amount_cents: int,
    ) -> CreateTopupResult:
        s = self.settings
        if not (s.platform_paymob_secret_key and s.platform_paymob_public_key):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Card / mobile-wallet top-ups are not configured.",
            )
        if (
            method == TopupMethod.PAYMOB_WALLET
            and not s.platform_paymob_wallet_integration_id
        ):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Mobile-wallet top-ups are not configured.",
            )

        from src.infrastructure.external_services.paymob.payment_service import (
            PaymobPaymentService,
        )

        owner = (
            await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        ).scalar_one_or_none()

        intent_id = uuid4()
        special_reference = f"WTOP-{intent_id}"
        service = PaymobPaymentService(
            secret_key=s.platform_paymob_secret_key,
            public_key=s.platform_paymob_public_key,
            hmac_secret=s.platform_paymob_hmac_secret,
            card_integration_id=s.platform_paymob_card_integration_id,
            wallet_integration_id=s.platform_paymob_wallet_integration_id,
        )
        api_base = s.platform_api_base_url.rstrip("/")
        billing: dict = {}
        if owner is not None:
            billing = {
                "first_name": owner.first_name or "Merchant",
                "last_name": owner.last_name or "NA",
                "phone_number": owner.phone or "+201000000000",
            }
        payment_intent = await service.create_payment_intent(
            amount=amount_cents,
            currency="EGP",
            customer_email=str(owner.email) if owner else None,
            metadata={
                "order_id": special_reference,
                "notification_url": (
                    f"{api_base}/api/v1/webhooks/paymob/platform/callback"
                ),
                "redirection_url": (
                    f"{s.merchant_hub_url}/wallet?topup_id={intent_id}"
                ),
                "billing_data": billing,
            },
        )

        intent = WalletTopupIntentModel(
            id=intent_id,
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            method=method.value,
            amount_cents=amount_cents,
            currency="EGP",
            status=TopupIntentStatus.PENDING.value,
            special_reference=special_reference,
            paymob_intention_id=payment_intent.id,
            paymob_client_secret=payment_intent.client_secret,
            expires_at=datetime.now(UTC) + timedelta(hours=PAYMOB_EXPIRY_HOURS),
        )
        self.db.add(intent)
        await self.db.flush()

        checkout_url = (
            f"{_PAYMOB_UNIFIED_CHECKOUT}"
            f"?publicKey={s.platform_paymob_public_key}"
            f"&clientSecret={payment_intent.client_secret}"
        )
        logger.info(
            "wallet_topup_intent_created",
            extra={
                "tenant_id": str(tenant_id),
                "intent_id": str(intent_id),
                "method": method.value,
                "amount_cents": amount_cents,
            },
        )
        return CreateTopupResult(
            intent=intent, checkout_url=checkout_url, instapay=None
        )

    # ------------------------------------------------------------------
    # InstaPay (platform IPA, proof-upload notary flow)
    # ------------------------------------------------------------------

    async def _create_instapay(
        self, *, tenant_id: UUID, user_id: UUID, amount_cents: int
    ) -> CreateTopupResult:
        s = self.settings
        if not s.platform_instapay_ipa:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="InstaPay top-ups are not configured.",
            )

        from src.infrastructure.external_services.instapay.payment_service import (
            generate_reference_code,
        )
        from src.infrastructure.external_services.instapay.qr_generator import (
            build_qr_payload,
        )

        # Retry the short code on the (rare) unique collision.
        reference = generate_reference_code(prefix="WT")
        for _ in range(3):
            exists = (
                await self.db.execute(
                    select(WalletTopupIntentModel.id).where(
                        WalletTopupIntentModel.special_reference == reference
                    )
                )
            ).scalar_one_or_none()
            if exists is None:
                break
            reference = generate_reference_code(prefix="WT")

        qr_payload = build_qr_payload(
            ipa=s.platform_instapay_ipa,
            amount_cents=amount_cents,
            reference_code=reference,
            note=f"NUMU wallet top-up {reference}",
        )
        expires_at = datetime.now(UTC) + timedelta(minutes=INSTAPAY_EXPIRY_MINUTES)
        intent = WalletTopupIntentModel(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            method=TopupMethod.INSTAPAY.value,
            amount_cents=amount_cents,
            currency="EGP",
            status=TopupIntentStatus.AWAITING_PROOF.value,
            special_reference=reference,
            display_ipa=s.platform_instapay_ipa,
            qr_payload=qr_payload,
            expires_at=expires_at,
        )
        self.db.add(intent)
        await self.db.flush()

        logger.info(
            "wallet_topup_intent_created",
            extra={
                "tenant_id": str(tenant_id),
                "intent_id": str(intent.id),
                "method": "instapay",
                "amount_cents": amount_cents,
            },
        )
        return CreateTopupResult(
            intent=intent,
            checkout_url=None,
            instapay={
                "reference_code": reference,
                "ipa": s.platform_instapay_ipa,
                "ipa_display_name": s.platform_instapay_display_name
                or s.platform_instapay_ipa,
                "qr_payload": qr_payload,
                "expires_at": expires_at.isoformat(),
            },
        )
