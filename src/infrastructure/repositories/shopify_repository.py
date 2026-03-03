"""Shopify-related repositories — installations, risk, payments, automation, settings."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.automation_log import AutomationLogModel
from src.infrastructure.database.models.tenant.automation_rule import (
    AutomationRuleModel,
)
from src.infrastructure.database.models.tenant.payment_transaction import (
    PaymentTransactionModel,
)
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)
from src.infrastructure.database.models.tenant.shopify_app_settings import (
    ShopifyAppSettingsModel,
)
from src.infrastructure.database.models.tenant.shopify_installation import (
    ShopifyInstallationModel,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# ShopifyInstallationRepository
# ---------------------------------------------------------------------------


class ShopifyInstallationRepository:
    """CRUD for shopify_installations table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_domain(self, domain: str) -> ShopifyInstallationModel | None:
        result = await self.session.execute(
            select(ShopifyInstallationModel).where(
                ShopifyInstallationModel.shopify_domain == domain,
                ShopifyInstallationModel.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_by_store_id(self, store_id: UUID) -> ShopifyInstallationModel | None:
        result = await self.session.execute(
            select(ShopifyInstallationModel).where(
                ShopifyInstallationModel.store_id == store_id,
                ShopifyInstallationModel.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def upsert(
        self,
        *,
        store_id: UUID,
        tenant_id: UUID | None = None,
        shopify_domain: str,
        access_token_encrypted: str,
        scopes: list[str] | None = None,
    ) -> ShopifyInstallationModel:
        existing = await self.get_by_domain(shopify_domain)
        if existing:
            existing.access_token_encrypted = access_token_encrypted
            existing.scopes = scopes
            existing.is_active = True
            existing.uninstalled_at = None
            self.session.add(existing)
            return existing

        model = ShopifyInstallationModel(
            store_id=store_id,
            tenant_id=tenant_id,
            shopify_domain=shopify_domain,
            access_token_encrypted=access_token_encrypted,
            scopes=scopes,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def mark_uninstalled(self, domain: str) -> None:
        await self.session.execute(
            update(ShopifyInstallationModel)
            .where(ShopifyInstallationModel.shopify_domain == domain)
            .values(is_active=False, uninstalled_at=func.now())
        )

    async def delete_store_data(self, store_id: UUID) -> dict[str, int]:
        """GDPR shop/redact — delete ALL data for a store."""
        counts: dict[str, int] = {}
        for label, model_cls in [
            ("risk_assessments", RiskAssessmentModel),
            ("payment_transactions", PaymentTransactionModel),
            ("automation_logs", AutomationLogModel),
            ("automation_rules", AutomationRuleModel),
            ("app_settings", ShopifyAppSettingsModel),
        ]:
            r = await self.session.execute(
                delete(model_cls).where(model_cls.store_id == store_id)  # type: ignore[attr-defined]
            )
            counts[label] = r.rowcount or 0
        # Finally remove the installation itself
        r = await self.session.execute(
            delete(ShopifyInstallationModel).where(
                ShopifyInstallationModel.store_id == store_id
            )
        )
        counts["installations"] = r.rowcount or 0
        return counts


# ---------------------------------------------------------------------------
# RiskAssessmentRepository
# ---------------------------------------------------------------------------


class RiskAssessmentRepository:
    """CRUD for risk_assessments table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> RiskAssessmentModel:  # type: ignore[override]
        model = RiskAssessmentModel(**kwargs)
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_by_id(self, assessment_id: UUID) -> RiskAssessmentModel | None:
        result = await self.session.execute(
            select(RiskAssessmentModel).where(RiskAssessmentModel.id == assessment_id)
        )
        return result.scalar_one_or_none()

    async def list_by_store(
        self, store_id: UUID, *, limit: int = 50, offset: int = 0
    ) -> list[RiskAssessmentModel]:
        result = await self.session.execute(
            select(RiskAssessmentModel)
            .where(RiskAssessmentModel.store_id == store_id)
            .order_by(RiskAssessmentModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def update_action(
        self, assessment_id: UUID, action: str
    ) -> RiskAssessmentModel | None:
        model = await self.get_by_id(assessment_id)
        if model:
            model.action_taken = action
            model.action_taken_at = func.now()
            model.action_taken_by = "shopify_app"
            self.session.add(model)
            await self.session.flush()
        return model

    async def count_high_risk(self, store_id: UUID, *, days: int = 30) -> int:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.count())
            .select_from(RiskAssessmentModel)
            .where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.risk_score >= 60,
                RiskAssessmentModel.created_at >= since,
            )
        )
        return result.scalar_one() or 0

    async def delete_by_customer_email(self, store_id: UUID, email: str) -> int:
        """GDPR customers/redact — remove all risk assessments for a customer."""
        result = await self.session.execute(
            delete(RiskAssessmentModel).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.customer_email == email,
            )
        )
        return result.rowcount or 0

    async def list_by_customer_email(
        self, store_id: UUID, email: str
    ) -> list[RiskAssessmentModel]:
        """GDPR customers/data_request — return all risk assessments for a customer."""
        result = await self.session.execute(
            select(RiskAssessmentModel).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.customer_email == email,
            )
        )
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# PaymentTransactionRepository
# ---------------------------------------------------------------------------


class PaymentTransactionRepository:
    """CRUD for payment_transactions table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> PaymentTransactionModel:  # type: ignore[override]
        model = PaymentTransactionModel(**kwargs)
        self.session.add(model)
        await self.session.flush()
        return model

    async def aggregate_channels(self, store_id: UUID, *, days: int = 30) -> list[dict]:
        """Aggregate payment transactions by channel for analytics."""
        since = datetime.utcnow() - timedelta(days=days)

        success_case = case(
            (PaymentTransactionModel.status == "completed", 1),
            else_=0,
        )

        result = await self.session.execute(
            select(
                PaymentTransactionModel.channel,
                PaymentTransactionModel.gateway,
                PaymentTransactionModel.display_name,
                func.count().label("total_attempts"),
                func.sum(success_case).label("successful_raw"),
                func.sum(
                    case(
                        (
                            PaymentTransactionModel.status == "completed",
                            PaymentTransactionModel.amount_cents,
                        ),
                        else_=0,
                    )
                ).label("revenue_cents"),
            )
            .where(
                PaymentTransactionModel.store_id == store_id,
                PaymentTransactionModel.created_at >= since,
            )
            .group_by(
                PaymentTransactionModel.channel,
                PaymentTransactionModel.gateway,
                PaymentTransactionModel.display_name,
            )
        )
        return [dict(row._mapping) for row in result.all()]

    async def aggregate_failures(self, store_id: UUID, *, days: int = 30) -> list[dict]:
        since = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(
                PaymentTransactionModel.failure_reason,
                PaymentTransactionModel.failure_code,
                func.count().label("count"),
            )
            .where(
                PaymentTransactionModel.store_id == store_id,
                PaymentTransactionModel.status == "failed",
                PaymentTransactionModel.failure_reason.isnot(None),
                PaymentTransactionModel.created_at >= since,
            )
            .group_by(
                PaymentTransactionModel.failure_reason,
                PaymentTransactionModel.failure_code,
            )
            .order_by(func.count().desc())
            .limit(10)
        )
        return [dict(row._mapping) for row in result.all()]


# ---------------------------------------------------------------------------
# AutomationRepository
# ---------------------------------------------------------------------------


class AutomationRepository:
    """CRUD for automation_rules + automation_logs tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # --- rules ---

    async def list_rules(self, store_id: UUID) -> list[AutomationRuleModel]:
        result = await self.session.execute(
            select(AutomationRuleModel)
            .where(AutomationRuleModel.store_id == store_id)
            .order_by(
                AutomationRuleModel.priority.desc(), AutomationRuleModel.created_at
            )
        )
        return list(result.scalars().all())

    async def get_rule(self, rule_id: UUID) -> AutomationRuleModel | None:
        result = await self.session.execute(
            select(AutomationRuleModel).where(AutomationRuleModel.id == rule_id)
        )
        return result.scalar_one_or_none()

    async def create_rule(
        self,
        *,
        store_id: UUID,
        name: str,
        description: str = "",
        trigger_event: str,
        conditions: dict | None = None,
        actions: list | None = None,
        priority: int = 0,
    ) -> AutomationRuleModel:
        model = AutomationRuleModel(
            store_id=store_id,
            name=name,
            description=description,
            trigger_event=trigger_event,
            conditions=conditions or {},
            actions=actions or [],
            priority=priority,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def update_rule(
        self, rule_id: UUID, updates: dict
    ) -> AutomationRuleModel | None:
        model = await self.get_rule(rule_id)
        if not model:
            return None
        for k, v in updates.items():
            if v is not None:
                setattr(model, k, v)
        self.session.add(model)
        await self.session.flush()
        return model

    async def delete_rule(self, rule_id: UUID) -> bool:
        result = await self.session.execute(
            delete(AutomationRuleModel).where(AutomationRuleModel.id == rule_id)
        )
        return (result.rowcount or 0) > 0

    # --- logs ---

    async def list_logs(
        self, store_id: UUID, *, limit: int = 20, offset: int = 0
    ) -> list[AutomationLogModel]:
        result = await self.session.execute(
            select(AutomationLogModel)
            .where(AutomationLogModel.store_id == store_id)
            .order_by(AutomationLogModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def create_log(self, **kwargs) -> AutomationLogModel:  # type: ignore[override]
        model = AutomationLogModel(**kwargs)
        self.session.add(model)
        await self.session.flush()
        return model


# ---------------------------------------------------------------------------
# ShopifyAppSettingsRepository
# ---------------------------------------------------------------------------


class ShopifyAppSettingsRepository:
    """CRUD for shopify_app_settings table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self, store_id: UUID, tenant_id: UUID | None = None
    ) -> ShopifyAppSettingsModel:
        result = await self.session.execute(
            select(ShopifyAppSettingsModel).where(
                ShopifyAppSettingsModel.store_id == store_id
            )
        )
        model = result.scalar_one_or_none()
        if model:
            return model
        model = ShopifyAppSettingsModel(store_id=store_id, tenant_id=tenant_id)
        self.session.add(model)
        await self.session.flush()
        return model

    async def update(
        self, store_id: UUID, updates: dict
    ) -> ShopifyAppSettingsModel | None:
        result = await self.session.execute(
            select(ShopifyAppSettingsModel).where(
                ShopifyAppSettingsModel.store_id == store_id
            )
        )
        model = result.scalar_one_or_none()
        if not model:
            # Auto-create if not found
            model = ShopifyAppSettingsModel(store_id=store_id)
            self.session.add(model)
        for k, v in updates.items():
            if v is not None:
                setattr(model, k, v)
        await self.session.flush()
        return model
