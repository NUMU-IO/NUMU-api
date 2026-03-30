"""Shopify-related repositories — installations, risk, payments, automation, settings, network reputation."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.automation_log import AutomationLogModel
from src.infrastructure.database.models.tenant.automation_rule import (
    AutomationRuleModel,
)
from src.infrastructure.database.models.tenant.network_contribution_log import (
    NetworkContributionLogModel,
)
from src.infrastructure.database.models.tenant.network_reputation import (
    NetworkReputationModel,
)
from src.infrastructure.database.models.tenant.payment_link_session import (
    PaymentLinkSessionModel,
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
        """GDPR shop/redact — delete ALL identifiable data for a store.

        Network reputation aggregates are kept but their counts are
        decremented using the network_contribution_log, per the
        constitution's data retention rule.

        Uses a PostgreSQL advisory lock keyed on the store_id to prevent
        race conditions if a new order arrives during the deletion window.
        """
        counts: dict[str, int] = {}

        # Acquire advisory lock scoped to this transaction to serialize
        # concurrent GDPR deletions and order writes for the same store.
        lock_key = abs(hash(str(store_id))) % (2**31)
        await self.session.execute(select(func.pg_advisory_xact_lock(lock_key)))

        # 1) Decrement network_reputation aggregates using contribution log
        contrib_rows = await self.session.execute(
            select(
                NetworkContributionLogModel.phone_hash,
                NetworkContributionLogModel.event_type,
                func.count().label("cnt"),
            )
            .where(NetworkContributionLogModel.store_id == store_id)
            .group_by(
                NetworkContributionLogModel.phone_hash,
                NetworkContributionLogModel.event_type,
            )
        )
        decremented = 0
        for row in contrib_rows.all():
            phone_hash = row.phone_hash
            event_type = row.event_type
            cnt = row.cnt

            col_map = {
                "order": "total_network_orders",
                "rto": "total_network_rtos",
                "delivery": "total_successful_deliveries",
                "refund": "total_refunds",
            }
            col_name = col_map.get(event_type)
            if not col_name:
                continue

            col = getattr(NetworkReputationModel, col_name)
            await self.session.execute(
                update(NetworkReputationModel)
                .where(NetworkReputationModel.phone_hash == phone_hash)
                .values(**{col_name: func.greatest(col - cnt, 0)})
            )
            decremented += cnt

        # Decrement contributing_store_count for each distinct phone_hash
        distinct_hashes = await self.session.execute(
            select(NetworkContributionLogModel.phone_hash)
            .where(NetworkContributionLogModel.store_id == store_id)
            .distinct()
        )
        for (phone_hash,) in distinct_hashes.all():
            await self.session.execute(
                update(NetworkReputationModel)
                .where(NetworkReputationModel.phone_hash == phone_hash)
                .values(
                    contributing_store_count=func.greatest(
                        NetworkReputationModel.contributing_store_count - 1, 0
                    )
                )
            )

        # Anonymize network_reputation records that have reached zero across
        # all counters instead of deleting them — preserves historical
        # analytics data while removing any merchant-attributable signal.
        await self.session.execute(
            update(NetworkReputationModel)
            .where(
                NetworkReputationModel.total_network_orders <= 0,
                NetworkReputationModel.total_network_rtos <= 0,
                NetworkReputationModel.total_successful_deliveries <= 0,
                NetworkReputationModel.total_refunds <= 0,
                NetworkReputationModel.anonymized_at.is_(None),
            )
            .values(anonymized_at=func.now())
        )
        counts["network_contributions_decremented"] = decremented

        # 2) Delete the contribution log entries for this store
        r = await self.session.execute(
            delete(NetworkContributionLogModel).where(
                NetworkContributionLogModel.store_id == store_id
            )
        )
        counts["contribution_logs"] = r.rowcount or 0

        # 3) Delete all store-scoped data
        for label, model_cls in [
            ("payment_link_sessions", PaymentLinkSessionModel),
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

        # 4) Finally remove the installation itself
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

    async def aggregate_dashboard(self, store_id: UUID, *, days: int = 30) -> dict:
        """Aggregate dashboard metrics via SQL — no in-memory loading."""
        since = datetime.utcnow() - timedelta(days=days)

        # Total scored orders
        total_result = await self.session.execute(
            select(func.count()).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.created_at >= since,
            )
        )
        total_orders = total_result.scalar_one() or 0

        # High risk count
        high_risk_result = await self.session.execute(
            select(func.count()).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.risk_score >= 60,
                RiskAssessmentModel.created_at >= since,
            )
        )
        high_risk = high_risk_result.scalar_one() or 0

        # Revenue protected (held or cancelled high-risk orders)
        protected_result = await self.session.execute(
            select(func.coalesce(func.sum(RiskAssessmentModel.total_cents), 0)).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.action_taken.in_([
                    "held_for_review",
                    "auto_cancelled",
                    "cancelled",
                    "cancel",
                ]),
                RiskAssessmentModel.risk_score >= 60,
                RiskAssessmentModel.created_at >= since,
            )
        )
        revenue_protected = protected_result.scalar_one() or 0

        # Payment recovery (auto-approved medium-risk)
        recovery_result = await self.session.execute(
            select(func.coalesce(func.sum(RiskAssessmentModel.total_cents), 0)).where(
                RiskAssessmentModel.store_id == store_id,
                RiskAssessmentModel.action_taken == "auto_approved",
                RiskAssessmentModel.risk_score >= 30,
                RiskAssessmentModel.created_at >= since,
            )
        )
        payment_recovery = recovery_result.scalar_one() or 0

        return {
            "total_orders": total_orders,
            "high_risk_orders_count": high_risk,
            "revenue_protected_cents": revenue_protected,
            "payment_recovery_cents": payment_recovery,
        }

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

    async def increment_triggered(self, rule_id: UUID | str) -> None:
        """Bump ``times_triggered`` and set ``last_triggered_at``."""
        await self.session.execute(
            update(AutomationRuleModel)
            .where(AutomationRuleModel.id == UUID(str(rule_id)))
            .values(
                times_triggered=AutomationRuleModel.times_triggered + 1,
                last_triggered_at=func.now(),
            )
        )

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


# ---------------------------------------------------------------------------
# NetworkReputationRepository
# ---------------------------------------------------------------------------


class NetworkReputationRepository:
    """Read/write for network_reputation + network_contribution_log tables."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_phone_hash(self, phone_hash: str) -> NetworkReputationModel | None:
        result = await self.session.execute(
            select(NetworkReputationModel).where(
                NetworkReputationModel.phone_hash == phone_hash
            )
        )
        return result.scalar_one_or_none()

    async def upsert_order(
        self, *, phone_hash: str, store_id: UUID
    ) -> NetworkReputationModel:
        """Record a new order event for a phone hash."""
        stmt = (
            pg_insert(NetworkReputationModel)
            .values(
                phone_hash=phone_hash,
                total_network_orders=1,
                contributing_store_count=1,
                last_order_at=func.now(),
            )
            .on_conflict_do_update(
                index_elements=["phone_hash"],
                set_={
                    "total_network_orders": (
                        NetworkReputationModel.total_network_orders + 1
                    ),
                    "last_order_at": func.now(),
                },
            )
            .returning(NetworkReputationModel)
        )
        result = await self.session.execute(stmt)
        model = result.scalar_one()

        # Append contribution log (append-only ledger)
        self.session.add(
            NetworkContributionLogModel(
                store_id=store_id,
                phone_hash=phone_hash,
                event_type="order",
            )
        )
        await self.session.flush()
        return model

    async def record_event(
        self,
        *,
        phone_hash: str,
        store_id: UUID,
        event_type: str,
    ) -> None:
        """Record an rto, delivery, or refund event."""
        col_map = {
            "rto": "total_network_rtos",
            "delivery": "total_successful_deliveries",
            "refund": "total_refunds",
        }
        col_name = col_map.get(event_type)
        if not col_name:
            logger.warning("Unknown network event type: %s", event_type)
            return

        timestamp_map = {
            "rto": {"last_rto_at": func.now()},
        }
        extra_values = timestamp_map.get(event_type, {})

        await self.session.execute(
            update(NetworkReputationModel)
            .where(NetworkReputationModel.phone_hash == phone_hash)
            .values(
                **{col_name: getattr(NetworkReputationModel, col_name) + 1},
                **extra_values,
            )
        )

        # Append contribution log
        self.session.add(
            NetworkContributionLogModel(
                store_id=store_id,
                phone_hash=phone_hash,
                event_type=event_type,
            )
        )
        await self.session.flush()

    async def update_store_count(self, phone_hash: str) -> None:
        """Recompute contributing_store_count from the contribution log."""
        result = await self.session.execute(
            select(
                func.count(func.distinct(NetworkContributionLogModel.store_id))
            ).where(NetworkContributionLogModel.phone_hash == phone_hash)
        )
        count = result.scalar_one() or 0
        await self.session.execute(
            update(NetworkReputationModel)
            .where(NetworkReputationModel.phone_hash == phone_hash)
            .values(contributing_store_count=count)
        )

    async def recompute_cached_score(self, phone_hash: str) -> None:
        """Recompute and persist the cached network_risk_score and confidence_level."""
        from src.application.use_cases.shopify.risk_scoring_engine import (
            compute_network_score,
        )

        rep = await self.get_by_phone_hash(phone_hash)
        if rep is None:
            return

        score, confidence, _label = compute_network_score(
            total_orders=rep.total_network_orders,
            total_rtos=rep.total_network_rtos,
            total_deliveries=rep.total_successful_deliveries,
            total_refunds=rep.total_refunds,
            contributing_store_count=rep.contributing_store_count,
        )
        await self.session.execute(
            update(NetworkReputationModel)
            .where(NetworkReputationModel.phone_hash == phone_hash)
            .values(network_risk_score=score, confidence_level=confidence)
        )


