"""Get default template use case.

Pure-registry lookup — no DB read for the template content. Store
ownership is still verified so we don't trivially expose the registry
to anonymous users via this endpoint (the registry contents themselves
are public, but pinning the lookup to a specific store keeps the API
surface uniform with the rest of the email-templates routes).
"""

from __future__ import annotations

from uuid import UUID

from src.application.dto.email_template import DefaultTemplateDTO
from src.application.services.email_template_registry import (
    EMAIL_EVENT_REGISTRY,
    get_event_spec,
)
from src.core.exceptions import (
    AuthorizationError,
    EntityNotFoundError,
    ValidationError,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.infrastructure.external_services.resend.email_templates._base import (
    header as _email_header,
)
from src.infrastructure.external_services.resend.email_templates._base import (
    wrap as _wrap_with_chrome,
)

_ALLOWED_LANGS: set[str] = {"ar", "en"}


class GetDefaultTemplateUseCase:
    """Use case for fetching the registry default for an event/language."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        event_type: str,
        language: str,
        user_id: UUID,
    ) -> DefaultTemplateDTO:
        # Auth check kept for parity with other email-template endpoints.
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to access this store's templates"
            )

        if event_type not in EMAIL_EVENT_REGISTRY:
            raise ValidationError(
                f"Unknown event type: {event_type!r}. "
                f"Allowed: {sorted(EMAIL_EVENT_REGISTRY.keys())}"
            )
        if language not in _ALLOWED_LANGS:
            raise ValidationError(
                f"Invalid language {language!r}; expected one of {sorted(_ALLOWED_LANGS)}"
            )

        spec = get_event_spec(event_type)

        # Hand the merchant a FULLY styled email document — header bar +
        # body + footer chrome, all baked in. Once they fork this into
        # their own template the renderer treats it as raw merchant HTML
        # (no chrome forced on top), so anything not in this seed has to
        # be re-added by the merchant. Giving them the complete document
        # means they don't lose styling the moment they save.
        title = spec.label_ar if language == "ar" else spec.label_en
        full_html = _wrap_with_chrome(
            _email_header(title=title, language=language) + spec.default_html[language],
            language=language,
        )

        return DefaultTemplateDTO(
            event_type=event_type,
            language=language,
            subject=spec.default_subject[language],
            html_body=full_html,
        )
