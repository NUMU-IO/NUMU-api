"""Pydantic schemas for store email-template endpoints.

These schemas live on the API boundary — they validate incoming
requests and shape the JSON shipped back to merchants. They mirror the
:mod:`src.application.dto.email_template` DTOs but are HTTP-flavored
(``EmailStr``, ``Literal`` for language, etc.) and intentionally
disallow updating the ``event_type`` / ``language`` fields — those
form the unique key on the row and changing them is equivalent to a
delete + create.
"""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class EmailVariableInfo(BaseModel):
    """Single variable descriptor for the merchant template editor."""

    name: str = Field(description="Variable name as referenced in {{ ... }}")
    description: str = Field(description="Developer-facing description")


class EmailEventResponse(BaseModel):
    """Registry entry for a single email event."""

    event_type: str = Field(description="Stable event identifier")
    label_en: str = Field(description="Human label in English")
    label_ar: str = Field(description="Human label in Arabic")
    variables: dict[str, str] = Field(
        description="Variable name → description map", default_factory=dict
    )
    sample_data: dict[str, Any] = Field(
        description="Sample values used for previews", default_factory=dict
    )
    default_subject_en: str = Field(description="Registry default English subject")
    default_subject_ar: str = Field(description="Registry default Arabic subject")


class DefaultTemplateResponse(BaseModel):
    """Registry-default subject + body for one (event_type, language)."""

    event_type: str
    language: Literal["ar", "en"]
    subject: str
    html_body: str


class CreateEmailTemplateRequest(BaseModel):
    """Body for ``POST /stores/{store_id}/email-templates``."""

    event_type: str = Field(..., max_length=50)
    language: Literal["ar", "en"]
    name: str = Field(..., min_length=1, max_length=255)
    subject: str = Field(..., min_length=1, max_length=500)
    html_body: str = Field(..., min_length=1)
    is_enabled: bool = True
    from_name: str | None = Field(None, max_length=255)
    reply_to: EmailStr | None = None
    extra_data: dict[str, Any] | None = None


class UpdateEmailTemplateRequest(BaseModel):
    """Body for ``PUT /stores/{store_id}/email-templates/{id}``.

    All fields are optional — ``None`` means *unchanged*.
    """

    name: str | None = Field(None, min_length=1, max_length=255)
    subject: str | None = Field(None, min_length=1, max_length=500)
    html_body: str | None = Field(None, min_length=1)
    is_enabled: bool | None = None
    from_name: str | None = Field(None, max_length=255)
    reply_to: EmailStr | None = None
    extra_data: dict[str, Any] | None = None


class EmailTemplateResponse(BaseModel):
    """Read-shape returned for a single template."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    store_id: UUID
    event_type: str
    language: str
    name: str
    subject: str
    html_body: str
    is_enabled: bool
    from_name: str | None
    reply_to: str | None
    extra_data: dict[str, Any] | None
    created_at: datetime
    updated_at: datetime


class PreviewEmailRequest(BaseModel):
    """Body for ``POST /stores/{store_id}/email-templates/{id}/preview``."""

    variables: dict[str, Any] | None = None


class PreviewDraftRequest(BaseModel):
    """Body for ``POST /stores/{store_id}/email-templates/preview-draft``.

    Renders the in-flight editor buffer without persisting anything — used
    by the merchant editor for live preview while the merchant is typing.
    """

    event_type: str = Field(..., max_length=50)
    language: Literal["ar", "en"]
    subject: str = Field(..., min_length=1, max_length=500)
    html_body: str = Field(..., min_length=1)
    variables: dict[str, Any] | None = None


class PreviewEmailResponse(BaseModel):
    """Rendered preview output."""

    subject: str
    html: str


class SendTestEmailRequest(BaseModel):
    """Body for ``POST /stores/{store_id}/email-templates/{id}/send-test``."""

    recipient: EmailStr
    variables: dict[str, Any] | None = None


class SendTestEmailResponse(BaseModel):
    """Send-test outcome."""

    sent: bool
    message_id: str | None = None
