"""Get email template use case.

Returns the merchant's custom template for ``(store_id, template_id)``.
Mismatched store ownership is reported as ``EntityNotFoundError`` so we
don't leak which IDs exist for which merchants.
"""

from __future__ import annotations

from uuid import UUID

from src.application.dto.email_template import EmailTemplateDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class GetEmailTemplateUseCase:
    """Use case for retrieving a single email template by id."""

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
        user_id: UUID,
    ) -> EmailTemplateDTO:
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to access this store's templates"
            )

        template = await self.email_template_repository.get_by_id(template_id)
        if not template or template.store_id != store_id:
            raise EntityNotFoundError("EmailTemplate", str(template_id))

        return EmailTemplateDTO.from_entity(template)
