"""Create store use case."""

import uuid
from uuid import UUID

from slugify import slugify

from src.application.dto.store import CreateStoreDTO, StoreDTO
from src.core.entities.store import Store, StoreStatus
from src.core.exceptions import EntityAlreadyExistsError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.value_objects.money import Currency


class CreateStoreUseCase:
    """Use case for creating a new store."""

    def __init__(self, store_repository: IStoreRepository) -> None:
        self.store_repository = store_repository

    async def execute(self, dto: CreateStoreDTO, owner_id: UUID) -> StoreDTO:
        """Create a new store."""
        # Generate slug if not provided
        slug = dto.slug or slugify(dto.name)

        # Check if slug already exists
        if await self.store_repository.slug_exists(slug):
            # Append a random suffix to make it unique
            slug = f"{slug}-{str(uuid.uuid4())[:8]}"

        # Parse currency
        try:
            currency = Currency(dto.default_currency)
        except ValueError:
            currency = Currency.USD

        # Create store entity
        store = Store(
            name=dto.name,
            slug=slug,
            owner_id=owner_id,
            description=dto.description,
            status=StoreStatus.PENDING_APPROVAL,
            default_currency=currency,
            contact_email=dto.contact_email,
            contact_phone=dto.contact_phone,
        )

        # Save store
        created_store = await self.store_repository.create(store)

        return StoreDTO.from_entity(created_store)
