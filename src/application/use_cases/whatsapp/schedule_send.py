"""Schedule a future-dated WhatsApp send (FR-013 / US3).

Validates:
- Phone is E.164-canonicalizable (else InvalidPhoneError → 422).
- ``scheduled_for`` is in the future.
- Exactly one of ``template_id`` / ``text_message`` is supplied
  (the DB CHECK constraint enforces this too — we surface a clear
  error at the API boundary first).
- If ``template_id`` is supplied, the local template's ``status``
  is ``APPROVED`` (FR-029: sends refuse non-APPROVED templates;
  catching this at schedule-time gives the merchant immediate
  feedback instead of waiting for dispatch-time skip).
"""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.value_objects.phone import InvalidPhoneError, PhoneNumber
from src.infrastructure.database.models.tenant.store import StoreModel
from src.infrastructure.database.models.tenant.whatsapp_scheduled_send import (
    WhatsAppScheduledSendModel,
)
from src.infrastructure.database.models.tenant.whatsapp_template import (
    WhatsAppTemplateModel,
)
from src.infrastructure.repositories.whatsapp_scheduled_send_repository import (
    WhatsAppScheduledSendRepository,
)


class ScheduleSendError(Exception):
    """Raised when a scheduled-send request fails validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ScheduleSendUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = WhatsAppScheduledSendRepository(session)

    async def execute(
        self,
        *,
        store_id: UUID,
        phone: str,
        scheduled_for: datetime,
        template_id: UUID | None = None,
        template_params: dict[str, Any] | None = None,
        text_message: str | None = None,
        customer_id: UUID | None = None,
        related_order_id: UUID | None = None,
        created_by: UUID | None = None,
    ) -> WhatsAppScheduledSendModel:
        # 1. Phone canonicalization
        try:
            phone_e164 = PhoneNumber.parse(phone.strip(), default_region="EG").e164
        except InvalidPhoneError as exc:
            raise ScheduleSendError("invalid_phone", str(exc)) from exc

        # 2. scheduled_for must be in the future
        now = datetime.now(UTC)
        if scheduled_for.tzinfo is None:
            scheduled_for = scheduled_for.replace(tzinfo=UTC)
        if scheduled_for <= now:
            raise ScheduleSendError(
                "scheduled_in_past",
                "scheduled_for must be in the future.",
            )

        # 3. XOR template_id / text_message
        has_template = template_id is not None
        has_text = text_message is not None and text_message.strip()
        if has_template == has_text:
            raise ScheduleSendError(
                "payload_invalid",
                "Exactly one of template_id or text_message must be provided.",
            )

        # 4. Tenant resolution
        tenant_row = (
            await self.session.execute(
                select(StoreModel.tenant_id).where(StoreModel.id == store_id)
            )
        ).scalar_one_or_none()
        if tenant_row is None:
            raise ScheduleSendError("store_not_found", f"Store {store_id} not found.")
        tenant_id: UUID = tenant_row

        # 5. Template status check — must be APPROVED at schedule time. The
        # dispatcher will re-check at fire time (FR-017) in case status
        # changed between schedule and dispatch.
        if template_id is not None:
            tmpl = (
                await self.session.execute(
                    select(WhatsAppTemplateModel).where(
                        WhatsAppTemplateModel.id == template_id,
                        WhatsAppTemplateModel.store_id == store_id,
                    )
                )
            ).scalar_one_or_none()
            if tmpl is None:
                raise ScheduleSendError(
                    "template_not_found",
                    f"Template {template_id} not found in store {store_id}.",
                )
            if tmpl.status != "APPROVED":
                raise ScheduleSendError(
                    "template_not_approved",
                    f"Template '{tmpl.name}' status is {tmpl.status}; only APPROVED templates may be scheduled.",
                )

        # 6. Persist
        return await self.repo.create(
            tenant_id=tenant_id,
            store_id=store_id,
            phone=phone_e164,
            scheduled_for=scheduled_for,
            template_id=template_id,
            template_params=template_params,
            text_message=text_message,
            customer_id=customer_id,
            related_order_id=related_order_id,
            created_by=created_by,
        )
