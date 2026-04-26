"""EmailLog entity for transactional email audit trail.

Each row captures one email-send attempt for a store. Records are written
on enqueue (``queued``), updated when the provider accepts the request
(``sent`` / ``failed``), and updated again on delivery webhooks
(``delivered``).
"""

from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from src.core.entities.base import BaseEntity

# Lifecycle status of an email send attempt. Mirrors the literal type at
# both DB and entity boundaries — the column is a free String to avoid
# Postgres-enum migrations on small changes; validation happens here.
EmailStatus = Literal["queued", "sent", "failed", "delivered"]


class EmailLog(BaseEntity):
    """Domain entity for a single transactional-email send record."""

    store_id: UUID
    tenant_id: UUID | None = None
    recipient: str = Field(..., min_length=1, max_length=255)
    message_id: str | None = Field(default=None, max_length=255)
    event_type: str = Field(..., max_length=50)
    template_id: UUID | None = None
    language: str = Field(..., max_length=10)
    subject: str = Field(..., max_length=500)
    status: EmailStatus = "queued"
    error_code: str | None = Field(default=None, max_length=100)
    used_custom_template: bool = False
    extra_data: dict[str, Any] = Field(default_factory=dict)
