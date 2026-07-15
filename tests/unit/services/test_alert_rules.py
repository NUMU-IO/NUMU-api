"""Unit tests for the smart-alert evaluation (AI-2, pure part)."""

from datetime import date, timedelta
from types import SimpleNamespace

from src.application.services.alert_service import evaluate_alerts, render_alert

TODAY = date(2026, 7, 15)  # Wednesday


def _fmt(cents: int) -> str:
    return f"{cents / 100:,.2f} EGP"


def _rollup(d: date, revenue=0, orders=0, refunds=0):
    return SimpleNamespace(
        rollup_date=d,
        total_revenue_cents=revenue,
        total_orders=orders,
        refund_count=refunds,
    )


def _ctx(**over):
    ctx = {
        "fmt": _fmt,
        "today_local": TODAY,
        "daily_by_date": {},
        "orders_today": 0,
        "abandoned_24h": 0,
        "cod_7d": {"total": 0, "rejected": 0, "rejected_amount": 0},
        "cod_90d": {"total": 0, "rejected": 0, "rejected_amount": 0},
        "products_7d": [],
        "stock": {},
    }
    ctx.update(over)
    return ctx


def _same_weekday_history(day_offset: int, orders: int, revenue: int) -> dict:
    """Four prior same-weekday rollups relative to TODAY-day_offset."""
    base = TODAY - timedelta(days=day_offset)
    return {
        base - timedelta(days=7 * w): _rollup(
            base - timedelta(days=7 * w), revenue=revenue, orders=orders
        )
        for w in range(1, 5)
    }


class TestSpike:
    def test_fires_on_strong_day(self):
        ctx = _ctx(
            daily_by_date=_same_weekday_history(0, orders=4, revenue=400_00),
            orders_today=15,
        )
        fired = {f["rule_id"]: f for f in evaluate_alerts(ctx)}
        assert "AL-SPIKE" in fired
        assert fired["AL-SPIKE"]["severity"] == "opportunity"

    def test_quiet_on_normal_day(self):
        ctx = _ctx(
            daily_by_date=_same_weekday_history(0, orders=4, revenue=400_00),
            orders_today=5,
        )
        assert "AL-SPIKE" not in {f["rule_id"] for f in evaluate_alerts(ctx)}


class TestRevDrop:
    def test_fires_on_collapsed_yesterday(self):
        history = _same_weekday_history(1, orders=10, revenue=1_000_00)
        # Add slight variance so std > 0.
        items = list(history.items())
        items[0][1].total_revenue_cents = 1_100_00
        yesterday = TODAY - timedelta(days=1)
        history[yesterday] = _rollup(yesterday, revenue=100_00, orders=1)
        fired = {f["rule_id"]: f for f in evaluate_alerts(_ctx(daily_by_date=history))}
        assert "AL-REV-DROP" in fired
        assert fired["AL-REV-DROP"]["metrics"]["weekday"] == "Tuesday"
        assert fired["AL-REV-DROP"]["impact_cents"] > 0


class TestCodSpike:
    def test_fires_on_rate_jump(self):
        ctx = _ctx(
            cod_7d={"total": 12, "rejected": 4, "rejected_amount": 300_00},  # 33%
            cod_90d={"total": 100, "rejected": 10, "rejected_amount": 0},  # 10%
        )
        fired = {f["rule_id"]: f for f in evaluate_alerts(ctx)}
        assert "AL-COD-SPIKE" in fired
        assert fired["AL-COD-SPIKE"]["metrics"]["rate_pct"] == 33.3

    def test_min_volumes(self):
        ctx = _ctx(
            cod_7d={"total": 5, "rejected": 4, "rejected_amount": 0},
            cod_90d={"total": 100, "rejected": 10, "rejected_amount": 0},
        )
        assert "AL-COD-SPIKE" not in {f["rule_id"] for f in evaluate_alerts(ctx)}


class TestRefundSpike:
    def test_fires_on_doubled_weekly(self):
        history = {}
        for i in range(1, 91):
            d = TODAY - timedelta(days=i)
            history[d] = _rollup(d, refunds=1 if i % 7 == 0 else 0)  # ~13 total
        for i in range(1, 7):  # this week: 6 more refunds
            history[TODAY - timedelta(days=i)].refund_count = 1
        fired = {f["rule_id"] for f in evaluate_alerts(_ctx(daily_by_date=history))}
        assert "AL-REFUND-SPIKE" in fired


class TestAbandonSurge:
    def test_fires_when_abandons_dwarf_orders(self):
        ctx = _ctx(abandoned_24h=9, orders_today=2)
        assert "AL-ABANDON-SURGE" in {f["rule_id"] for f in evaluate_alerts(ctx)}

    def test_quiet_when_proportional(self):
        ctx = _ctx(abandoned_24h=6, orders_today=10)
        assert "AL-ABANDON-SURGE" not in {f["rule_id"] for f in evaluate_alerts(ctx)}


class TestStockout:
    def test_fires_under_five_days_cover(self):
        ctx = _ctx(
            products_7d=[
                {"product_id": "a", "units_sold": 14, "revenue_cents": 140_00}
            ],
            stock={
                "a": {
                    "product_id": "a",
                    "name": "Cap",
                    "quantity": 6,
                    "unit_value_cents": 10_00,
                    "value_is_cost": True,
                    "created_at": None,
                }
            },
        )
        fired = {f["rule_id"]: f for f in evaluate_alerts(ctx)}
        assert "AL-STOCKOUT" in fired  # velocity 2/day → 3d cover
        assert fired["AL-STOCKOUT"]["metrics"]["product_name"] == "Cap"


class TestRender:
    def test_bilingual(self):
        en = render_alert("AL-STOCKOUT", {"product_name": "Cap", "days_cover": 3}, "en")
        ar = render_alert("AL-STOCKOUT", {"product_name": "Cap", "days_cover": 3}, "ar")
        assert "URGENT" in en["title"] and "Cap" in en["title"]
        assert "عاجل" in ar["title"]

    def test_missing_placeholder_safe(self):
        out = render_alert("AL-SPIKE", {}, "en")
        assert "{orders}" in out["title"]
