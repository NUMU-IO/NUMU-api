"""Renewal sweep: a card already charged must stay recorded when a later
tenant in the same batch fails — otherwise the task retry charges it again."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.infrastructure.messaging.tasks.subscription_renewal_task as renewal_mod
from src.application.services.billing_settings import invalidate_billing_settings_cache
from src.application.services.paymob_recurring_billing_service import (
    PaymobRecurringBillingService,
    RecurringChargeSuccess,
)
from src.infrastructure.database import connection
from src.infrastructure.database.models.public.billing import BillingInvoiceModel
from src.infrastructure.database.models.public.tenant import TenantModel


@pytest.mark.asyncio
async def test_charged_tenant_survives_a_later_tenant_failure(test_engine, monkeypatch):
    invalidate_billing_settings_cache()
    factory = async_sessionmaker(
        test_engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(connection, "AsyncSessionLocal", factory)

    async with factory() as session:
        for _ in range(2):
            session.add(
                TenantModel(
                    id=uuid4(),
                    name="Renewing",
                    subdomain=f"rn-{uuid4().hex[:8]}",
                    plan="starter",
                    lifecycle_state="active",
                    billing_cycle="monthly",
                    next_renewal_at=datetime.now(UTC) - timedelta(hours=1),
                    paymob_card_token_encrypted="enc-token",
                )
            )
        await session.commit()

    charged: list = []

    async def fake_charge(self, *, tenant_id, **_):
        charged.append(tenant_id)
        return RecurringChargeSuccess(transaction_id=f"tx-{tenant_id}")

    monkeypatch.setattr(
        PaymobRecurringBillingService, "charge_subscription", fake_charge
    )

    async def credit_then_fail_second(session, invoice):
        if len(charged) == 2:
            # A DB error on the second tenant poisons the session.
            session.add(TenantModel(id=charged[0], name="x", subdomain="x"))
            await session.flush()

    monkeypatch.setattr(renewal_mod, "credit_invoice", credit_then_fail_second)

    result = await renewal_mod._async_run(batch_size=10)

    assert result["succeeded"] == 1
    async with factory() as session:
        invoices = (await session.execute(select(BillingInvoiceModel))).scalars().all()
    assert [inv.tenant_id for inv in invoices] == [charged[0]]
    invalidate_billing_settings_cache()
