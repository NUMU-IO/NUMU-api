"""Create or restore a customer's WhatsApp opt-in for a store (FR-006/007/012).

History-preserving: re-opting after a prior opt-out always creates a NEW
row; existing rows are never mutated to reset opted_out_at. Idempotent on
"already active" (no extra row written when an active opt-in already
exists).
"""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.whatsapp_opt_in import (
    WhatsAppOptInModel,
)
from src.infrastructure.repositories.whatsapp_opt_in_repository import (
    WhatsAppOptInRepository,
)


class OptInCustomerUseCase:
    """Idempotent opt-in for (store, phone).

    - Canonicalizes phone to E.164.
    - No-ops when an active opt-in row already exists for (store, phone).
    - Otherwise inserts a new row, preserving any prior opted-out history.
    - If a ``customer_id`` is supplied, also backfills it onto any opt-in
      rows for this (store, phone) whose ``customer_id`` is still NULL
      (storefront guest checkout writes phone first; merging happens
      later when the customer record is identified — FR-007).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WhatsAppOptInRepository(session)

    async def execute(
        self,
        *,
        store_id: UUID,
        phone: str,
        source: str,
        customer_id: UUID | None = None,
    ) -> WhatsAppOptInModel:
        # 1. Canonicalize phone — raises InvalidPhoneError on bad input.
        try:
            phone_e164 = PhoneNumber.parse(phone.strip(), default_region="EG").e164
        except InvalidPhoneError:
            raise

        # 2. Resolve tenant_id from the store (RLS-aware; the caller's
        #    session must have app.current_tenant set, or the resolver
        #    must run inside RLSBypassContext for cross-tenant lookups).
        tenant_row = (
            await self.session.execute(
                select(StoreModel.tenant_id).where(StoreModel.id == store_id)
            )
        ).scalar_one_or_none()
        if tenant_row is None:
            raise ValueError(f"Store {store_id} not found for opt-in.")
        tenant_id: UUID = tenant_row

        # 3. Idempotency: if an active row already exists for (store, phone),
        #    just backfill the customer link (if missing) and return it.
        existing_active = await self.repo.get_active(store_id, phone_e164)
        if existing_active is not None:
            if customer_id is not None and existing_active.customer_id is None:
                await self.repo.attach_customer(store_id, phone_e164, customer_id)
            return existing_active

        # 4. No active row — insert one. The prior opted-out row(s),
        #    if any, are preserved untouched (FR-012 history).
        row = await self.repo.create(
            tenant_id=tenant_id,
            store_id=store_id,
            phone=phone_e164,
            source=source,
            customer_id=customer_id,
        )
        # 5. Backfill customer_id onto historical rows for this phone too,
        #    in case the merchant later wants to associate them.
        if customer_id is not None:
            await self.repo.attach_customer(store_id, phone_e164, customer_id)
        return row
