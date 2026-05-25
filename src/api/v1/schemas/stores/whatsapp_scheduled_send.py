"""Pydantic v2 schemas for the WhatsApp scheduled-sends API surface."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ScheduledSend(BaseModel):
    """A row returned by GET /whatsapp/scheduled-sends."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    customer_id: UUID | None = None
    phone: str
    template_id: UUID | None = None
    template_params: dict[str, Any] | None = None
    text_message: str | None = None
    scheduled_for: datetime
    status: Literal["pending", "sent", "cancelled", "skipped", "failed"]
    skip_reason: str | None = None
    failure_reason: str | None = None
    related_order_id: UUID | None = None
    created_by: UUID | None = None
    created_at: datetime
    dispatched_at: datetime | None = None
    sent_at: datetime | None = None


class ScheduledSendCreate(BaseModel):
    """Request body for POST /whatsapp/scheduled-sends."""

    phone: str = Field(..., description="Will be canonicalized to E.164.")
    customer_id: UUID | None = None
    template_id: UUID | None = None
    template_params: dict[str, Any] | None = None
    text_message: str | None = Field(
        default=None,
        description="Free-form text. Only valid inside the 24h window — guard enforces at dispatch-time.",
    )
    scheduled_for: datetime
    related_order_id: UUID | None = None

    @model_validator(mode="after")
    def _exactly_one_payload(self) -> "ScheduledSendCreate":
        has_template = self.template_id is not None
        has_text = self.text_message is not None and self.text_message.strip()
        if has_template == has_text:
            raise ValueError(
                "Exactly one of template_id or text_message must be provided."
            )
        return self
