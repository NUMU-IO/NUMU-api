"""Webhook subscription routes.

URL: /stores/{store_id}/webhooks
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from src.api.dependencies.auth import get_current_user_id
from src.api.dependencies.repositories import (
    get_store_repository,
    get_webhook_delivery_log_repository,
    get_webhook_subscription_repository,
)
from src.api.responses.base import DeleteResponse, ListResponse, SuccessResponse
from src.api.v1.schemas.tenant.webhooks import (
    CreateWebhookSubscriptionRequest,
    WebhookDeliveryLogResponse,
    WebhookSubscriptionCreatedResponse,
    WebhookSubscriptionResponse,
)
from src.application.use_cases.webhooks import (
    CreateWebhookSubscriptionUseCase,
    DeleteWebhookSubscriptionUseCase,
    ListWebhookDeliveryLogsUseCase,
    ListWebhookSubscriptionsUseCase,
)
from src.core.entities.webhook import WebhookDeliveryLog, WebhookSubscription
from src.infrastructure.repositories import StoreRepository
from src.infrastructure.repositories.webhook_delivery_log_repository import (
    WebhookDeliveryLogRepository,
)
from src.infrastructure.repositories.webhook_subscription_repository import (
    WebhookSubscriptionRepository,
)

router = APIRouter(prefix="/{store_id}/webhooks")


def _subscription_to_response(sub: WebhookSubscription) -> WebhookSubscriptionResponse:
    return WebhookSubscriptionResponse(
        id=str(sub.id),
        store_id=str(sub.store_id),
        url=sub.url,
        events=[e.value for e in sub.events],
        is_active=sub.is_active,
        description=sub.description,
        created_at=sub.created_at,
        updated_at=sub.updated_at,
    )


def _log_to_response(log: WebhookDeliveryLog) -> WebhookDeliveryLogResponse:
    return WebhookDeliveryLogResponse(
        id=str(log.id),
        subscription_id=str(log.subscription_id) if log.subscription_id else None,
        event_type=log.event_type.value,
        event_id=str(log.event_id),
        status=log.status.value,
        attempt_count=log.attempt_count,
        last_status_code=log.last_status_code,
        last_response_body=log.last_response_body,
        last_error=log.last_error,
        next_attempt_at=log.next_attempt_at,
        last_attempt_at=log.last_attempt_at,
        exhausted_at=log.exhausted_at,
        created_at=log.created_at,
    )


@router.post(
    "",
    response_model=SuccessResponse[WebhookSubscriptionCreatedResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a webhook endpoint",
)
async def create_webhook_subscription(
    store_id: UUID,
    request: CreateWebhookSubscriptionRequest,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    subscription_repo: Annotated[
        WebhookSubscriptionRepository, Depends(get_webhook_subscription_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> SuccessResponse[WebhookSubscriptionCreatedResponse]:
    use_case = CreateWebhookSubscriptionUseCase(subscription_repo, store_repo)
    subscription, plain_secret = await use_case.execute(
        store_id=store_id,
        user_id=user_id,
        url=str(request.url),
        events=request.events,
        description=request.description,
    )
    response_data = WebhookSubscriptionCreatedResponse(
        id=str(subscription.id),
        store_id=str(subscription.store_id),
        url=subscription.url,
        events=[e.value for e in subscription.events],
        is_active=subscription.is_active,
        description=subscription.description,
        secret=plain_secret,
        created_at=subscription.created_at,
    )
    return SuccessResponse(
        data=response_data,
        message="Webhook registered. Save the secret — it will not be shown again.",
    )


@router.get(
    "",
    response_model=ListResponse[WebhookSubscriptionResponse],
    summary="List webhook subscriptions",
)
async def list_webhook_subscriptions(
    store_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    subscription_repo: Annotated[
        WebhookSubscriptionRepository, Depends(get_webhook_subscription_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> ListResponse[WebhookSubscriptionResponse]:
    use_case = ListWebhookSubscriptionsUseCase(subscription_repo, store_repo)
    subscriptions = await use_case.execute(store_id=store_id, user_id=user_id)
    items = [_subscription_to_response(s) for s in subscriptions]
    return ListResponse.create(items)


@router.delete(
    "/{subscription_id}",
    response_model=DeleteResponse,
    status_code=status.HTTP_200_OK,
    summary="Delete a webhook subscription",
)
async def delete_webhook_subscription(
    store_id: UUID,
    subscription_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    subscription_repo: Annotated[
        WebhookSubscriptionRepository, Depends(get_webhook_subscription_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
) -> DeleteResponse:
    use_case = DeleteWebhookSubscriptionUseCase(subscription_repo, store_repo)
    await use_case.execute(
        subscription_id=subscription_id,
        store_id=store_id,
        user_id=user_id,
    )
    return DeleteResponse(
        message="Webhook subscription deleted",
        deleted_id=str(subscription_id),
    )


@router.get(
    "/{subscription_id}/logs",
    response_model=ListResponse[WebhookDeliveryLogResponse],
    summary="List delivery logs for a webhook subscription",
)
async def list_delivery_logs(
    store_id: UUID,
    subscription_id: UUID,
    user_id: Annotated[UUID, Depends(get_current_user_id)],
    subscription_repo: Annotated[
        WebhookSubscriptionRepository, Depends(get_webhook_subscription_repository)
    ],
    delivery_log_repo: Annotated[
        WebhookDeliveryLogRepository, Depends(get_webhook_delivery_log_repository)
    ],
    store_repo: Annotated[StoreRepository, Depends(get_store_repository)],
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ListResponse[WebhookDeliveryLogResponse]:
    use_case = ListWebhookDeliveryLogsUseCase(
        subscription_repo, delivery_log_repo, store_repo
    )
    logs = await use_case.execute(
        subscription_id=subscription_id,
        store_id=store_id,
        user_id=user_id,
        skip=skip,
        limit=limit,
    )
    items = [_log_to_response(log) for log in logs]
    return ListResponse.create(items)
