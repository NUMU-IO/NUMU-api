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

from sqlalchemy import String as SAString
from sqlalchemy import and_, case, cast, func, or_, select
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

# Minimum sample sizes before a sub-score is treated as meaningful.
# Below these counts, the sub-score is excluded from the weighted average
# (and its weight is redistributed across the remaining metrics). This
# prevents a brand-new store with one delivered order from getting a
# perfect grade.
MIN_SHIPMENTS_FOR_DELIVERY = 5
MIN_COD_SHIPMENTS = 3
MIN_ORDERS_FOR_COMPLETION = 5
MIN_ORDERS_FOR_RETURN_RATE = 5
MIN_ORDERS_FOR_RESPONSE_TIME = 3


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


_RECS = {
    "delivery": {
        "ar": "معدل التوصيل الناجح منخفض — تأكد من صحة عناوين العملاء قبل الشحن",
        "en": "Low delivery success rate — verify customer addresses before shipping",
    },
    "cod": {
        "ar": "معدل رفض الدفع عند الاستلام مرتفع — فعّل تأكيد الطلب عبر OTP",
        "en": "High COD rejection rate — enable order confirmation via OTP",
    },
    "completion": {
        "ar": "معدل إتمام الطلبات منخفض — تابع الطلبات المعلقة وسرّع التجهيز",
        "en": "Low order completion rate — follow up on pending orders and speed up fulfillment",
    },
    "returns": {
        "ar": "معدل الإرجاع مرتفع — راجع وصف المنتجات وجودة الصور",
        "en": "High return rate — review product descriptions and image quality",
    },
    "speed": {
        "ar": "سرعة التجهيز بطيئة — حاول شحن الطلبات خلال 24 ساعة",
        "en": "Slow fulfillment speed — try to ship orders within 24 hours",
    },
    "great": {
        "ar": "أداء متجرك ممتاز! استمر على هذا المستوى 🎉",
        "en": "Your store is performing great! Keep it up 🎉",
    },
}


def build_recommendations(
    sub_scores: dict[str, int],
    final_score: int,
    insufficient_metrics: set[str],
    lang: str = "ar",
) -> list[str]:
    """Build localized recommendations from sub-scores.

    Skips metrics flagged as insufficient_data so we don't nag a new
    merchant about a metric we can't measure yet.
    """
    lang = lang if lang in ("ar", "en") else "ar"
    recs: list[str] = []

    if (
        "delivery_success" not in insufficient_metrics
        and sub_scores["delivery_success"] < 60
    ):
        recs.append(_RECS["delivery"][lang])
    if (
        "cod_acceptance" not in insufficient_metrics
        and sub_scores["cod_acceptance"] < 60
    ):
        recs.append(_RECS["cod"][lang])
    if (
        "order_completion" not in insufficient_metrics
        and sub_scores["order_completion"] < 60
    ):
        recs.append(_RECS["completion"][lang])
    if "low_return" not in insufficient_metrics and sub_scores["low_return"] < 50:
        recs.append(_RECS["returns"][lang])
    if "response_time" not in insufficient_metrics and sub_scores["response_time"] < 50:
        recs.append(_RECS["speed"][lang])

    if not recs and final_score >= 80 and len(insufficient_metrics) < len(WEIGHTS):
        recs.append(_RECS["great"][lang])

    return recs


