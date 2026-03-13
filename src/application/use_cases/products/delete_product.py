"""Delete product use case."""

from uuid import UUID

from src.core.events.base import EventBus
from src.core.events.product_events import ProductDeletedEvent
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.product_repository import IProductRepository
from src.core.interfaces.repositories.store_repository import IStoreRepository


class DeleteProductUseCase:
    """Use case for deleting a product."""

    def __init__(
        self,
        product_repository: IProductRepository,
        store_repository: IStoreRepository,
        event_bus: EventBus | None = None,
    ) -> None:
        self.product_repository = product_repository
        self.store_repository = store_repository
        self.event_bus = event_bus

    async def execute(self, product_id: UUID, user_id: UUID) -> bool:
        """Delete a product."""
        # Get product
        product = await self.product_repository.get_by_id(product_id)
        if not product:
            raise EntityNotFoundError("Product", str(product_id))

        # Get store and verify ownership
        store = await self.store_repository.get_by_id(product.store_id)
        if not store or store.owner_id != user_id:
            raise AuthorizationError("You don't have permission to delete this product")

        store_id = product.store_id

        # Delete product
        result = await self.product_repository.delete(product_id)

        if self.event_bus:
            try:
                self.event_bus.publish(
                    ProductDeletedEvent(product_id=product_id, store_id=store_id)
                )
            except Exception:
                pass

        return result
