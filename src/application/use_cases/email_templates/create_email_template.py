"""Create email template use case.

Validates the merchant-supplied template (event registered, language
allowed, no duplicate for this triple) and runs three layers of
template-content validation before persisting:

1. **HTML sanitization** — strips ``<script>``, ``onclick=""``, and
   anything not in the email-friendly allowlist.
2. **Jinja syntax check** — both subject and body are parsed via the
   stdlib :class:`jinja2.Environment` so syntax errors fail loudly with
   a clear ``ValidationError`` instead of silently breaking sends.
3. **Variable whitelist** — uses the entity's
   ``references_unknown_variables`` helper against
   :func:`allowed_variables` for that event so typos surface here.
4. **Smoke render** — render against ``spec.sample_data`` in the same
   sandbox the runtime renderer uses, so any runtime-only failures
   (e.g. a filter not allowed in the sandbox) are caught up front.

The store's ``tenant_id`` is propagated onto the template entity so RLS
sees it correctly on insert.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from jinja2 import ChainableUndefined, Environment, select_autoescape
from jinja2.exceptions import TemplateSyntaxError
from jinja2.sandbox import SandboxedEnvironment

from src.application.dto.email_template import (
    CreateEmailTemplateDTO,
    EmailTemplateDTO,
)
from src.application.services.email_template_registry import (
    EMAIL_EVENT_REGISTRY,
    allowed_variables,
    get_event_spec,
)
from src.application.services.email_template_sanitizer import sanitize_email_html
from src.core.entities.email_template import EmailTemplate
from src.core.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository

_ALLOWED_LANGS: set[str] = {"ar", "en"}


def _validate_jinja_syntax(source: str, *, field: str) -> None:
    """Raise :class:`ValidationError` if ``source`` is invalid Jinja2."""
    try:
        Environment(autoescape=True).parse(source)
    except TemplateSyntaxError as exc:
        raise ValidationError(
            f"Invalid Jinja2 syntax in {field}: {exc.message} (line {exc.lineno})"
        ) from exc


def _smoke_render(subject: str, body: str, sample: dict[str, Any]) -> None:
    """Render ``subject`` + ``body`` once in the runtime sandbox.

    Surfaces issues that only show up at render time (sandbox-blocked
    attribute access, missing filters, etc.) before the template ever
    reaches a real customer.
    """
    env = SandboxedEnvironment(
        autoescape=select_autoescape(["html", "htm"]),
        undefined=ChainableUndefined,
    )
    try:
        env.from_string(subject).render(**sample)
        env.from_string(body).render(**sample)
    except Exception as exc:  # noqa: BLE001 — surface as user-facing
        raise ValidationError(
            f"Template failed smoke render against sample data: {exc}"
        ) from exc


class CreateEmailTemplateUseCase:
    """Use case for creating a new merchant email template override."""

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
        dto: CreateEmailTemplateDTO,
        user_id: UUID,
    ) -> EmailTemplateDTO:
        # ── Auth: store must exist and be owned by the caller ───────
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to create email templates for this store"
            )

        # ── Field-level validation ──────────────────────────────────
        if dto.event_type not in EMAIL_EVENT_REGISTRY:
            raise ValidationError(
                f"Unknown event type: {dto.event_type!r}. "
                f"Allowed: {sorted(EMAIL_EVENT_REGISTRY.keys())}"
            )
        if dto.language not in _ALLOWED_LANGS:
            raise ValidationError(
                f"Invalid language {dto.language!r}; expected one of {sorted(_ALLOWED_LANGS)}"
            )
        if not dto.name or not dto.name.strip():
            raise ValidationError("Template name is required")
        if not dto.subject or not dto.subject.strip():
            raise ValidationError("Template subject is required")
        if not dto.html_body or not dto.html_body.strip():
            raise ValidationError("Template body is required")

        # ── Uniqueness on (store_id, event_type, language) ──────────
        existing = await self.email_template_repository.get_by_store_event_language(
            store_id, dto.event_type, dto.language
        )
        if existing is not None:
            raise ValidationError(
                f"Template already exists for event {dto.event_type!r} "
                f"and language {dto.language!r}"
            )

        # ── Sanitize HTML, validate Jinja, check vars, smoke render ─
        sanitized_body = sanitize_email_html(dto.html_body)
        _validate_jinja_syntax(dto.subject, field="subject")
        _validate_jinja_syntax(sanitized_body, field="html_body")

        spec = get_event_spec(dto.event_type)
        allowed = allowed_variables(dto.event_type) | {"store_name"}

        # Build a transient entity to use the helper for var validation.
        candidate = EmailTemplate(
            store_id=store_id,
            tenant_id=store.tenant_id,
            event_type=dto.event_type,
            language=dto.language,
            name=dto.name.strip(),
            subject=dto.subject,
            html_body=sanitized_body,
            is_enabled=dto.is_enabled,
            from_name=dto.from_name,
            reply_to=dto.reply_to,
            extra_data=dto.extra_data or {},
        )
        unknown = candidate.references_unknown_variables(allowed)
        if unknown:
            raise ValidationError(
                f"Template references unknown variables: {sorted(unknown)}. "
                f"Allowed: {sorted(allowed)}"
            )

        _smoke_render(candidate.subject, candidate.html_body, spec.sample_data)

        # ── Persist ────────────────────────────────────────────────
        created = await self.email_template_repository.create(candidate)
        return EmailTemplateDTO.from_entity(created)
