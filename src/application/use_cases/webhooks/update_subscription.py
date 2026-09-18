"""Change a webhook endpoint in place, rotate its secret, or test it.

Until these existed, the only way to correct a URL or replace a leaked secret
was to delete the subscription and create a new one — which changes the secret
the receiver verifies and drops every event in between. An integrator reads
that as "this platform's webhooks are not operable", and they are right.
"""

import secrets
from uuid import UUID

from src.core.entities.webhook import (
    SUBSCRIBABLE_EVENT_TYPES,
    WebhookEventType,
    WebhookSubscription,
)
from src.core.exceptions import AuthorizationError, EntityNotFoundError, ValidationError
from src.core.interfaces.repositories.store_repository import IStoreRepository
from src.core.interfaces.repositories.webhook_repository import (
    IWebhookSubscriptionRepository,
)
from src.core.logging import get_logger
from src.core.url_guard import UnsafeUrlError, assert_webhook_target

logger = get_logger(__name__)

VALID_EVENT_TYPES = {e.value for e in SUBSCRIBABLE_EVENT_TYPES}


async def _load_owned(
    subscription_repo: IWebhookSubscriptionRepository,
    store_repo: IStoreRepository,
    store_id: UUID,
    user_id: UUID,
    subscription_id: UUID,
) -> WebhookSubscription:
    store = await store_repo.get_by_id(store_id)
    if not store:
        raise EntityNotFoundError("Store", str(store_id))
    if store.owner_id != user_id:
        raise AuthorizationError(
            "You don't have permission to manage webhooks for this store"
        )
    subscription = await subscription_repo.get_by_id(subscription_id)
    if subscription is None or subscription.store_id != store_id:
        raise EntityNotFoundError("WebhookSubscription", str(subscription_id))
    return subscription


class UpdateWebhookSubscriptionUseCase:
    """Edit the URL, the event list, the description or the active flag."""

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
        subscription_id: UUID,
        url: str | None = None,
        events: list[str] | None = None,
        is_active: bool | None = None,
        description: str | None = None,
    ) -> WebhookSubscription:
        subscription = await _load_owned(
            self.subscription_repo, self.store_repo, store_id, user_id, subscription_id
        )

        if url is not None:
            try:
                assert_webhook_target(url)
            except UnsafeUrlError as exc:
                raise ValidationError(str(exc)) from exc
            subscription.url = url

        if events is not None:
            invalid = [e for e in events if e not in VALID_EVENT_TYPES]
            if invalid:
                raise ValidationError(
                    f"Invalid event type(s): {invalid}. "
                    f"Valid types: {sorted(VALID_EVENT_TYPES)}"
                )
            subscription.events = [WebhookEventType(e) for e in events]

        if is_active is not None:
            subscription.is_active = is_active
        if description is not None:
            subscription.description = description

        updated = await self.subscription_repo.update(subscription)
        logger.info(
            "webhook_subscription_updated",
            subscription_id=str(subscription_id),
            store_id=str(store_id),
            is_active=updated.is_active,
        )
        return updated


class RotateWebhookSecretUseCase:
    """Issue a new signing secret for an endpoint.

    Deliberately abrupt: the old secret stops signing immediately, so a leaked
    one is dead the moment this returns. Rotate, then deploy the new secret —
    or subscribe a second endpoint first if the receiver cannot take a gap.
    """

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.store_repo = store_repo

    async def execute(
        self, store_id: UUID, user_id: UUID, subscription_id: UUID
    ) -> tuple[WebhookSubscription, str]:
        subscription = await _load_owned(
            self.subscription_repo, self.store_repo, store_id, user_id, subscription_id
        )
        new_secret = secrets.token_hex(32)
        await self.subscription_repo.set_secret(subscription_id, new_secret)
        logger.info(
            "webhook_secret_rotated",
            subscription_id=str(subscription_id),
            store_id=str(store_id),
        )
        subscription.secret = new_secret
        return subscription, new_secret


class SendTestWebhookUseCase:
    """Deliver a ``webhook.ping`` to the endpoint and report what it answered.

    Synchronous on purpose: the merchant is watching, and "did my endpoint
    accept it, and with what status" is the entire question. Failures are
    reported, not retried — this is a test, not an event anyone is owed.
    """

    def __init__(
        self,
        subscription_repo: IWebhookSubscriptionRepository,
        store_repo: IStoreRepository,
    ) -> None:
        self.subscription_repo = subscription_repo
        self.store_repo = store_repo

    async def execute(
        self, store_id: UUID, user_id: UUID, subscription_id: UUID
    ) -> dict:
        from src.application.services.webhook_delivery_service import send_test_delivery

        subscription = await _load_owned(
            self.subscription_repo, self.store_repo, store_id, user_id, subscription_id
        )
        return await send_test_delivery(subscription)
