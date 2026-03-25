"""Upsell rule repository."""

import logging
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.upsell_rule import UpsellRuleModel

logger = logging.getLogger(__name__)


class UpsellRuleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, data: dict) -> UpsellRuleModel:
        model = UpsellRuleModel(**data)
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_by_id(self, rule_id: UUID) -> UpsellRuleModel | None:
        result = await self.session.execute(
            select(UpsellRuleModel).where(UpsellRuleModel.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self, store_id: UUID, active_only: bool = False
    ) -> list[UpsellRuleModel]:
        query = select(UpsellRuleModel).where(UpsellRuleModel.store_id == store_id)
        if active_only:
            query = query.where(UpsellRuleModel.is_active.is_(True))
        query = query.order_by(UpsellRuleModel.priority.desc())
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def get_matching_rules(
        self,
        store_id: UUID,
        product_ids: list[UUID],
        category_ids: list[UUID],
        cart_value: int,
    ) -> list[UpsellRuleModel]:
        """Find upsell rules that match the given order context."""
        query = (
            select(UpsellRuleModel)
            .where(
                and_(
                    UpsellRuleModel.store_id == store_id,
                    UpsellRuleModel.is_active.is_(True),
                )
            )
            .order_by(UpsellRuleModel.priority.desc())
        )

        result = await self.session.execute(query)
        rules = list(result.scalars().all())

        # Filter in Python for JSONB array matching
        matching = []
        for rule in rules:
            # Check max uses
            if rule.max_uses is not None and rule.uses_count >= rule.max_uses:
                continue

            # Check trigger
            if rule.trigger_type == "any":
                matching.append(rule)
            elif rule.trigger_type == "product":
                trigger_ids = [
                    UUID(pid) if isinstance(pid, str) else pid
                    for pid in (rule.trigger_product_ids or [])
                ]
                if any(pid in trigger_ids for pid in product_ids):
                    matching.append(rule)
            elif rule.trigger_type == "category":
                trigger_cats = [
                    UUID(cid) if isinstance(cid, str) else cid
                    for cid in (rule.trigger_category_ids or [])
                ]
                if any(cid in trigger_cats for cid in category_ids):
                    matching.append(rule)
            elif rule.trigger_type == "cart_value":
                if cart_value >= rule.trigger_min_cart_value:
                    matching.append(rule)

        return matching

    async def update(self, rule: UpsellRuleModel) -> UpsellRuleModel:
        await self.session.flush()
        return rule

    async def delete(self, rule_id: UUID, store_id: UUID) -> bool:
        rule = await self.get_by_id(rule_id)
        if not rule or rule.store_id != store_id:
            return False
        await self.session.delete(rule)
        await self.session.flush()
        return True

    async def increment_uses(self, rule_id: UUID) -> None:
        rule = await self.get_by_id(rule_id)
        if rule:
            rule.uses_count = (rule.uses_count or 0) + 1
            await self.session.flush()
