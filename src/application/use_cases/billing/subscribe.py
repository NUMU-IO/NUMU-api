"""Subscribe a tenant — trial or read-only → active paid plan.

Validates the payment method via Paymob recurring API (or accepts a
discount code that covers the first period), creates an invoice,
flips the tenant lifecycle to active.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db
        self.tenant_repo = TenantRepository(db)

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

        # TODO: Charge via Paymob recurring API using paymob_card_token
        # For v1, we accept the subscription if a valid discount covers it
        # or if the card token is provided (mocked as success for now).
        paymob_tx_id = None
        if final_amount > 0 and not paymob_card_token:
            raise ValueError("Payment method required for this plan.")

        # Create invoice
        now = datetime.now(UTC)
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
