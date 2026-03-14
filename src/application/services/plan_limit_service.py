"""Plan limit enforcement service.

Checks whether a store/tenant has exceeded its plan's resource limits
before allowing write operations.
"""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.entities.plan import PlanFeatures, get_plan_features
from src.core.exceptions import PlanLimitExceededError
from src.infrastructure.tenancy.repository import TenantRepository

# Map plan names to the next tier (for upgrade hints in error messages)
_UPGRADE_MAP: dict[str, str] = {
    "demo": "free",
    "free": "starter",
    "starter": "pro",
    "pro": "enterprise",
}


class PlanLimitService:
    """Enforces plan-based resource and feature limits.

    Always loads the plan from the tenant record so limits reflect
    any recent plan changes without restarting the process.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _get_plan(self, tenant_id: UUID) -> tuple[str, PlanFeatures]:
        """Return (plan_name, PlanFeatures) for the given tenant."""
        tenant_repo = TenantRepository(self.session)
        tenant = await tenant_repo.get_by_id(tenant_id)
        plan_name = tenant.plan if tenant else "free"
        return plan_name, get_plan_features(plan_name)

    # ------------------------------------------------------------------
    # Resource count checks
    # ------------------------------------------------------------------

    async def check_product_limit(self, store_id: UUID, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if adding a product would exceed the limit."""
        from src.infrastructure.database.models.tenant.product import ProductModel

        plan_name, features = await self._get_plan(tenant_id)
        if features.max_products == -1:
            return

        result = await self.session.execute(
            select(func.count())
            .select_from(ProductModel)
            .where(ProductModel.store_id == store_id)
        )
        current = result.scalar_one()

        if current >= features.max_products:
            raise PlanLimitExceededError(
                resource="products",
                limit=features.max_products,
                current=current,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    async def check_order_limit(self, store_id: UUID, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if creating an order would exceed the monthly limit."""
        from src.infrastructure.database.models.tenant.order import OrderModel

        plan_name, features = await self._get_plan(tenant_id)
        if features.max_orders_per_month == -1:
            return

        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

        result = await self.session.execute(
            select(func.count())
            .select_from(OrderModel)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= month_start,
            )
        )
        current = result.scalar_one()

        if current >= features.max_orders_per_month:
            raise PlanLimitExceededError(
                resource="orders this month",
                limit=features.max_orders_per_month,
                current=current,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    async def check_store_limit(self, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if creating a store would exceed the limit."""
        from src.infrastructure.database.models.tenant.store import StoreModel

        plan_name, features = await self._get_plan(tenant_id)
        if features.max_stores == -1:
            return

        result = await self.session.execute(
            select(func.count())
            .select_from(StoreModel)
            .where(StoreModel.tenant_id == tenant_id)
        )
        current = result.scalar_one()

        if current >= features.max_stores:
            raise PlanLimitExceededError(
                resource="stores",
                limit=features.max_stores,
                current=current,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    # ------------------------------------------------------------------
    # Feature flag checks
    # ------------------------------------------------------------------

    async def require_webhooks(self, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if webhooks are not available on this plan."""
        plan_name, features = await self._get_plan(tenant_id)
        if not features.webhooks_enabled:
            raise PlanLimitExceededError(
                resource="webhooks",
                limit=0,
                current=0,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    async def require_custom_domain(self, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if custom domains are not available on this plan."""
        plan_name, features = await self._get_plan(tenant_id)
        if not features.custom_domain_enabled:
            raise PlanLimitExceededError(
                resource="custom domains",
                limit=0,
                current=0,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    async def require_discount_codes(self, tenant_id: UUID) -> None:
        """Raise PlanLimitExceededError if discount codes are not available on this plan."""
        plan_name, features = await self._get_plan(tenant_id)
        if not features.discount_codes_enabled:
            raise PlanLimitExceededError(
                resource="discount codes",
                limit=0,
                current=0,
                plan=features.display_name,
                upgrade_to=_UPGRADE_MAP.get(plan_name),
            )

    async def get_usage_summary(self, store_id: UUID, tenant_id: UUID) -> dict:
        """Return current usage vs plan limits for a store."""
        from src.infrastructure.database.models.tenant.order import OrderModel
        from src.infrastructure.database.models.tenant.product import ProductModel

        plan_name, features = await self._get_plan(tenant_id)

        product_count_result = await self.session.execute(
            select(func.count())
            .select_from(ProductModel)
            .where(ProductModel.store_id == store_id)
        )
        product_count = product_count_result.scalar_one()

        now = datetime.now(UTC)
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        order_count_result = await self.session.execute(
            select(func.count())
            .select_from(OrderModel)
            .where(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= month_start,
            )
        )
        order_count = order_count_result.scalar_one()

        return {
            "plan": plan_name,
            "display_name": features.display_name,
            "products": {
                "used": product_count,
                "limit": features.max_products,
                "unlimited": features.max_products == -1,
            },
            "orders_this_month": {
                "used": order_count,
                "limit": features.max_orders_per_month,
                "unlimited": features.max_orders_per_month == -1,
            },
            "features": {
                "webhooks": features.webhooks_enabled,
                "custom_domain": features.custom_domain_enabled,
                "api_access": features.api_access_enabled,
                "analytics": features.analytics_enabled,
                "discount_codes": features.discount_codes_enabled,
            },
        }
