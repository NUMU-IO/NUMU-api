"""Use case: merchant creates a wallet top-up intent.

Three methods, one table — all switchable from the admin panel
(``wallet_settings`` in platform_config):

* ``card`` — Kashier hosted payment session on NUMU's own (platform)
  account; the merchant is redirected and the platform Kashier webhook
  credits the wallet instantly.
* ``vodafone_cash`` — MANUAL: merchant transfers to NUMU's own Vodafone
  Cash number and uploads a receipt (:mod:`submit_topup_proof`). Not a
  gateway flow.
* ``instapay`` — MANUAL: reference + QR against NUMU's platform IPA,
  then receipt upload.

Manual receipts that don't auto-verify credit the wallet ON HOLD
(``pending_balance_cents``) so the merchant sees instant feedback while
an admin reviews.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.wallet_settings import (
    WalletAdminSettings,
    get_wallet_settings,
)
from src.config.settings import get_settings
from src.core.entities.wallet import TopupIntentStatus, TopupMethod
from src.infrastructure.database.models.public.user import UserModel
from src.infrastructure.database.models.public.wallet import WalletTopupIntentModel

logger = logging.getLogger(__name__)

MIN_TOPUP_CENTS = 5_000  # 50 EGP
MAX_TOPUP_CENTS = 5_000_000  # 50,000 EGP
MANUAL_EXPIRY_MINUTES = 30
GATEWAY_EXPIRY_HOURS = 24


@dataclass
class CreateTopupResult:
    intent: WalletTopupIntentModel
    # Card: hosted checkout redirect. Manual methods: None.
    checkout_url: str | None
    # Manual payload for the hub dialog (destination, reference, QR...).
    manual: dict | None


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
        admin = await get_wallet_settings(self.db)
        if not admin.topups_enabled and not self.settings.ff_wallet_topups:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Wallet top-ups are not enabled yet.",
            )
        if not admin.method_enabled(method.value):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This top-up method is currently disabled.",
            )
        if not MIN_TOPUP_CENTS <= amount_cents <= MAX_TOPUP_CENTS:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=(
                    f"Top-up amount must be between {MIN_TOPUP_CENTS // 100} "
                    f"and {MAX_TOPUP_CENTS // 100} EGP."
                ),
            )

        if method == TopupMethod.CARD:
            return await self._create_card(
                tenant_id=tenant_id, user_id=user_id, amount_cents=amount_cents
            )
        return await self._create_manual(
            tenant_id=tenant_id,
            user_id=user_id,
            method=method,
            amount_cents=amount_cents,
            admin=admin,
        )

    # ------------------------------------------------------------------
    # Card — Kashier hosted session on the platform account
    # ------------------------------------------------------------------

    async def _create_card(
        self, *, tenant_id: UUID, user_id: UUID, amount_cents: int
    ) -> CreateTopupResult:
        s = self.settings
        if not (s.platform_kashier_mid and s.platform_kashier_api_key):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Card top-ups are not configured.",
            )

        from src.infrastructure.external_services.kashier import (
            KashierPaymentService,
        )

        owner = (
            await self.db.execute(select(UserModel).where(UserModel.id == user_id))
        ).scalar_one_or_none()

        intent_id = uuid4()
        special_reference = f"WTOP-{intent_id}"
        api_base = s.platform_api_base_url.rstrip("/")
        service = KashierPaymentService(
            mid=s.platform_kashier_mid,
            api_key=s.platform_kashier_api_key,
            mode=s.platform_kashier_mode,
        )
        payment_intent = await service.create_payment_intent(
            amount=amount_cents,
            currency="EGP",
            customer_email=str(owner.email) if owner else None,
            metadata={
                "order_id": special_reference,
                "webhook_url": (
                    f"{api_base}/api/v1/webhooks/kashier/platform/callback"
                ),
                "redirect_url": (f"{s.merchant_hub_url}/wallet?topup_id={intent_id}"),
            },
        )

        intent = WalletTopupIntentModel(
            id=intent_id,
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            method=TopupMethod.CARD.value,
            amount_cents=amount_cents,
            currency="EGP",
            status=TopupIntentStatus.PENDING.value,
            special_reference=special_reference,
            gateway_reference=payment_intent.id,
            gateway_payload=payment_intent.client_secret,  # session URL
            expires_at=datetime.now(UTC) + timedelta(hours=GATEWAY_EXPIRY_HOURS),
        )
        self.db.add(intent)
        await self.db.flush()

        logger.info(
            "wallet_topup_intent_created",
            extra={
                "tenant_id": str(tenant_id),
                "intent_id": str(intent_id),
                "method": "card",
                "amount_cents": amount_cents,
            },
        )
        # Kashier's client_secret IS the hosted session URL.
        return CreateTopupResult(
            intent=intent,
            checkout_url=payment_intent.client_secret,
            manual=None,
        )

    # ------------------------------------------------------------------
    # Manual — Vodafone Cash / InstaPay against NUMU's own destination
    # ------------------------------------------------------------------

    async def _create_manual(
        self,
        *,
        tenant_id: UUID,
        user_id: UUID,
        method: TopupMethod,
        amount_cents: int,
        admin: WalletAdminSettings,
    ) -> CreateTopupResult:
        from src.infrastructure.external_services.instapay.payment_service import (
            generate_reference_code,
        )

        if method == TopupMethod.INSTAPAY:
            destination = admin.instapay_ipa
            destination_label = admin.instapay_display_name or destination
            prefix = "WT"
        else:  # vodafone_cash
            destination = admin.vodafone_cash_number
            destination_label = destination
            prefix = "VC"
        if not destination:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"{method.value} top-ups are not configured.",
            )

        # Retry the short code on the (rare) unique collision.
        reference = generate_reference_code(prefix=prefix)
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
            reference = generate_reference_code(prefix=prefix)

        qr_payload: str | None = None
        if method == TopupMethod.INSTAPAY:
            from src.infrastructure.external_services.instapay.qr_generator import (
                build_qr_payload,
            )

            qr_payload = build_qr_payload(
                ipa=destination,
                amount_cents=amount_cents,
                reference_code=reference,
                note=f"NUMU wallet top-up {reference}",
            )

        expires_at = datetime.now(UTC) + timedelta(minutes=MANUAL_EXPIRY_MINUTES)
        intent = WalletTopupIntentModel(
            tenant_id=tenant_id,
            created_by_user_id=user_id,
            method=method.value,
            amount_cents=amount_cents,
            currency="EGP",
            status=TopupIntentStatus.AWAITING_PROOF.value,
            special_reference=reference,
            display_destination=destination,
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
                "method": method.value,
                "amount_cents": amount_cents,
            },
        )
        return CreateTopupResult(
            intent=intent,
            checkout_url=None,
            manual={
                "method": method.value,
                "reference_code": reference,
                "destination": destination,
                "destination_label": destination_label,
                "qr_payload": qr_payload,
                "expires_at": expires_at.isoformat(),
            },
        )
