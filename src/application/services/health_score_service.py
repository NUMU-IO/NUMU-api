"""Merchant Health Score calculation service.

Computes a 0-100 health score for each store based on:
- Delivery Success Rate (weight: 30%)
- COD Rejection Rate (weight: 25%)
- Order Completion Rate (weight: 20%)
- Return Rate (weight: 15%)
- Average Response Time (weight: 10%)

Score is computed daily via Celery Beat and stored in store.settings.
"""

import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.shipment import ShipmentModel

logger = logging.getLogger(__name__)

# Score weights (must sum to 1.0)
WEIGHTS = {
    "delivery_success": 0.30,
    "cod_acceptance": 0.25,
    "order_completion": 0.20,
    "low_return": 0.15,
    "response_time": 0.10,
}

# Thresholds for each metric (maps metric value to a 0-100 sub-score)
# delivery_success_rate: 95%+ = 100, 80% = 50, <60% = 0
# cod_acceptance_rate: 90%+ = 100, 70% = 50, <50% = 0
# order_completion_rate: 95%+ = 100, 80% = 50, <60% = 0
# return_rate: 0% = 100, 5% = 70, 10% = 40, >20% = 0 (inverted - lower is better)
# response_time: <2h = 100, <12h = 70, <24h = 40, >48h = 0 (inverted - lower is better)


def _rate_to_score(rate: float, thresholds: list[tuple[float, int]]) -> int:
    """Convert a rate to a 0-100 score using linear interpolation between thresholds.

    thresholds: list of (rate_value, score) pairs, sorted ascending by rate_value.
    """
    if not thresholds:
        return 50

    # Below lowest threshold
    if rate <= thresholds[0][0]:
        return thresholds[0][1]

    # Above highest threshold
    if rate >= thresholds[-1][0]:
        return thresholds[-1][1]

    # Interpolate between two surrounding thresholds
    for i in range(len(thresholds) - 1):
        low_rate, low_score = thresholds[i]
        high_rate, high_score = thresholds[i + 1]
        if low_rate <= rate <= high_rate:
            ratio = (
                (rate - low_rate) / (high_rate - low_rate)
                if high_rate != low_rate
                else 0
            )
            return int(low_score + ratio * (high_score - low_score))

    return 50


# Higher rate = better score
DELIVERY_SUCCESS_THRESHOLDS = [
    (0.0, 0),
    (0.6, 10),
    (0.8, 50),
    (0.9, 75),
    (0.95, 90),
    (1.0, 100),
]
COD_ACCEPTANCE_THRESHOLDS = [
    (0.0, 0),
    (0.5, 10),
    (0.7, 50),
    (0.85, 75),
    (0.9, 90),
    (1.0, 100),
]
ORDER_COMPLETION_THRESHOLDS = [
    (0.0, 0),
    (0.6, 10),
    (0.8, 50),
    (0.9, 75),
    (0.95, 90),
    (1.0, 100),
]

# Lower rate = better score (inverted)
RETURN_RATE_THRESHOLDS = [
    (0.0, 100),
    (0.03, 85),
    (0.05, 70),
    (0.10, 40),
    (0.15, 20),
    (0.20, 0),
]
RESPONSE_TIME_THRESHOLDS = [
    (0.0, 100),
    (2.0, 90),
    (6.0, 75),
    (12.0, 50),
    (24.0, 30),
    (48.0, 0),
]


