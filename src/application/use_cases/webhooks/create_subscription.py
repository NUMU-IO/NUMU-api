"""Create webhook subscription use case."""

import secrets
from uuid import UUID

from src.config.logging_config import get_logger
from src.core.entities.webhook import WebhookEventType, WebhookSubscription
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookSubscriptionRepository,
)

logger = get_logger(__name__)

VALID_EVENT_TYPES = {e.value for e in WebhookEventType}


class CreateWebhookSubscriptionUseCase:
    """Register a new webhook endpoint for a store."""

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.store_repo = store_repo

    async def execute(
        self,
        store_id: UUID,
        user_id: UUID,
        url: str,
        events: list[str],
        description: str | None = None,
    ) -> tuple[WebhookSubscription, str]:
        """Create a webhook subscription.

        Returns (subscription, plain_secret). The secret is shown ONCE — the
        merchant must save it. It is stored in plaintext so we can sign payloads.
        """
        store = await self.store_repo.get_by_id(store_id)
        if not store:
            raise EntityNotFoundError("Store", str(store_id))
        if store.owner_id != user_id:
            raise AuthorizationError(
                "You don't have permission to manage webhooks for this store"
            )

        # Validate event types
        invalid = [e for e in events if e not in VALID_EVENT_TYPES]
        if invalid:
            raise ValidationError(
                f"Invalid event type(s): {invalid}. "
                f"Valid types: {sorted(VALID_EVENT_TYPES)}"
            )
        if not events:
            raise ValidationError("At least one event type is required")

        parsed_events = [WebhookEventType(e) for e in events]
        plain_secret = secrets.token_hex(32)  # 64-char hex — shown once

        subscription = WebhookSubscription(
            store_id=store_id,
            tenant_id=store.tenant_id,
            url=url,
            events=parsed_events,
            secret=plain_secret,
            is_active=True,
            description=description,
        )

        created = await self.subscription_repo.create(subscription)
        logger.info(
            "webhook_subscription_created",
            store_id=str(store_id),
            subscription_id=str(created.id),
            events=events,
        )
        return created, plain_secret