async def calculate_store_health_score(
    session: AsyncSession,
    store_id: UUID,
    days: int = 30,
    lang: str = "ar",
) -> dict:
    """Calculate health score for a store over the given period.

    Returns:
        {
            "score": int | None (0-100, or None when no data at all),
            "grade": str (A/B/C/D/F or "—" when no data),
            "insufficient_data": bool,  # True when no orders AND no shipments
            "insufficient_metrics": list[str],  # metric keys with not enough data
            "metrics": {...},
            "sub_scores": {...},
            "recommendations": list[str],
            "orders_analyzed": int,
            "shipments_analyzed": int,
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
        (delivered_shipments / total_shipments) if total_shipments > 0 else 0.0
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
                        1,
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
    cod_acceptance_rate = (cod_collected / total_cod) if total_cod > 0 else 0.0

    # === 3. Order Completion Rate ===
    # Orders that reached DELIVERED / total orders (excluding very recent
    # in-flight orders that haven't had a chance to reach delivery yet).
    status_text = cast(OrderModel.status, SAString)
    order_stats = await session.execute(
        select(
            func.count().label("total"),
            func.sum(
                case((status_text.in_(["DELIVERED", "delivered"]), 1), else_=0)
            ).label("completed"),
            func.sum(
                case(
                    (
                        status_text.in_([
                            "RETURNED",
                            "returned",
                        ]),
                        1,
                    ),
                    else_=0,
                )
            ).label("returned"),
        ).where(
            and_(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= period_start,
                OrderModel.created_at
                <= now - timedelta(days=1),  # Exclude last day (still in progress)
            )
        )
    )
    order_row = order_stats.one()
    total_orders = order_row.total or 0
    completed_orders = order_row.completed or 0
    returned_orders = order_row.returned or 0
    order_completion_rate = (
        (completed_orders / total_orders) if total_orders > 0 else 0.0
    )

    # === 4. Return Rate ===
    # Two signals combined:
    #   (a) Approved/processing/completed refunds via the Refund flow
    #   (b) Orders that landed in RETURNED status (manual RTO recorded by
    #       merchants without a connected carrier).
    # Denominator = delivered orders + returned orders (i.e. all orders
    # that actually shipped to a customer in the period).
    refund_status_text = cast(RefundModel.status, SAString)
    refund_stats = await session.execute(
        select(func.count().label("total_refunds")).where(
            and_(
                RefundModel.store_id == store_id,
                RefundModel.created_at >= period_start,
                or_(
                    refund_status_text.in_(["APPROVED", "COMPLETED", "PROCESSING"]),
                    refund_status_text.in_(["approved", "completed", "processing"]),
                ),
            )
        )
    )
    total_refunds = refund_stats.scalar() or 0

    return_signals = total_refunds + returned_orders
    return_denominator = completed_orders + returned_orders
    return_rate = (
        (return_signals / return_denominator) if return_denominator > 0 else 0.0
    )

    # === 5. Average Response Time ===
    # Time from order creation to fulfillment (fulfilled_at - created_at)
    response_stats = await session.execute(
        select(
            func.count().label("fulfilled_count"),
            func.avg(
                func.extract("epoch", OrderModel.fulfilled_at - OrderModel.created_at)
                / 3600
            ).label("avg_hours"),
        ).where(
            and_(
                OrderModel.store_id == store_id,
                OrderModel.created_at >= period_start,
                OrderModel.fulfilled_at.isnot(None),
            )
        )
    )
    resp_row = response_stats.one()
    fulfilled_count = resp_row.fulfilled_count or 0
    avg_response_hours = float(resp_row.avg_hours or 0.0)

    # === Detect insufficient-data metrics ===
    # When a metric doesn't have enough samples, we exclude it from the
    # weighted average and redistribute its weight pro-rata across the
    # remaining metrics. If everything is below threshold we surface
    # `insufficient_data: True` so the UI can show a friendly empty state
    # instead of a misleading grade.
    insufficient_metrics: set[str] = set()
    if total_shipments < MIN_SHIPMENTS_FOR_DELIVERY:
        insufficient_metrics.add("delivery_success")
    if total_cod < MIN_COD_SHIPMENTS:
        insufficient_metrics.add("cod_acceptance")
    if total_orders < MIN_ORDERS_FOR_COMPLETION:
        insufficient_metrics.add("order_completion")
    if return_denominator < MIN_ORDERS_FOR_RETURN_RATE:
        insufficient_metrics.add("low_return")
    if fulfilled_count < MIN_ORDERS_FOR_RESPONSE_TIME:
        insufficient_metrics.add("response_time")

    insufficient_data = total_shipments == 0 and total_orders == 0 and total_cod == 0

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

    # === Weighted final score over metrics that have enough data ===
    if insufficient_data:
        final_score: int | None = None
        grade = "—"
    else:
        usable = {k: v for k, v in WEIGHTS.items() if k not in insufficient_metrics}
        if not usable:
            # Some samples exist, but none reach the per-metric threshold.
            # Fall back to a flat average so the merchant still sees a number.
            usable = WEIGHTS
            insufficient_metrics.clear()

        total_weight = sum(usable.values())
        final_score = int(
            sum(sub_scores[k] * (usable[k] / total_weight) for k in usable)
        )
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

    recommendations = build_recommendations(
        sub_scores=sub_scores,
        final_score=final_score or 0,
        insufficient_metrics=insufficient_metrics,
        lang=lang,
    )

    return {
        "score": final_score,
        "grade": grade,
        "insufficient_data": insufficient_data,
        "insufficient_metrics": sorted(insufficient_metrics),
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
