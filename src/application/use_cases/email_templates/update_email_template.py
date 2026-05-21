"""Update email template use case.

Patches an existing merchant template. ``None``-valued fields on the
DTO are left unchanged. The same validation gauntlet as create runs,
but only on fields that actually changed — sanitization / Jinja parse /
whitelist / smoke render are skipped for fields the merchant didn't
touch, so an unrelated update doesn't re-validate already-stored
content.

``event_type`` and ``language`` are intentionally NOT updatable here —
they're part of the unique key and changing them would be equivalent
to deleting and re-creating the row, which we expose as separate
operations.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from jinja2 import ChainableUndefined, Environment, select_autoescape
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from src.application.dto.email_template import (
    EmailTemplateDTO,
    UpdateEmailTemplateDTO,
)
from src.application.services.email_template_registry import (
    allowed_variables,
    get_event_spec,
)
from src.application.services.email_template_sanitizer import sanitize_email_html
from src.core.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


def _validate_jinja_syntax(source: str, *, field: str) -> None:
    try:
        Environment(autoescape=True).parse(source)
    except TemplateSyntaxError as exc:
        raise ValidationError(
            f"Invalid Jinja2 syntax in {field}: {exc.message} (line {exc.lineno})"
        ) from exc


def _smoke_render(subject: str, body: str, sample: dict[str, Any]) -> None:
    env = SandboxedEnvironment(
        autoescape=select_autoescape(["html", "htm"]),
        undefined=ChainableUndefined,
    )
    try:
        env.from_string(subject).render(**sample)
        env.from_string(body).render(**sample)
    except Exception as exc:  # noqa: BLE001
        raise ValidationError(
            f"Template failed smoke render against sample data: {exc}"
        ) from exc


class UpdateEmailTemplateUseCase:
    """Use case for updating an existing email template."""

    def __init__(
        self,
        email_template_repository: IEmailTemplateRepository,
        store_repository: IStoreRepository,
    ) -> None:
        self.email_template_repository = email_template_repository
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        template_id: UUID,
        dto: UpdateEmailTemplateDTO,
        user_id: UUID,
    ) -> EmailTemplateDTO:
        # ── Fetch + auth ────────────────────────────────────────────
        template = await self.email_template_repository.get_by_id(template_id)
        if not template or template.store_id != store_id:
            raise EntityNotFoundError("EmailTemplate", str(template_id))

        store = await self.store_repository.get_by_id(template.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to update this email template"
            )

        # ── Apply patches in-place ──────────────────────────────────
        subject_changed = False
        body_changed = False

        if dto.name is not None:
            if not dto.name.strip():
                raise ValidationError("Template name cannot be empty")
            template.name = dto.name.strip()

        if dto.subject is not None:
            if not dto.subject.strip():
                raise ValidationError("Template subject cannot be empty")
            template.subject = dto.subject
            subject_changed = True

        if dto.html_body is not None:
            if not dto.html_body.strip():
                raise ValidationError("Template body cannot be empty")
            template.html_body = sanitize_email_html(dto.html_body)
            body_changed = True

        if dto.is_enabled is not None:
            template.is_enabled = dto.is_enabled

        if dto.from_name is not None:
            template.from_name = dto.from_name

        if dto.reply_to is not None:
            template.reply_to = dto.reply_to

        if dto.extra_data is not None:
            template.extra_data = dto.extra_data

        # ── Re-validate touched template fields ─────────────────────
        if subject_changed:
            _validate_jinja_syntax(template.subject, field="subject")
        if body_changed:
            _validate_jinja_syntax(template.html_body, field="html_body")

        if subject_changed or body_changed:
            allowed = allowed_variables(template.event_type) | {"store_name"}
            unknown = template.references_unknown_variables(allowed)
            if unknown:
                raise ValidationError(
                    f"Template references unknown variables: {sorted(unknown)}. "
                    f"Allowed: {sorted(allowed)}"
                )

            spec = get_event_spec(template.event_type)
            _smoke_render(template.subject, template.html_body, spec.sample_data)

        template.touch()
        updated = await self.email_template_repository.update(template)
        return EmailTemplateDTO.from_entity(updated)
