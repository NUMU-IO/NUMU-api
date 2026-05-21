"""Subscribe a tenant — trial or read-only → active paid plan.

Charges the merchant's Paymob card token via
``PaymobRecurringBillingService`` (or accepts a discount code that
covers the first period), creates an invoice, flips the tenant
lifecycle to active. Failure raises — no silent activation.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.application.services.paymob_recurring_billing_service import (
    PaymobRecurringBillingService,
    RecurringChargeFailure,
    RecurringChargeSuccess,
)
from src.infrastructure.database.models.public.billing import (
    BillingInvoiceModel,
    DiscountCodeModel,
)
from src.infrastructure.database.models.public.tenant import (
    TenantLifecycleState,
    TenantModel,
)
from src.infrastructure.tenancy.repository import TenantRepository

logger = logging.getLogger(__name__)


class SubscribeUseCase:
    """Activate a paying subscription for a tenant.

    Handles trial→active and read_only→active transitions.

    Args:
        db: Async session.
        recurring_service: Optional injected
            ``PaymobRecurringBillingService``. When omitted, the
            production singleton is constructed on demand using the
            platform's Paymob credentials from ``settings``.
    """

    def __init__(
        self,
        db: AsyncSession,
        recurring_service: PaymobRecurringBillingService | None = None,
    ) -> None:
        self.db = db
        self.tenant_repo = TenantRepository(db)
        self._recurring = recurring_service

    async def execute(
        self,
        tenant_id: UUID,
        plan: str,
        billing_cycle: str = "monthly",
        discount_code: str | None = None,
        paymob_card_token: str | None = None,
    ) -> TenantModel:
        tenant = await self.tenant_repo.get_by_id(tenant_id)
        if not tenant:
            raise ValueError("Tenant not found")

        if tenant.lifecycle_state == TenantLifecycleState.ACTIVE:
            logger.info(
                "subscribe_skipped_already_active", extra={"tenant_id": str(tenant_id)}
            )
            return tenant

        # Resolve plan pricing
        from src.core.entities.plan import get_plan_features

        features = get_plan_features(plan)
        if billing_cycle == "annual":
            amount = features.annual_price_piasters
        else:
            amount = features.monthly_price_piasters

        # Apply discount code if provided
        discount_amount = 0
        discount_code_id = None
        if discount_code:
            discount_amount, discount_code_id = await self._apply_discount(
                discount_code, amount, plan
            )

        final_amount = max(0, amount - discount_amount)

        # Charge via Paymob (real call). Free first period via discount → skip.
        paymob_tx_id: str | None = None
        encrypted_token: str | None = None
        now = datetime.now(UTC)
        if final_amount > 0:
            if not paymob_card_token:
                raise ValueError("Payment method required for this plan.")

            recurring = self._recurring or self._build_recurring_service()
            from src.config.settings import get_settings

            settings = get_settings()
            key_id = getattr(settings, "credential_encryption_key_id", "v1")

            encrypted_token = await recurring.encrypt_card_token(
                paymob_card_token, key_id
            )

            charge = await recurring.charge_subscription(
                tenant_id=tenant_id,
                amount_cents=final_amount,
                currency="EGP",
                encrypted_card_token=encrypted_token,
                key_id=key_id,
                idempotency_ref=f"first-period-{tenant_id}-{now.isoformat()}",
            )
            if isinstance(charge, RecurringChargeFailure):
                logger.warning(
                    "subscribe_charge_failed",
                    extra={
                        "tenant_id": str(tenant_id),
                        "reason": charge.reason,
                    },
                )
                raise ValueError(f"payment_failed: {charge.reason}")
            assert isinstance(charge, RecurringChargeSuccess)
            paymob_tx_id = charge.transaction_id

        period_end = now + (
            timedelta(days=365) if billing_cycle == "annual" else timedelta(days=30)
        )

        invoice = BillingInvoiceModel(
            tenant_id=tenant_id,
            period_start=now,
            period_end=period_end,
            amount_cents=final_amount,
            currency="EGP",
            status="paid",
            paymob_transaction_id=paymob_tx_id,
            discount_code_id=discount_code_id,
            discount_amount_cents=discount_amount,
            paid_at=now,
        )
        self.db.add(invoice)

        # Activate tenant
        tenant.lifecycle_state = TenantLifecycleState.ACTIVE
        tenant.plan = plan
        tenant.billing_cycle = billing_cycle
        tenant.subscription_started_at = now
        tenant.next_renewal_at = period_end
        tenant.expires_at = None
        tenant.read_only_at = None
        tenant.delete_at = None
        tenant.renewal_retry_count = 0
        if encrypted_token:
            tenant.paymob_card_token_encrypted = encrypted_token
        if not tenant.trial_converted_at:
            tenant.trial_converted_at = now

        await self.tenant_repo.update(tenant)
        await self.db.flush()

        logger.info(
            "subscription_started",
            extra={
                "tenant_id": str(tenant_id),
                "plan": plan,
                "billing_cycle": billing_cycle,
                "amount_cents": final_amount,
            },
        )
        return tenant

    async def _apply_discount(
        self, code: str, amount: int, plan: str
    ) -> tuple[int, UUID | None]:
        """Validate and apply a discount code. Returns (discount_amount, code_id)."""
        q = select(DiscountCodeModel).where(DiscountCodeModel.code == code.upper())
        result = await self.db.execute(q)
        dc = result.scalar_one_or_none()

        if not dc:
            raise ValueError(f"Invalid discount code: {code}")

        now = datetime.now(UTC)
        if dc.valid_from and now < dc.valid_from:
            raise ValueError("Discount code not yet active.")
        if dc.valid_until and now > dc.valid_until:
            raise ValueError("Discount code has expired.")
        if dc.max_uses and dc.current_uses >= dc.max_uses:
            raise ValueError("Discount code has been fully redeemed.")
        if dc.applies_to_plans and plan not in dc.applies_to_plans:
            raise ValueError(f"Discount code does not apply to {plan} plan.")

        if dc.type == "percent":
            discount = int(amount * dc.value / 100)
        elif dc.type == "fixed":
            discount = dc.value
        elif dc.type == "free_months":
            discount = amount  # first period free
        else:
            discount = 0

        dc.current_uses += 1
        self.db.add(dc)

        return discount, dc.id

    def _build_recurring_service(self) -> PaymobRecurringBillingService:
        """Construct the production recurring service from settings.

        Lazy import + lazy build so unit tests that inject their own
        ``recurring_service`` don't need the platform Paymob secrets
        configured.
        """
        from src.config.settings import get_settings
        from src.infrastructure.external_services.paymob.payment_service import (
            PaymobPaymentService,
        )

        settings = get_settings()
        paymob = PaymobPaymentService(
            secret_key=getattr(settings, "platform_paymob_secret_key", None),
            public_key=getattr(settings, "platform_paymob_public_key", None),
            hmac_secret=getattr(settings, "platform_paymob_hmac_secret", None),
            card_integration_id=getattr(
                settings, "platform_paymob_card_integration_id", None
            ),
        )
        return PaymobRecurringBillingService(paymob_service=paymob)
