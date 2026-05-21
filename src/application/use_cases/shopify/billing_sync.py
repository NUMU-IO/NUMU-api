"""Billing-sync use case (backend-001).

Persists a Shopify Billing API subscription record posted by the
Shopify-app's syncSubscriptionToNumu helper. Idempotent upsert keyed
by (store_id, shopify_subscription_id) — same Shopify subscription
always maps to the same row, regardless of how many times the
Shopify-app retries.

See specs/backend-001-billing-sync/.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from src.core.entities.shopify import ShopifySubscription
from src.infrastructure.database.models.tenant.shopify_subscription import (
    ShopifySubscriptionModel,
)
from src.infrastructure.repositories.shopify_repository import (
    ShopifySubscriptionRepository,
)


def _to_entity(model: ShopifySubscriptionModel) -> ShopifySubscription:
    """SQLAlchemy model → domain entity."""
    return ShopifySubscription(
        id=model.id,
        store_id=model.store_id,
        tenant_id=model.tenant_id,
        shopify_subscription_id=model.shopify_subscription_id,
        status=model.status,
        plan_id=model.plan_id,
        is_trial=model.is_trial,
        trial_ends_at=model.trial_ends_at,
        current_period_end=model.current_period_end,
        cancelled_at=model.cancelled_at,
        synced_at=model.synced_at,
        created_at=model.created_at,
        updated_at=model.updated_at,
    )


class BillingSyncUseCase:
    """Upsert a Shopify subscription record."""

    def __init__(self, repo: ShopifySubscriptionRepository) -> None:
        self.repo = repo

    async def execute(
        self,
        *,
        store_id: UUID,
        shopify_subscription_id: str,
        status: str,
        plan_id: str,
        is_trial: bool,
        trial_ends_at: datetime | None,
        current_period_end: datetime | None,
        tenant_id: UUID | None = None,
    ) -> ShopifySubscription:
        model = await self.repo.upsert(
            store_id=store_id,
            shopify_subscription_id=shopify_subscription_id,
            status=status,
            plan_id=plan_id,
            is_trial=is_trial,
            trial_ends_at=trial_ends_at,
            current_period_end=current_period_end,
            tenant_id=tenant_id,
        )
        return _to_entity(model)


class GetActiveSubscriptionUseCase:
    """Return the most recent non-terminal subscription for a store."""

    def __init__(self, repo: ShopifySubscriptionRepository) -> None:
        self.repo = repo

    async def execute(self, *, store_id: UUID) -> ShopifySubscription | None:
        model = await self.repo.get_active(store_id)
        if model is None:
            return None
        return _to_entity(model)
