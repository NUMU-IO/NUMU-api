"""List webhook delivery logs use case."""

from uuid import UUID

from src.core.entities.webhook import WebhookDeliveryLog
from src.core.exceptions import AuthorizationError, EntityNotFoundError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookDeliveryLogRepository,
    IWebhookSubscriptionRepository,
)


class ListWebhookDeliveryLogsUseCase:
    """List delivery logs for a specific webhook subscription."""

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        delivery_log_repo: IWebhookDeliveryLogRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.delivery_log_repo = delivery_log_repo
        self.store_repo = store_repo

    async def execute(
        self,
        subscription_id: UUID,
        store_id: UUID,
        user_id: UUID,
        skip: int = 0,
        limit: int = 50,
    ) -> list[WebhookDeliveryLog]:
        store = await self.store_repo.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to view webhooks for this store"
            )

        subscription = await self.subscription_repo.get_by_store_and_id(
            store_id, subscription_id
        )
        if not subscription:
            raise EntityNotFoundError("WebhookSubscription", str(subscription_id))

        return await self.delivery_log_repo.get_by_subscription(
            subscription_id, skip=skip, limit=limit
        )
