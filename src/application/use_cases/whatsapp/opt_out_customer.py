"""Revoke a customer's active WhatsApp opt-in for a store (FR-009/010).

Idempotent: when no active opt-in row exists, the use case is a no-op
and returns None. Used by:
- The inbound webhook STOP-keyword handler (reason='inbound_stop_keyword')
- The merchant /opt-ins/revoke endpoint (merchant_revoke /
  customer_request_via_support / api_revoke)
"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.database.models.tenant.whatsapp_opt_in import (
    WhatsAppOptInModel,
)
from src.infrastructure.repositories.whatsapp_opt_in_repository import (
    WhatsAppOptInRepository,
)


class OptOutCustomerUseCase:
    """Flip the active opt-in row for (store, phone) to opted-out.

    Returns the updated row, or None if no active row existed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WhatsAppOptInRepository(session)

    async def execute(
        self,
        *,
        store_id: UUID,
        phone: str,
        reason: str,
    ) -> WhatsAppOptInModel | None:
        try:
            phone_e164 = PhoneNumber.parse(phone.strip(), default_region="EG").e164
        except InvalidPhoneError:
            raise

        return await self.repo.revoke_active(store_id, phone_e164, reason)
