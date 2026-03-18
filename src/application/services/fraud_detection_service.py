"""Fraud detection service.

PAY-05: Basic fraud detection with velocity checks, large-amount detection,
billing/shipping mismatch, and queuing flagged orders for manual review.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.risk_assessment import (
    RiskAssessmentModel,
)

logger = logging.getLogger(__name__)

# ── Thresholds ────────────────────────────────────────────────────────────────

VELOCITY_WINDOW_MINUTES: int = 60
VELOCITY_THRESHOLD: int = 3  # more than N orders in the window → flag

# 5 000 EGP absolute ceiling; also flag if > multiplier × store average
LARGE_AMOUNT_ABSOLUTE_CENTS: int = 500_000
LARGE_AMOUNT_MULTIPLIER: float = 3.0


# ── Result types ─────────────────────────────────────────────────────────────


@dataclass
class FraudFlag:
    name: str
    description: str
    severity: str  # low | medium | high | critical


@dataclass
class FraudCheckResult:
    order_id: UUID
    risk_score: int
    risk_level: str  # low | medium | high | critical
    suggested_action: str  # auto_approve | review | hold | reject
    flags: list[FraudFlag] = field(default_factory=list)
    requires_review: bool = False


# ── Service ───────────────────────────────────────────────────────────────────


class FraudDetectionService:
    """Evaluates orders for fraud risk using heuristic rules.

    All checks are independent; results are combined into a single score
    and persisted as a RiskAssessmentModel record.
    """

    # -- individual checks ----------------------------------------------------

    async def check_velocity(
        self,
        ip_address: str,
        store_id: UUID,
        session: AsyncSession,
        window_minutes: int = VELOCITY_WINDOW_MINUTES,
        threshold: int = VELOCITY_THRESHOLD,
    ) -> tuple[bool, int]:
        """Count recent orders from the same IP address.

        Returns (flagged, count).  flagged is True when count > threshold.
        """
        window_start = datetime.now(UTC) - timedelta(minutes=window_minutes)
        stmt = (
            select(func.count())
            .select_from(OrderModel)
            .where(OrderModel.store_id == store_id)
            .where(OrderModel.extra_data["ip_address"].astext == ip_address)
            .where(OrderModel.created_at >= window_start)
        )
        result = await session.execute(stmt)
        count: int = result.scalar_one() or 0
        return count > threshold, count

    def check_large_amount(
        self,
        total_cents: int,
        store_avg_cents: int = 80_000,  # default 800 EGP
    ) -> tuple[bool, float]:
        """Flag orders that exceed an absolute ceiling or a multiple of the store average.

        Returns (flagged, ratio_vs_average).
        """
        avg = max(store_avg_cents, 1)
        ratio = total_cents / avg
        flagged = (
            total_cents > LARGE_AMOUNT_ABSOLUTE_CENTS or ratio > LARGE_AMOUNT_MULTIPLIER
        )
        return flagged, ratio

    def check_address_mismatch(
        self,
        shipping_address: dict,
        billing_address: dict | None,
    ) -> bool:
        """Return True when billing and shipping addresses differ in city or country."""
        if not billing_address:
            return False
        s_city = (shipping_address.get("city") or "").lower().strip()
        b_city = (billing_address.get("city") or "").lower().strip()
        s_country = (shipping_address.get("country") or "").lower().strip()
        b_country = (billing_address.get("country") or "").lower().strip()
        if s_country and b_country and s_country != b_country:
            return True
        if s_city and b_city and s_city != b_city:
            return True
        return False

    # -- orchestrator ---------------------------------------------------------

    async def assess_order(
        self,
        *,
        order_id: UUID,
        store_id: UUID,
        tenant_id: UUID | None,
        order_number: str,
        total_cents: int,
        currency: str,
        payment_method: str | None,
        customer_name: str | None,
        customer_email: str | None,
        shipping_address: dict,
        billing_address: dict | None,
        ip_address: str | None,
        session: AsyncSession,
        store_avg_cents: int = 80_000,
    ) -> FraudCheckResult:
        """Run all fraud checks, persist the result, and return a FraudCheckResult.

        The caller is responsible for committing (or rolling back) the session.
        """
        flags: list[FraudFlag] = []

        # 1. Velocity
        if ip_address:
            flagged, count = await self.check_velocity(ip_address, store_id, session)
            if flagged:
                flags.append(
                    FraudFlag(
                        name="velocity",
                        description=(
                            f"{count} orders placed from IP {ip_address} "
                            f"in the last {VELOCITY_WINDOW_MINUTES} minutes"
                        ),
                        severity="high",
                    )
                )

        # 2. Large amount
        amount_flagged, ratio = self.check_large_amount(total_cents, store_avg_cents)
        if amount_flagged:
            flags.append(
                FraudFlag(
                    name="large_amount",
                    description=(
                        f"Order total {total_cents / 100:.0f} {currency} "
                        f"is {ratio:.1f}× the store average"
                    ),
                    severity="medium"
                    if ratio < LARGE_AMOUNT_MULTIPLIER * 2
                    else "high",
                )
            )

        # 3. Address mismatch
        if billing_address and self.check_address_mismatch(
            shipping_address, billing_address
        ):
            flags.append(
                FraudFlag(
                    name="address_mismatch",
                    description="Billing city/country differs from shipping city/country",
                    severity="medium",
                )
            )

        # ── Scoring ──────────────────────────────────────────────────────────
        _severity_base = {"low": 20, "medium": 40, "high": 70, "critical": 90}
        if flags:
            max_score = max(_severity_base[f.severity] for f in flags)
            bonus = (len(flags) - 1) * 10  # each additional flag adds 10 pts
            risk_score = min(max_score + bonus, 100)
        else:
            risk_score = 5  # baseline

        risk_level = self._risk_level(risk_score)
        suggested_action = self._suggested_action(risk_score)
        requires_review = risk_level in ("high", "critical")

        # ── Persist ──────────────────────────────────────────────────────────
        assessment = RiskAssessmentModel(
            tenant_id=tenant_id,
            store_id=store_id,
            order_id=order_id,
            order_number=order_number,
            customer_name=customer_name,
            customer_email=customer_email,
            total_cents=total_cents,
            currency=currency,
            payment_method=payment_method,
            risk_score=risk_score,
            risk_level=risk_level,
            suggested_action=suggested_action,
            factors=[
                {
                    "factor": f.name,
                    "description": f.description,
                    "severity": f.severity,
                }
                for f in flags
            ],
        )
        session.add(assessment)
        await session.flush()

        logger.info(
            "fraud_assessment_complete",
            extra={
                "order_id": str(order_id),
                "order_number": order_number,
                "risk_score": risk_score,
                "risk_level": risk_level,
                "flags": [f.name for f in flags],
                "requires_review": requires_review,
            },
        )
        if requires_review:
            logger.warning(
                f"Order {order_number} flagged for manual review "
                f"(risk_level={risk_level}, score={risk_score})"
            )

        return FraudCheckResult(
            order_id=order_id,
            risk_score=risk_score,
            risk_level=risk_level,
            suggested_action=suggested_action,
            flags=flags,
            requires_review=requires_review,
        )

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _risk_level(score: int) -> str:
        if score >= 80:
            return "critical"
        if score >= 60:
            return "high"
        if score >= 40:
            return "medium"
        return "low"

    @staticmethod
    def _suggested_action(score: int) -> str:
        if score >= 80:
            return "reject"
        if score >= 60:
            return "hold"
        if score >= 30:
            return "review"
        return "auto_approve"
