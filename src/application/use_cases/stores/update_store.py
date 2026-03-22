"""Update store use case."""

from uuid import UUID

from src.application.dto.store import StoreDTO, UpdateStoreDTO
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository


class UpdateStoreUseCase:
    """Use case for updating a store."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(
        self,
        store_id: UUID,
        dto: UpdateStoreDTO,
        user_id: UUID,
    ) -> StoreDTO:
        """Update a store."""
        # Get store
        store = await self.store_repository.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))

        # Check ownership
        if store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to update this store")

        # Update fields
        if dto.name is not None:
            store.name = dto.name
        if dto.description is not None:
            store.description = dto.description
        if dto.logo_url is not None:
            store.logo_url = dto.logo_url
        if dto.banner_url is not None:
            store.banner_url = dto.banner_url
        if dto.contact_email is not None:
            store.contact_email = dto.contact_email
        if dto.contact_phone is not None:
            store.contact_phone = dto.contact_phone
        if dto.address is not None:
            store.address = dto.address
        if dto.social_links is not None:
            store.social_links = dto.social_links
        if dto.default_language is not None:
            store.default_language = dto.default_language
        if dto.status is not None:
            if dto.status == "active":
                store.activate()
            elif dto.status == "inactive":
                store.deactivate()
        if dto.settings is not None:
            store.settings = {**(store.settings or {}), **dto.settings}

        # Save store
        updated_store = await self.store_repository.update(store)

        return StoreDTO.from_entity(updated_store)
