"""Unit tests for the merchant health score service.

These tests seed orders, shipments, and refunds directly into the test
database and assert on the score, grade, per-metric sub-scores, the
insufficient-data flags, and the recommendation language.

The math under test is intricate (5 thresholded sub-scores, weight
redistribution when a metric has too few samples, and an empty-state
branch when nothing is measurable) — these cases exist to keep that
math honest.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from src.application.services.health_score_service import (
    build_recommendations,
    calculate_store_health_score,
)
from src.core.entities.order import OrderStatus
from src.core.entities.refund import RefundReason, RefundStatus, RefundType
from src.infrastructure.database.models.tenant.order import OrderModel
from src.infrastructure.database.models.tenant.refund import RefundModel
from src.infrastructure.database.models.tenant.shipment import ShipmentModel

# ── Helpers ─────────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


async def _seed_shipment(
    session,
    *,
    store_id,
    order_id,
    status: str,
    cod_amount: int = 0,
    cod_collected: bool = False,
    days_ago: int = 5,
):
    sm = ShipmentModel(
        id=uuid4(),
        store_id=store_id,
        tenant_id=store_id,  # use store_id as tenant for tests
        order_id=order_id,
        carrier="bosta",
        status=status,
        cod_amount=cod_amount,
        cod_collected=cod_collected,
        created_at=_now() - timedelta(days=days_ago),
        updated_at=_now(),
        status_history=[],
    )
    session.add(sm)
    return sm


async def _seed_order(
    session,
    *,
    store_id,
    customer_id,
    status: OrderStatus,
    days_ago: int = 5,
    fulfilled_hours_after: float | None = None,
):
    created = _now() - timedelta(days=days_ago)
    fulfilled = (
        created + timedelta(hours=fulfilled_hours_after)
        if fulfilled_hours_after is not None
        else None
    )
    om = OrderModel(
        id=uuid4(),
        store_id=store_id,
        tenant_id=store_id,
        customer_id=customer_id,
        order_number=f"NUM-{uuid4().hex[:6]}",
        status=status,
        line_items=[],
        shipping_address={
            "first_name": "Test",
            "last_name": "User",
            "address_line1": "1 St",
            "city": "Cairo",
            "country": "EG",
            "phone": "+201111111111",
        },
        billing_address=None,
        subtotal=10000,
        shipping_cost=5000,
        tax_amount=0,
        discount_amount=0,
        total=15000,
        currency="EGP",
        created_at=created,
        updated_at=_now(),
        fulfilled_at=fulfilled,
        version=1,
    )
    session.add(om)
    return om


async def _seed_refund(session, *, store_id, order_id, status: RefundStatus):
    rm = RefundModel(
        id=uuid4(),
        store_id=store_id,
        tenant_id=store_id,
        order_id=order_id,
        refund_number=f"R-{uuid4().hex[:6]}",
        refund_type=RefundType.FULL,
        status=status,
        reason=RefundReason.CUSTOMER_REQUEST,
        amount=15000,
        currency="EGP",
        created_at=_now() - timedelta(days=5),
        updated_at=_now(),
    )
    session.add(rm)
    return rm


# ── build_recommendations (pure function, no DB) ────────────────────────


def test_build_recommendations_en_for_poor_metrics():
    subs = {
        "delivery_success": 30,
        "cod_acceptance": 40,
        "order_completion": 50,
        "low_return": 30,
        "response_time": 20,
    }
    recs = build_recommendations(
        subs, final_score=40, insufficient_metrics=set(), lang="en"
    )
    # Every metric is below threshold → 5 recs
    assert len(recs) == 5
    assert all(isinstance(r, str) for r in recs)
    # Recommendations should be in English
    assert any("delivery" in r.lower() for r in recs)
    assert any("cod" in r.lower() or "otp" in r.lower() for r in recs)


def test_build_recommendations_ar_for_poor_metrics():
    subs = {
        "delivery_success": 30,
        "cod_acceptance": 40,
        "order_completion": 50,
        "low_return": 30,
        "response_time": 20,
    }
    recs = build_recommendations(
        subs, final_score=40, insufficient_metrics=set(), lang="ar"
    )
    assert len(recs) == 5
    # Arabic text contains at least one Arabic letter
    assert any(any("؀" <= ch <= "ۿ" for ch in r) for r in recs)


def test_build_recommendations_skips_insufficient_metrics():
    """A metric flagged as insufficient_data should not produce a complaint."""
    subs = {
        "delivery_success": 30,  # bad but insufficient
        "cod_acceptance": 40,  # bad
        "order_completion": 90,  # great
        "low_return": 90,  # great
        "response_time": 90,  # great
    }
    recs = build_recommendations(
        subs,
        final_score=60,
        insufficient_metrics={"delivery_success"},
        lang="en",
    )
    # Only cod_acceptance should produce a complaint
    assert len(recs) == 1
    assert "cod" in recs[0].lower() or "otp" in recs[0].lower()


def test_build_recommendations_great_when_score_high():
    subs = dict.fromkeys(
        [
            "delivery_success",
            "cod_acceptance",
            "order_completion",
            "low_return",
            "response_time",
        ],
        95,
    )
    recs = build_recommendations(
        subs, final_score=95, insufficient_metrics=set(), lang="en"
    )
    assert len(recs) == 1
    assert "great" in recs[0].lower()


def test_build_recommendations_no_great_when_everything_insufficient():
    """Don't tell a store with no data that it's doing great."""
    subs = dict.fromkeys(
        [
            "delivery_success",
            "cod_acceptance",
            "order_completion",
            "low_return",
            "response_time",
        ],
        100,
    )
    recs = build_recommendations(
        subs,
        final_score=100,
        insufficient_metrics={
            "delivery_success",
            "cod_acceptance",
            "order_completion",
            "low_return",
            "response_time",
        },
        lang="en",
    )
    assert recs == []


# ── calculate_store_health_score (DB-driven) ────────────────────────────


@pytest.mark.asyncio
async def test_empty_store_returns_insufficient_data(test_session):
    """A brand-new store with no orders and no shipments must NOT get a
    numeric grade — the old bug returned 100/A here."""
    store_id = uuid4()
    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )

    assert result["insufficient_data"] is True
    assert result["score"] is None
    assert result["grade"] == "—"
    assert result["orders_analyzed"] == 0
    assert result["shipments_analyzed"] == 0
    # All five sub-scores were excluded for lack of data
    assert set(result["insufficient_metrics"]) == {
        "delivery_success",
        "cod_acceptance",
        "order_completion",
        "low_return",
        "response_time",
    }
    # Empty-state should produce no recommendations
    assert result["recommendations"] == []


@pytest.mark.asyncio
async def test_healthy_store_gets_high_grade(test_session):
    """A store with 10 successful deliveries, mostly delivered orders,
    no returns, and fast fulfillment should land in A territory."""
    store_id = uuid4()
    customer_id = uuid4()

    # 10 shipments — all delivered, 8 of them COD collected
    for i in range(10):
        oid = uuid4()
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED,
            days_ago=5,
            fulfilled_hours_after=1.0,  # 1 hour — very fast
        )
        await _seed_shipment(
            test_session,
            store_id=store_id,
            order_id=oid,
            status="delivered",
            cod_amount=15000 if i < 8 else 0,
            cod_collected=(i < 8),
            days_ago=5,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )

    assert result["insufficient_data"] is False
    assert result["score"] is not None
    assert result["score"] >= 90, f"expected A, got {result['score']}"
    assert result["grade"] == "A"
    assert result["metrics"]["delivery_success_rate"] == 100.0
    assert result["metrics"]["cod_acceptance_rate"] == 100.0
    assert result["metrics"]["return_rate"] == 0.0
    # 1 hour fulfillment is well under the 2h excellent threshold
    assert result["metrics"]["avg_response_hours"] < 2.0


@pytest.mark.asyncio
async def test_failed_deliveries_drop_grade(test_session):
    """7 delivered out of 10 shipments → 70% success → should fail
    the delivery sub-score badly enough to land below A."""
    store_id = uuid4()
    customer_id = uuid4()

    for i in range(10):
        oid = uuid4()
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED if i < 7 else OrderStatus.CANCELLED,
            days_ago=5,
            fulfilled_hours_after=1.0,
        )
        await _seed_shipment(
            test_session,
            store_id=store_id,
            order_id=oid,
            status="delivered" if i < 7 else "failed",
            days_ago=5,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )
    assert result["metrics"]["delivery_success_rate"] == 70.0
    # Below the (0.8, 50) threshold — sub-score should be modest
    assert result["sub_scores"]["delivery_success"] < 50
    assert result["grade"] in {"B", "C", "D", "F"}


@pytest.mark.asyncio
async def test_returned_orders_count_toward_return_rate(test_session):
    """OrderStatus.RETURNED orders should drive return_rate up even
    when there are zero refund rows — this is the bug we fixed."""
    store_id = uuid4()
    customer_id = uuid4()

    # 6 delivered + 4 returned = 10 shipped orders, 40% return rate
    for _ in range(6):
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED,
            days_ago=5,
            fulfilled_hours_after=1.0,
        )
    for _ in range(4):
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.RETURNED,
            days_ago=5,
            fulfilled_hours_after=1.0,
        )
    # Also need enough shipments so delivery metric isn't excluded
    for _ in range(5):
        await _seed_shipment(
            test_session,
            store_id=store_id,
            order_id=uuid4(),
            status="delivered",
            days_ago=5,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )

    assert result["metrics"]["return_rate"] == 40.0
    # 40% returns is below the (0.20, 0) threshold → sub-score 0
    assert result["sub_scores"]["low_return"] == 0
    # Returns no longer "missing"
    assert "low_return" not in result["insufficient_metrics"]


@pytest.mark.asyncio
async def test_approved_refunds_count_toward_return_rate(test_session):
    """When merchants use the Refund flow, APPROVED/PROCESSING/COMPLETED
    refunds should also drive return_rate."""
    store_id = uuid4()
    customer_id = uuid4()

    # 10 delivered orders, 3 with approved refunds
    orders = []
    for _ in range(10):
        o = await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED,
            days_ago=5,
            fulfilled_hours_after=1.0,
        )
        orders.append(o)
    for i in range(3):
        await _seed_refund(
            test_session,
            store_id=store_id,
            order_id=orders[i].id,
            status=RefundStatus.APPROVED,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )
    assert result["metrics"]["return_rate"] == 30.0


@pytest.mark.asyncio
async def test_partial_data_only_excludes_unmeasurable_metrics(test_session):
    """A store with only orders (no shipments) should produce a score
    based on order metrics, with delivery + cod flagged insufficient."""
    store_id = uuid4()
    customer_id = uuid4()

    # 10 delivered orders, no shipments, no refunds
    for _ in range(10):
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED,
            days_ago=5,
            fulfilled_hours_after=1.0,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )

    assert result["insufficient_data"] is False
    assert result["score"] is not None
    # Delivery and COD have no data
    assert "delivery_success" in result["insufficient_metrics"]
    assert "cod_acceptance" in result["insufficient_metrics"]
    # Order completion + low_return + response time have data
    assert "order_completion" not in result["insufficient_metrics"]
    assert "low_return" not in result["insufficient_metrics"]
    assert "response_time" not in result["insufficient_metrics"]
    # 100% completion + 0% return + fast response → high score
    assert result["score"] >= 85


@pytest.mark.asyncio
async def test_recommendations_language_switches(test_session):
    """Calling with lang='en' vs lang='ar' produces recs in the
    requested language even though the rest of the payload is the same."""
    store_id = uuid4()
    customer_id = uuid4()

    # Seed a clearly bad store
    for _ in range(10):
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.CANCELLED,
            days_ago=5,
            fulfilled_hours_after=72.0,  # slow
        )
    for _ in range(10):
        await _seed_shipment(
            test_session,
            store_id=store_id,
            order_id=uuid4(),
            status="failed",
            days_ago=5,
        )
    await test_session.commit()

    en = await calculate_store_health_score(test_session, store_id, days=30, lang="en")
    ar = await calculate_store_health_score(test_session, store_id, days=30, lang="ar")

    assert en["score"] == ar["score"]
    assert en["sub_scores"] == ar["sub_scores"]
    assert len(en["recommendations"]) > 0
    assert len(ar["recommendations"]) > 0
    # English recs are ASCII; Arabic recs have Arabic letters
    assert any(ord(c) < 128 for c in en["recommendations"][0])
    assert any("؀" <= ch <= "ۿ" for ch in ar["recommendations"][0])


# ── Cache freshness helper (endpoint-level) ─────────────────────────────


def test_cache_fresh_helper_recognises_recent_timestamp():
    from src.api.v1.routes.stores.analytics import _cache_is_fresh

    cached = {"calculated_at": _now().isoformat()}
    assert _cache_is_fresh(cached) is True


def test_cache_fresh_helper_rejects_stale_timestamp():
    from src.api.v1.routes.stores.analytics import (
        HEALTH_SCORE_CACHE_TTL_HOURS,
        _cache_is_fresh,
    )

    old = _now() - timedelta(hours=HEALTH_SCORE_CACHE_TTL_HOURS + 1)
    cached = {"calculated_at": old.isoformat()}
    assert _cache_is_fresh(cached) is False


def test_cache_fresh_helper_handles_missing_or_bad_timestamp():
    from src.api.v1.routes.stores.analytics import _cache_is_fresh

    assert _cache_is_fresh({}) is False
    assert _cache_is_fresh({"calculated_at": None}) is False
    assert _cache_is_fresh({"calculated_at": "not-a-date"}) is False


def test_cache_fresh_helper_handles_naive_timestamp():
    """Old caches written before tz-aware timestamps were enforced should
    still be parseable — assume UTC instead of crashing."""
    from src.api.v1.routes.stores.analytics import _cache_is_fresh

    naive = datetime.utcnow().isoformat()  # no tz info
    assert _cache_is_fresh({"calculated_at": naive}) is True


@pytest.mark.asyncio
async def test_old_orders_outside_window_ignored(test_session):
    """Orders older than the analysis window should not affect the score."""
    store_id = uuid4()
    customer_id = uuid4()

    # Old orders — outside 30d window
    for _ in range(10):
        await _seed_order(
            test_session,
            store_id=store_id,
            customer_id=customer_id,
            status=OrderStatus.DELIVERED,
            days_ago=120,
            fulfilled_hours_after=1.0,
        )
    await test_session.commit()

    result = await calculate_store_health_score(
        test_session, store_id, days=30, lang="en"
    )
    # Old orders were filtered out → no data → insufficient
    assert result["insufficient_data"] is True
    assert result["orders_analyzed"] == 0
