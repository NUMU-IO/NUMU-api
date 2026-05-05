"""List email templates use case.

Returns the (paginated) set of merchant-customized templates for a
store, plus the unfiltered total via the repository's ``count_by_store``
helper. Filters on ``event_type`` / ``language`` / ``is_enabled`` map
1:1 to the repository signature.
"""

from __future__ import annotations

from uuid import UUID

from src.application.dto.email_template import EmailTemplateDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.email_template_repository import (
    IEmailTemplateRepository,
)
from src.core.interfaces.repositories.store_repository import IStoreRepository


class ListEmailTemplatesUseCase:
    """Use case for listing a store's email templates."""

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
        user_id: UUID,
        event_type: str | None = None,
        language: str | None = None,
        is_enabled: bool | None = None,
        skip: int = 0,
        limit: int = 100,
    ) -> tuple[list[EmailTemplateDTO], int]:
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to list this store's templates"
            )

        items = await self.email_template_repository.list_by_store(
            store_id=store_id,
            event_type=event_type,
            language=language,
            is_enabled=is_enabled,
            skip=skip,
            limit=limit,
        )
        total = await self.email_template_repository.count_by_store(
            store_id=store_id,
            event_type=event_type,
            language=language,
            is_enabled=is_enabled,
        )
        return [EmailTemplateDTO.from_entity(t) for t in items], total
