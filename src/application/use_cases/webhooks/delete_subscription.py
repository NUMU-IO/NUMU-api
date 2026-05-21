"""Delete webhook subscription use case."""

from uuid import UUID

from src.config.logging_config import get_logger
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookSubscriptionRepository,
)

logger = get_logger(__name__)


class DeleteWebhookSubscriptionUseCase:
    """Delete a webhook subscription."""

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.store_repo = store_repo

    async def execute(
        self, subscription_id: UUID, store_id: UUID, user_id: UUID
    ) -> None:
        store = await self.store_repo.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to manage webhooks for this store"
            )

        subscription = await self.subscription_repo.get_by_store_and_id(
            store_id, subscription_id
        )
        if not subscription:
            raise EntityNotFoundError("WebhookSubscription", str(subscription_id))

        await self.subscription_repo.delete(subscription_id)
        logger.info(
            "webhook_subscription_deleted",
            store_id=str(store_id),
            subscription_id=str(subscription_id),
        )
