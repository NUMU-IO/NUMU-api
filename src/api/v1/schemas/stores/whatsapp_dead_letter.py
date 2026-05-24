"""Pydantic v2 schemas for the WhatsApp dead-letters API surface."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DeadLetterError(BaseModel):
    """One row inside `error_history`."""

    attempt_n: int
    at: datetime
    http_status: int | None = None
    meta_error_code: str | None = None
    error_message: str


class DeadLetter(BaseModel):
    """A row returned by GET /whatsapp/dead-letters."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    phone: str
    customer_id: UUID | None = None
    template_id: UUID | None = None
    template_params: dict[str, Any] | None = None
    text_message: str | None = None
    originating_context: Literal[
        "order_created",
        "order_paid",
        "order_status_changed",
        "campaign",
        "scheduled_send",
        "abandoned_cart",
        "ad_hoc",
    ]
    originating_context_id: UUID | None = None
    error_history: list[DeadLetterError]
    error_classification: Literal["retriable_exhausted", "non_retriable"]
    final_error_code: str | None = None
    replay_state: Literal[
        "not_replayed", "replaying", "replayed_success", "replayed_failed"
    ]
    replayed_at: datetime | None = None
    replayed_by: UUID | None = None
    replayed_send_id: UUID | None = None
    created_at: datetime
