"""List webhook subscriptions use case."""

from uuid import UUID

from src.core.entities.webhook import WebhookSubscription
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookSubscriptionRepository,
)


class ListWebhookSubscriptionsUseCase:
    """List all webhook subscriptions for a store."""

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.store_repo = store_repo

    async def execute(self, store_id: UUID, user_id: UUID) -> list[WebhookSubscription]:
        store = await self.store_repo.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view webhooks for this store"
            )

        return await self.subscription_repo.get_by_store(store_id)