# ---------------------------------------------------------------------------
# PaymentLinkSessionRepository
# ---------------------------------------------------------------------------


class PaymentLinkSessionRepository:
    """CRUD for payment_link_sessions table."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, **kwargs) -> PaymentLinkSessionModel:
        model = PaymentLinkSessionModel(**kwargs)
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_by_id(self, session_id: UUID) -> PaymentLinkSessionModel | None:
        result = await self.session.execute(
            select(PaymentLinkSessionModel).where(
                PaymentLinkSessionModel.id == session_id
            )
        )
        return result.scalar_one_or_none()

    async def mark_completed(
        self,
        session_id: UUID,
        *,
        gateway_used: str,
        gateway_transaction_id: str,
    ) -> PaymentLinkSessionModel | None:
        model = await self.get_by_id(session_id)
        if not model:
            return None
        model.status = "completed"
        model.gateway_used = gateway_used
        model.gateway_transaction_id = gateway_transaction_id
        model.completed_at = func.now()
        self.session.add(model)
        await self.session.flush()
        return model

    async def aggregate_conversions(self, store_id: UUID, *, days: int = 30) -> dict:
        """Aggregate COD-to-Prepaid conversion metrics."""
        since = datetime.utcnow() - timedelta(days=days)

        total_result = await self.session.execute(
            select(func.count()).where(
                PaymentLinkSessionModel.store_id == store_id,
                PaymentLinkSessionModel.created_at >= since,
            )
        )
        total_sent = total_result.scalar_one() or 0

        completed_result = await self.session.execute(
            select(
                func.count(),
                func.coalesce(func.sum(PaymentLinkSessionModel.amount_cents), 0),
            ).where(
                PaymentLinkSessionModel.store_id == store_id,
                PaymentLinkSessionModel.status == "completed",
                PaymentLinkSessionModel.created_at >= since,
            )
        )
        row = completed_result.one()
        completed = row[0] or 0
        revenue = row[1] or 0

        return {
            "links_sent": total_sent,
            "links_completed": completed,
            "conversion_rate": round(
                (completed / total_sent * 100) if total_sent else 0.0, 1
            ),
            "conversion_revenue_cents": revenue,
        }
