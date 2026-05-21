"""EmailTemplate DTOs.

Plain ``@dataclass`` DTOs (matching the pattern used by
``src/application/dto/category.py``) covering:

* :class:`EmailTemplateDTO` — full read-shape returned to merchant UI.
* :class:`CreateEmailTemplateDTO` / :class:`UpdateEmailTemplateDTO` — write
  payloads from the API layer to the use-case layer.
* :class:`RenderedEmailDTO` — the renderer's output, consumed by the
  send-email pipeline.
* :class:`DefaultTemplateDTO` — small read-shape for "give me the
  registry default for this (event, language)" lookups.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from src.application.dto.base import BaseDTO
from src.core.entities.email_template import EmailTemplate


@dataclass
class EmailTemplateDTO(BaseDTO):
    """Email template data transfer object."""

    id: UUID
    store_id: UUID
    tenant_id: UUID | None
    event_type: str
    language: str  # "ar" | "en"
    name: str
    subject: str
    html_body: str
    is_enabled: bool
    from_name: str | None
    reply_to: str | None
    extra_data: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_entity(cls, entity: EmailTemplate) -> "EmailTemplateDTO":
        """Create DTO from EmailTemplate entity."""
        return cls(
            id=entity.id,
            store_id=entity.store_id,
            tenant_id=entity.tenant_id,
            event_type=entity.event_type,
            language=entity.language,
            name=entity.name,
            subject=entity.subject,
            html_body=entity.html_body,
            is_enabled=entity.is_enabled,
            from_name=entity.from_name,
            reply_to=entity.reply_to,
            extra_data=dict(entity.extra_data or {}),
            created_at=entity.created_at,
            updated_at=entity.updated_at,
        )


@dataclass
class CreateEmailTemplateDTO(BaseDTO):
    """Create email template data transfer object."""

    event_type: str
    language: str
    name: str
    subject: str
    html_body: str
    is_enabled: bool = True
    from_name: str | None = None
    reply_to: str | None = None
    extra_data: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpdateEmailTemplateDTO(BaseDTO):
    """Update email template data transfer object.

    All fields optional — only the fields explicitly provided are
    applied. ``None`` means *unchanged*; this means there is no way to
    clear ``from_name`` / ``reply_to`` via update — the merchant must
    delete and re-create. That trade-off is deliberate to keep the
    update semantics simple.
    """

    name: str | None = None
    subject: str | None = None
    html_body: str | None = None
    is_enabled: bool | None = None
    from_name: str | None = None
    reply_to: str | None = None
    extra_data: dict[str, Any] | None = None


@dataclass
class RenderedEmailDTO(BaseDTO):
    """Rendered email — output of the :class:`EmailTemplateRenderer`."""

    subject: str
    html: str
    from_name: str | None
    reply_to: str | None
    used_custom: bool
    template_id: UUID | None


@dataclass
class DefaultTemplateDTO(BaseDTO):
    """Registry-default template for a given (event_type, language).

    Returned by the "preview defaults" use case so merchants can see what
    the system will send when no customization exists. No DB lookup —
    pulled straight from :data:`EMAIL_EVENT_REGISTRY`.
    """

    event_type: str
    language: str
    subject: str
    html_body: str