async def calculate_store_health_score(
    session: AsyncSession,
    store_id: UUID,
    days: int = 30,
) -> dict:
    """Calculate health score for a store over the given period.

    Returns:
        {
            "score": int (0-100),
            "grade": str (A/B/C/D/F),
            "metrics": {
                "delivery_success_rate": float,
                "cod_acceptance_rate": float,
                "order_completion_rate": float,
                "return_rate": float,
                "avg_response_hours": float,
            },
            "sub_scores": {
                "delivery_success": int,
                "cod_acceptance": int,
                "order_completion": int,
                "low_return": int,
                "response_time": int,
            },
            "recommendations": list[str],
            "calculated_at": str (ISO),
        }
    """
    now = datetime.now(UTC)
    period_start = now - timedelta(days=days)

    # === 1. Delivery Success Rate ===
    # Shipments that were delivered / total shipments (excluding cancelled before pickup)
    shipment_stats = await session.execute(
        select(
            func.count().label("total"),
            func.sum(case((ShipmentModel.status == "delivered", 1), else_=0)).label(
                "delivered"
            ),
            func.sum(case((ShipmentModel.status == "failed", 1), else_=0)).label(
                "failed"
            ),
            func.sum(case((ShipmentModel.status == "returned", 1), else_=0)).label(
                "returned"
            ),
        ).where(
            and_(
                ShipmentModel.store_id == store_id,
                ShipmentModel.created_at >= period_start,
                ShipmentModel.status != "cancelled",
                ShipmentModel.status != "pending",
            )
        )
    )
    ship_row = shipment_stats.one()
    total_shipments = ship_row.total or 0
    delivered_shipments = ship_row.delivered or 0
    delivery_success_rate = (
        (delivered_shipments / total_shipments) if total_shipments > 0 else 1.0
    )

    # === 2. COD Acceptance Rate ===
    # COD shipments delivered and collected / total COD shipments
    cod_stats = await session.execute(
        select(
            func.count().label("total_cod"),
            func.sum(
                case(
                    (
                        and_(
                            ShipmentModel.status == "delivered",
                            ShipmentModel.cod_collected.is_(True),
                        ),
                        1,  # noqa: E712
                    ),
                    else_=0,
                )
            ).label("cod_collected"),
        ).where(
            and_(
                ShipmentModel.store_id == store_id,
                ShipmentModel.cod_amount > 0,
                ShipmentModel.created_at >= period_start,
                ShipmentModel.status.notin_(["cancelled", "pending", "created"]),
            )
        )
    )
    cod_row = cod_stats.one()
    total_cod = cod_row.total_cod or 0
    cod_collected = cod_row.cod_collected or 0
    cod_acceptance_rate = (cod_collected / total_cod) if total_cod > 0 else 1.0

    # === 3. Order Completion Rate ===
    # Orders that reached DELIVERED / total orders (excluding very recent)
    # Note: OrderStatus DB enum uses UPPERCASE member names
    order_stats = await session.execute(
        select(
            func.count().label("total"),
            func.sum(case((OrderModel.status.in_(["DELIVERED"]), 1), else_=0)).label(
                "completed"
            ),
            func.sum(
                case(
                    (OrderModel.status.in_(["CANCELLED", "PAYMENT_FAILED"]), 1), else_=0
                )
            ).label("cancelled"),
        ).where(
            and_(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= period_start,
                OrderModel.created_at
                <= now - timedelta(days=3),  # Exclude last 3 days (still in progress)
            )
        )
    )
    order_row = order_stats.one()
    total_orders = order_row.total or 0
    completed_orders = order_row.completed or 0
    order_completion_rate = (
        (completed_orders / total_orders) if total_orders > 0 else 1.0
    )

    # === 4. Return Rate ===
    # Refunds / total completed orders
    # Note: RefundStatus DB enum uses UPPERCASE member names
    refund_stats = await session.execute(
        select(func.count().label("total_refunds")).where(
            and_(
                RefundModel.store_id == store_id,
                RefundModel.created_at >= period_start,
                RefundModel.status.in_(["APPROVED", "COMPLETED", "PROCESSING"]),
            )
        )
    )
    total_refunds = refund_stats.scalar() or 0
    return_rate = (total_refunds / completed_orders) if completed_orders > 0 else 0.0

    # === 5. Average Response Time ===
    # Time from order creation to fulfillment (fulfilled_at - created_at)
    response_stats = await session.execute(
        select(
            func.avg(
                func.extract("epoch", OrderModel.fulfilled_at - OrderModel.created_at)
                / 3600
            ).label("avg_hours")
        ).where(
            and_(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= period_start,
                OrderModel.fulfilled_at.isnot(None),
            )
        )
    )
    avg_response_hours = float(response_stats.scalar() or 24.0)

    # === Calculate sub-scores ===
    sub_scores = {
        "delivery_success": _rate_to_score(
            delivery_success_rate, DELIVERY_SUCCESS_THRESHOLDS
        ),
        "cod_acceptance": _rate_to_score(
            cod_acceptance_rate, COD_ACCEPTANCE_THRESHOLDS
        ),
        "order_completion": _rate_to_score(
            order_completion_rate, ORDER_COMPLETION_THRESHOLDS
        ),
        "low_return": _rate_to_score(return_rate, RETURN_RATE_THRESHOLDS),
        "response_time": _rate_to_score(avg_response_hours, RESPONSE_TIME_THRESHOLDS),
    }

    # === Weighted final score ===
    final_score = int(sum(sub_scores[key] * WEIGHTS[key] for key in WEIGHTS))
    final_score = max(0, min(100, final_score))

    # === Grade ===
    if final_score >= 90:
        grade = "A"
    elif final_score >= 75:
        grade = "B"
    elif final_score >= 60:
        grade = "C"
    elif final_score >= 40:
        grade = "D"
    else:
        grade = "F"

    # === Recommendations ===
    recommendations = []
    if sub_scores["delivery_success"] < 60:
        recommendations.append(
            "\u0645\u0639\u062f\u0644 \u0627\u0644\u062a\u0648\u0635\u064a\u0644 \u0627\u0644\u0646\u0627\u062c\u062d \u0645\u0646\u062e\u0641\u0636 \u2014 \u062a\u0623\u0643\u062f \u0645\u0646 \u0635\u062d\u0629 \u0639\u0646\u0627\u0648\u064a\u0646 \u0627\u0644\u0639\u0645\u0644\u0627\u0621 \u0642\u0628\u0644 \u0627\u0644\u0634\u062d\u0646"
        )
    if sub_scores["cod_acceptance"] < 60:
        recommendations.append(
            "\u0645\u0639\u062f\u0644 \u0631\u0641\u0636 \u0627\u0644\u062f\u0641\u0639 \u0639\u0646\u062f \u0627\u0644\u0627\u0633\u062a\u0644\u0627\u0645 \u0645\u0631\u062a\u0641\u0639 \u2014 \u0641\u0639\u0651\u0644 \u062a\u0623\u0643\u064a\u062f \u0627\u0644\u0637\u0644\u0628 \u0639\u0628\u0631 OTP"
        )
    if sub_scores["order_completion"] < 60:
        recommendations.append(
            "\u0645\u0639\u062f\u0644 \u0625\u062a\u0645\u0627\u0645 \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0645\u0646\u062e\u0641\u0636 \u2014 \u062a\u0627\u0628\u0639 \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0627\u0644\u0645\u0639\u0644\u0642\u0629 \u0648\u0633\u0631\u0651\u0639 \u0627\u0644\u062a\u062c\u0647\u064a\u0632"
        )
    if sub_scores["low_return"] < 50:
        recommendations.append(
            "\u0645\u0639\u062f\u0644 \u0627\u0644\u0625\u0631\u062c\u0627\u0639 \u0645\u0631\u062a\u0641\u0639 \u2014 \u0631\u0627\u062c\u0639 \u0648\u0635\u0641 \u0627\u0644\u0645\u0646\u062a\u062c\u0627\u062a \u0648\u062c\u0648\u062f\u0629 \u0627\u0644\u0635\u0648\u0631"
        )
    if sub_scores["response_time"] < 50:
        recommendations.append(
            "\u0633\u0631\u0639\u0629 \u0627\u0644\u062a\u062c\u0647\u064a\u0632 \u0628\u0637\u064a\u0626\u0629 \u2014 \u062d\u0627\u0648\u0644 \u0634\u062d\u0646 \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u062e\u0644\u0627\u0644 24 \u0633\u0627\u0639\u0629"
        )

    if not recommendations and final_score >= 80:
        recommendations.append(
            "\u0623\u062f\u0627\u0621 \u0645\u062a\u062c\u0631\u0643 \u0645\u0645\u062a\u0627\u0632! \u0627\u0633\u062a\u0645\u0631 \u0639\u0644\u0649 \u0647\u0630\u0627 \u0627\u0644\u0645\u0633\u062a\u0648\u0649 \ud83c\udf89"
        )

    return {
        "score": final_score,
        "grade": grade,
        "metrics": {
            "delivery_success_rate": round(delivery_success_rate * 100, 1),
            "cod_acceptance_rate": round(cod_acceptance_rate * 100, 1),
            "order_completion_rate": round(order_completion_rate * 100, 1),
            "return_rate": round(return_rate * 100, 1),
            "avg_response_hours": round(avg_response_hours, 1),
        },
        "sub_scores": sub_scores,
        "recommendations": recommendations,
        "orders_analyzed": total_orders,
        "shipments_analyzed": total_shipments,
        "calculated_at": now.isoformat(),
    }
