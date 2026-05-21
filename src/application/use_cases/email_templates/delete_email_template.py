"""Delete email template use case.

Removes a merchant's custom override for a given (event_type,
language). After deletion, sends for that triple fall back to the
registry default — exactly as if the row had never existed.
"""

from __future__ import annotations

from uuid import UUID

from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DeleteEmailTemplateUseCase:
    """Use case for deleting an email template."""

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
    ) -> bool:
        template = await self.email_template_repository.get_by_id(template_id)
        if not template or template.store_id != store_id:
            raise EntityNotFoundError("EmailTemplate", str(template_id))

        store = await self.store_repository.get_by_id(template.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to delete this email template"
            )

        return await self.email_template_repository.delete(template_id)
