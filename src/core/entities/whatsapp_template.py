"""WhatsApp template entity."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field

from .base import BaseEntity


class TemplateCategory(StrEnum):
    """Category of WhatsApp template."""

    MARKETING = "MARKETING"
    UTILITY = "UTILITY"
    AUTHENTICATION = "AUTHENTICATION"


class TemplateStatus(StrEnum):
    """Status of a WhatsApp template."""

    DRAFT = "DRAFT"
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PAUSED = "PAUSED"
    DISABLED = "DISABLED"


class WhatsAppTemplate(BaseEntity):
    """Represents a WhatsApp message template."""

    tenant_id: UUID
    store_id: UUID
    channel_connection_id: UUID
    external_template_id: str | None = None
    name: str
    category: TemplateCategory
    language: str
    status: TemplateStatus = TemplateStatus.DRAFT
    components: dict[str, Any] = Field(default_factory=dict)
    rejection_reason: str | None = None
    submitted_at: datetime | None = None
    approved_at: datetime | None = None

    def is_approved(self) -> bool:
        """Check if template is approved."""
        return self.status == TemplateStatus.APPROVED

    def is_pending(self) -> bool:
        """Check if template is pending approval."""
        return self.status == TemplateStatus.PENDING

    def submit(self) -> None:
        """Mark template as submitted for approval."""
        self.status = TemplateStatus.PENDING
        self.submitted_at = datetime.now(UTC)
        self.touch()

    def approve(self) -> None:
        """Mark template as approved."""
        self.status = TemplateStatus.APPROVED
        self.approved_at = datetime.now(UTC)
        self.touch()

    def reject(self, reason: str) -> None:
        """Mark template as rejected with reason."""
        self.status = TemplateStatus.REJECTED
        self.rejection_reason = reason
        self.touch()
