"""Celery task: full 5-factor async risk score for Shopify COD orders.

Upgrade path
------------
1. ``orders/create`` webhook calls ``score_order_fast()`` synchronously
   (2 factors, <200ms) and persists ``score_type="preliminary"``.
2. This task is enqueued immediately after persistence and runs within
   the Celery ``default`` queue.
3. When the task completes it overwrites the risk score with the full
   5-factor result and sets ``score_type="final"`` + ``scored_at``.

Safety rule: if this task fails for any reason the preliminary score
remains operative.  The order is NEVER left in a broken state.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from uuid import UUID

from src.infrastructure.messaging.celery_app import celery_app

logger = logging.getLogger(__name__)

_task_loop: asyncio.AbstractEventLoop | None = None


def _run_async(coro):
    """Run an async coroutine in a persistent per-worker event loop."""
    global _task_loop
    if _task_loop is None or _task_loop.is_closed():
        _task_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_task_loop)
    return _task_loop.run_until_complete(coro)


@celery_app.task(
    name="tasks.compute_full_risk_score",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    soft_time_limit=15,
)
def compute_full_risk_score(
    self,
    assessment_id: str,
    store_id: str,
    total_cents: int,
    payment_method: str | None,
    customer_total_orders: int,
    customer_cancellation_rate: float | None,
    address: str | None,
    phone: str | None,
    avg_order_cents: int,
) -> dict:
    """Compute and persist the full 5-factor risk score for a COD order.

    Parameters
    ----------
    assessment_id:
        UUID string of the ``risk_assessments`` row to update.
    store_id:
        UUID string of the owning store (for logging context only).
    total_cents:
        Order total in smallest currency unit.
    payment_method:
        Gateway name from Shopify (e.g. ``"cash_on_delivery"``).
    customer_total_orders:
        Lifetime order count for this customer from the Shopify payload.
    customer_cancellation_rate:
        Historical cancellation rate (0.0–1.0) or ``None`` if unknown.
    address:
        Formatted shipping address string for quality assessment.
    phone:
        Raw phone number string for format validation.
    avg_order_cents:
        Store rolling average at the time of the order (from Redis).
    """

    async def _run() -> dict:
        from datetime import datetime

        from sqlalchemy import func as sa_func
        from sqlalchemy import select, text, update

        from src.application.use_cases.shopify.automation_engine import (
            OrderContext,
            evaluate_rules,
        )
        from src.application.use_cases.shopify.execute_actions import execute_actions
        from src.application.use_cases.shopify.phone_hash import normalize_and_hash
        from src.application.use_cases.shopify.risk_scoring_engine import (
            compute_network_score,
            score_order,
        )
        from src.config.settings import get_settings
        from src.infrastructure.database.connection import AsyncSessionLocal
        from src.infrastructure.database.models.tenant.automation_log import (
            AutomationLogModel,
        )
        from src.infrastructure.database.models.tenant.automation_rule import (
            AutomationRuleModel,
        )
        from src.infrastructure.database.models.tenant.network_reputation import (
            NetworkReputationModel,
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

        sid = UUID(store_id)

        # ── 1. Look up network reputation for this phone number ────────────
        net_score = 55
        net_label = "new_to_network"
        if phone:
            salt = get_settings().platform_secret_salt
            if salt:
                phone_hash = normalize_and_hash(phone, salt)
                if phone_hash:
                    async with AsyncSessionLocal() as lookup_session:
                        await lookup_session.execute(text("SET search_path TO public"))
                        result = await lookup_session.execute(
                            select(NetworkReputationModel).where(
                                NetworkReputationModel.phone_hash == phone_hash
                            )
                        )
                        rep = result.scalar_one_or_none()
                        if rep is not None:
                            net_score, _conf, net_label = compute_network_score(
                                total_orders=rep.total_network_orders,
                                total_rtos=rep.total_network_rtos,
                                total_deliveries=rep.total_successful_deliveries,
                                total_refunds=rep.total_refunds,
                                contributing_store_count=rep.contributing_store_count,
                            )

        # ── 2. Enrich customer_cancellation_rate from store history ────────
        enriched_cancel_rate = customer_cancellation_rate
        async with AsyncSessionLocal() as enrich_session:
            await enrich_session.execute(text("SET search_path TO public"))
            # Read the current assessment to get customer_email
            assess_row = await enrich_session.execute(
                select(RiskAssessmentModel).where(
                    RiskAssessmentModel.id == UUID(assessment_id)
                )
            )
            assessment = assess_row.scalar_one_or_none()
            if assessment and assessment.customer_email:
                # Count total orders and cancellations for this customer in this store
                total_row = await enrich_session.execute(
                    select(sa_func.count()).where(
                        RiskAssessmentModel.store_id == sid,
                        RiskAssessmentModel.customer_email == assessment.customer_email,
                    )
                )
                total_count = total_row.scalar_one() or 0

                cancel_row = await enrich_session.execute(
                    select(sa_func.count()).where(
                        RiskAssessmentModel.store_id == sid,
                        RiskAssessmentModel.customer_email == assessment.customer_email,
                        RiskAssessmentModel.action_taken.in_([
                            "auto_cancelled",
                            "cancelled",
                            "manual_cancelled",
                        ]),
                    )
                )
                cancel_count = cancel_row.scalar_one() or 0

                if total_count > 0:
                    enriched_cancel_rate = cancel_count / total_count
                    logger.info(
                        "Enriched cancellation rate for %s: %d/%d = %.2f",
                        assessment.customer_email,
                        cancel_count,
                        total_count,
                        enriched_cancel_rate,
                    )

        # ── 3. Compute full 5-factor score ─────────────────────────────────
        full_result = score_order(
            total_cents=total_cents,
            payment_method=payment_method,
            customer_total_orders=customer_total_orders,
            customer_cancellation_rate=enriched_cancel_rate,
            address=address,
            phone=phone,
            avg_order_cents=avg_order_cents,
            network_score=net_score,
            network_label=net_label,
        )

        factors_json = [
            {
                "name": f.factor,
                "score": f.score,
                "weight": f.weight,
                "detail": f.reason,
            }
            for f in full_result.factors
        ]

        # ── 4. Persist final score ─────────────────────────────────────────
        async with AsyncSessionLocal() as session:
            await session.execute(text("SET search_path TO public"))
            await session.execute(
                update(RiskAssessmentModel)
                .where(RiskAssessmentModel.id == UUID(assessment_id))
                .values(
                    risk_score=full_result.risk_score,
                    risk_level=full_result.risk_level,
                    suggested_action=full_result.suggested_action,
                    factors=factors_json,
                    score_type="final",
                    scored_at=datetime.now(UTC),
                )
            )

            # ── 5. Threshold-based auto-cancel (final score only) ──────────
            # Also enforces the 30-day installation safety gate.
            settings_row = await session.execute(
                select(ShopifyAppSettingsModel).where(
                    ShopifyAppSettingsModel.store_id == sid
                )
            )
            settings = settings_row.scalar_one_or_none()

            # Check 30-day installation gate before auto-cancelling
            cancel_allowed = True
            install_row = await session.execute(
                select(ShopifyInstallationModel).where(
                    ShopifyInstallationModel.store_id == sid,
                    ShopifyInstallationModel.is_active.is_(True),
                )
            )
            installation = install_row.scalar_one_or_none()
            if installation and installation.installed_at:
                installed = installation.installed_at
                if not installed.tzinfo:
                    installed = installed.replace(tzinfo=UTC)
                days_since = (datetime.now(UTC) - installed).days
                if days_since < 30:
                    cancel_allowed = False
                    logger.info(
                        "Auto-cancel blocked: installation %d days old (requires 30+)",
                        days_since,
                    )

            if (
                settings
                and settings.cod_risk_scoring_enabled
                and full_result.risk_score >= settings.auto_cancel_threshold
                and cancel_allowed
            ):
                await session.execute(
                    update(RiskAssessmentModel)
                    .where(RiskAssessmentModel.id == UUID(assessment_id))
                    .values(action_taken="auto_cancelled")
                )
                logger.info(
                    "Auto-cancel applied on final score: assessment=%s score=%d threshold=%d",
                    assessment_id,
                    full_result.risk_score,
                    settings.auto_cancel_threshold,
                )

            # ── 6. Run automation rules (risk_scored trigger) ──────────────
            rules_result = await session.execute(
                select(AutomationRuleModel)
                .where(
                    AutomationRuleModel.store_id == sid,
                    AutomationRuleModel.is_active.is_(True),
                    AutomationRuleModel.trigger_event == "risk_scored",
                )
                .order_by(
                    AutomationRuleModel.priority.desc(),
                    AutomationRuleModel.created_at,
                )
            )
            risk_scored_rules = list(rules_result.scalars().all())

            if risk_scored_rules:
                install_row = await session.execute(
                    select(ShopifyInstallationModel).where(
                        ShopifyInstallationModel.store_id == sid,
                        ShopifyInstallationModel.is_active.is_(True),
                    )
                )
                installation = install_row.scalar_one_or_none()

                ctx = OrderContext(
                    risk_score=full_result.risk_score,
                    risk_level=full_result.risk_level,
                    score_type="final",
                    payment_method=payment_method or "unknown",
                    total_cents=total_cents,
                    customer_total_orders=customer_total_orders,
                    customer_cancellation_rate=enriched_cancel_rate,
                    installed_at=installation.installed_at if installation else None,
                )
                resolved = evaluate_rules(
                    risk_scored_rules, ctx, trigger_event="risk_scored"
                )

                if resolved and installation:
                    # Get assessment details for logging
                    order_id_for_log = ""
                    order_number_for_log = ""
                    if assessment:
                        order_id_for_log = str(assessment.shopify_order_id or "")
                        order_number_for_log = str(assessment.order_number or "")

                    action_results = await execute_actions(
                        actions=resolved,
                        shop_domain=installation.shopify_domain,
                        access_token=installation.access_token_encrypted,
                        shopify_order_id=order_id_for_log,
                        risk_score=full_result.risk_score,
                        risk_level=full_result.risk_level,
                        score_type="final",
                        store_id=store_id,
                        order_number=order_number_for_log,
                        total_cents=total_cents,
                        currency="EGP",
                        customer_phone=phone or "",
                        customer_name=assessment.customer_name if assessment else "",
                    )

                    # Log and bump triggered count
                    logged_rule_ids = set()
                    for ra in resolved:
                        if ra.source_rule_id not in logged_rule_ids:
                            logged_rule_ids.add(ra.source_rule_id)
                            rule_actions = [
                                a
                                for a in resolved
                                if a.source_rule_id == ra.source_rule_id
                            ]
                            rule_results = [
                                r
                                for r, a in zip(action_results, resolved)
                                if a.source_rule_id == ra.source_rule_id
                            ]
                            all_ok = all(r.success for r in rule_results)
                            any_ok = any(r.success for r in rule_results)
                            log_status = (
                                "success"
                                if all_ok
                                else "partial_failure"
                                if any_ok
                                else "failed"
                            )
                            log = AutomationLogModel(
                                store_id=sid,
                                rule_id=ra.source_rule_id,
                                rule_name=ra.source_rule_name,
                                order_id=order_id_for_log or None,
                                order_number=order_number_for_log or None,
                                trigger_event="risk_scored",
                                actions_executed=[
                                    {"type": a.action_type, **a.params}
                                    for a in rule_actions
                                ],
                                status=log_status,
                            )
                            session.add(log)

                            # Bump times_triggered
                            await session.execute(
                                update(AutomationRuleModel)
                                .where(
                                    AutomationRuleModel.id == UUID(ra.source_rule_id)
                                )
                                .values(
                                    times_triggered=(
                                        AutomationRuleModel.times_triggered + 1
                                    ),
                                    last_triggered_at=sa_func.now(),
                                )
                            )

            await session.commit()

        logger.info(
            "Full risk score computed: assessment=%s store=%s score=%d level=%s",
            assessment_id,
            store_id,
            full_result.risk_score,
            full_result.risk_level,
        )
        return {
            "assessment_id": assessment_id,
            "risk_score": full_result.risk_score,
            "risk_level": full_result.risk_level,
            "score_type": "final",
        }

    try:
        return _run_async(_run())
    except Exception as exc:
        logger.error(
            "Full risk scoring failed for assessment %s (store %s): %s",
            assessment_id,
            store_id,
            exc,
            exc_info=True,
        )
        # Retry only on transient infrastructure errors.
        # On all other failures the preliminary score stays operative —
        # do NOT re-raise so the order is never left in a broken state.
        exc_str = str(exc).lower()
        if any(kw in exc_str for kw in ("connection", "timeout", "unavailable")):
            raise self.retry(exc=exc)
        return {
            "assessment_id": assessment_id,
            "error": str(exc),
            "score_type": "preliminary",
        }
