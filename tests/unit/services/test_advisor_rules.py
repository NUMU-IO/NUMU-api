"""Unit tests for the advisor rule registry — the deterministic brain of
the AI Commerce Intelligence layer. Rules are pure functions over a
context dict, so every trigger/no-trigger boundary is testable without a
database."""

from src.application.services.advisor_rules import (
    RULES,
    RULES_BY_ID,
    render_signal,
    run_rules,
)


def _fmt(cents: int) -> str:
    return f"{cents / 100:,.2f} EGP"


def _week(revenue=0, orders=0, gross=None, discounts=0):
    return {
        "revenue_cents": revenue,
        "orders": orders,
        "gross_cents": gross if gross is not None else revenue,
        "discounts_cents": discounts,
    }


def _base_ctx(**over):
    """A quiet store: nothing should fire."""
    ctx = {
        "fmt": _fmt,
        "weekly": [_week(100_00, 2)] * 8,
        "products_28d": [],
        "products_7d": {},
        "products_prev_7d": {},
        "product_names": {},
        "stock": [],
        "dead_stock_90_value_cents": 0,
        "cod": {"total": 0, "rejected": 0, "rejected_amount": 0},
        "cod_by_gov": [],
        "funnel_7d": {},
        "abandoned_reachable_7d": 0,
        "abandoned_value_7d_cents": 0,
        "repeat": {"customers": 0, "repeat_customers": 0},
        "orders_30d": 0,
        "coupon_orders_30d": 0,
        "discounts_30d_cents": 0,
        "aov_cents": 0,
    }
    ctx.update(over)
    return ctx


class TestQuietStore:
    def test_no_rules_fire_on_quiet_store(self):
        assert run_rules(_base_ctx()) == []


class TestRevenueRules:
    def test_rv1_fires_on_collapse(self):
        weekly = [
            _week(1_000_00, 5),  # current partial week (ignored)
            _week(50_00, 1),  # last full week — collapsed
            _week(1_000_00, 10),
            _week(1_050_00, 11),
            _week(950_00, 9),
            _week(1_000_00, 10),
        ]
        fired = {f["rule_id"]: f for f in run_rules(_base_ctx(weekly=weekly))}
        assert "RV-1" in fired
        assert fired["RV-1"]["severity"] == "critical"
        assert fired["RV-1"]["impact_cents"] > 0

    def test_rv1_quiet_when_stable(self):
        weekly = [_week(1_000_00, 10)] * 8
        fired = {f["rule_id"] for f in run_rules(_base_ctx(weekly=weekly))}
        assert "RV-1" not in fired

    def test_rv2_fires_on_three_week_aov_slide(self):
        weekly = [
            _week(0, 0),
            _week(400_00, 8),  # AOV 50
            _week(480_00, 8),  # 60
            _week(560_00, 8),  # 70
            _week(640_00, 8),  # 80 — strictly declining toward now
        ]
        fired = {f["rule_id"] for f in run_rules(_base_ctx(weekly=weekly))}
        assert "RV-2" in fired

    def test_rv3_concentration_with_slowdown(self):
        ctx = _base_ctx(
            products_28d=[
                {"product_id": "a", "units_sold": 50, "revenue_cents": 900_00},
                {"product_id": "b", "units_sold": 10, "revenue_cents": 100_00},
            ],
            products_7d={"a": {"units_sold": 3, "revenue_cents": 50_00}},
            products_prev_7d={"a": {"units_sold": 10, "revenue_cents": 200_00}},
            product_names={"a": "Hero Tee"},
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "RV-3" in fired
        assert fired["RV-3"]["metrics"]["product_name"] == "Hero Tee"

    def test_rv4_discount_creep(self):
        weekly = [
            _week(0, 0),
            _week(900_00, 9, gross=1_000_00, discounts=200_00),  # 20%
            _week(950_00, 9, gross=1_000_00, discounts=50_00),  # 5%
        ] + [_week(1_000_00, 10)] * 4
        fired = {f["rule_id"] for f in run_rules(_base_ctx(weekly=weekly))}
        assert "RV-4" in fired


class TestInventoryRules:
    def test_iv1_top_seller_low_cover(self):
        ctx = _base_ctx(
            products_28d=[
                {"product_id": "a", "units_sold": 56, "revenue_cents": 560_00}
            ],
            stock=[
                {
                    "product_id": "a",
                    "name": "Socks",
                    "quantity": 10,
                    "unit_value_cents": 10_00,
                    "value_is_cost": True,
                    "created_at": None,
                }
            ],
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "IV-1" in fired  # velocity 2/day, cover 5d
        assert fired["IV-1"]["metrics"]["suggested_qty"] >= 5

    def test_iv3_oos_while_selling(self):
        ctx = _base_ctx(
            products_28d=[
                {"product_id": "a", "units_sold": 40, "revenue_cents": 400_00}
            ],
            stock=[
                {
                    "product_id": "a",
                    "name": "Socks",
                    "quantity": 0,
                    "unit_value_cents": 10_00,
                    "value_is_cost": True,
                    "created_at": None,
                }
            ],
        )
        fired = {f["rule_id"] for f in run_rules(ctx)}
        assert "IV-3" in fired
        assert "IV-1" not in fired  # zero stock is IV-3's job, not IV-1's

    def test_iv2_dead_stock_threshold(self):
        assert "IV-2" in {
            f["rule_id"]
            for f in run_rules(_base_ctx(dead_stock_90_value_cents=1_000_00))
        }
        assert "IV-2" not in {
            f["rule_id"] for f in run_rules(_base_ctx(dead_stock_90_value_cents=999_99))
        }

    def test_lp1_dead_listing(self):
        ctx = _base_ctx(
            stock=[
                {
                    "product_id": "z",
                    "name": "Dusty",
                    "quantity": 30,
                    "unit_value_cents": 50_00,
                    "value_is_cost": True,
                    "created_at": None,
                }
            ],
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "LP-1" in fired
        assert fired["LP-1"]["metrics"]["example"] == "Dusty"

    def test_fg1_surge_low_cover(self):
        ctx = _base_ctx(
            products_7d={"a": {"units_sold": 21, "revenue_cents": 210_00}},
            products_prev_7d={"a": {"units_sold": 10, "revenue_cents": 100_00}},
            stock=[
                {
                    "product_id": "a",
                    "name": "Viral Cap",
                    "quantity": 20,
                    "unit_value_cents": 10_00,
                    "value_is_cost": True,
                    "created_at": None,
                }
            ],
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "FG-1" in fired  # +110% growth, cover ≈ 6.7d


class TestCodRules:
    def test_cd1_rejection_rate(self):
        ctx = _base_ctx(cod={"total": 20, "rejected": 5, "rejected_amount": 500_00})
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "CD-1" in fired
        assert fired["CD-1"]["metrics"]["rate_pct"] == 25.0

    def test_cd1_needs_min_volume(self):
        ctx = _base_ctx(cod={"total": 5, "rejected": 3, "rejected_amount": 0})
        assert "CD-1" not in {f["rule_id"] for f in run_rules(ctx)}

    def test_cd2_governorate_hotspot(self):
        ctx = _base_ctx(
            cod={"total": 40, "rejected": 4, "rejected_amount": 0},  # avg 10%
            cod_by_gov=[
                {"location": "sohag", "total": 12, "rejected": 5, "rate": 41.7},
                {"location": "cairo", "total": 28, "rejected": 1, "rate": 3.6},
            ],
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "CD-2" in fired
        assert fired["CD-2"]["metrics"]["governorate"] == "sohag"


class TestCartAndRetention:
    def test_ca1_low_cart_to_checkout(self):
        ctx = _base_ctx(
            funnel_7d={"add_to_cart": 50, "checkout_started": 10},
            aov_cents=100_00,
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "CA-1" in fired
        assert fired["CA-1"]["impact_cents"] > 0

    def test_ca4_reachable_abandoned(self):
        ctx = _base_ctx(abandoned_reachable_7d=12, abandoned_value_7d_cents=3_000_00)
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "CA-4" in fired
        assert fired["CA-4"]["kind"] == "opportunity"
        assert fired["CA-4"]["impact_cents"] == 30_000  # 10% of value

    def test_rc1_low_repeat(self):
        ctx = _base_ctx(
            repeat={"customers": 100, "repeat_customers": 5}, aov_cents=200_00
        )
        assert "RC-1" in {f["rule_id"] for f in run_rules(ctx)}

    def test_pr3_coupon_dependence(self):
        ctx = _base_ctx(orders_30d=20, coupon_orders_30d=15)
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "PR-3" in fired
        assert fired["PR-3"]["metrics"]["share_pct"] == 75


class TestRendering:
    def test_render_interpolates_both_langs(self):
        for lang, needle in (("en", "runs out"), ("ar", "هيخلص")):
            out = render_signal(
                "IV-1",
                {"product_name": "Socks", "days_cover": 3.5, "suggested_qty": 42},
                lang,
            )
            assert "Socks" in out["title"]
            assert needle in out["title"]
            assert "42" in out["action"]

    def test_render_survives_missing_placeholder(self):
        out = render_signal("IV-1", {}, "en")
        assert "{product_name}" in out["title"]  # rendered as-is, no crash

    def test_registry_has_18_rules_with_bilingual_copy(self):
        assert len(RULES) == 18
        for rule in RULES:
            assert set(rule.title) >= {"en", "ar"}
            assert set(rule.action) >= {"en", "ar"}
            assert rule.rule_id in RULES_BY_ID

    def test_crashing_rule_is_skipped(self):
        # weekly=None breaks every weekly rule's indexing — run_rules
        # must swallow those and still evaluate the rest.
        ctx = _base_ctx(
            weekly=None, abandoned_reachable_7d=12, abandoned_value_7d_cents=1_000_00
        )
        fired = {f["rule_id"] for f in run_rules(ctx)}
        assert "CA-4" in fired


class TestOpportunities:
    def test_op_bundle_lift(self):
        ctx = _base_ctx(
            basket={
                "total_orders": 40,
                "pairs": [{"a_id": "a", "b_id": "b", "pair_orders": 6}],
                "product_orders": {"a": 10, "b": 8},
            },
            product_names={"a": "Tee", "b": "Cap"},
            aov_cents=100_00,
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "OP-BUNDLE" in fired  # lift = 6*40/(10*8) = 3.0
        assert fired["OP-BUNDLE"]["metrics"]["product_a"] == "Tee"

    def test_op_bundle_needs_lift_3(self):
        ctx = _base_ctx(
            basket={
                "total_orders": 40,
                "pairs": [{"a_id": "a", "b_id": "b", "pair_orders": 5}],
                "product_orders": {"a": 10, "b": 8},
            },
        )
        assert "OP-BUNDLE" not in {f["rule_id"] for f in run_rules(ctx)}

    def test_op_ads_ready(self):
        ctx = _base_ctx(
            products_28d=[
                {"product_id": "a", "units_sold": 28, "revenue_cents": 2_800_00}
            ],
            stock=[
                {
                    "product_id": "a",
                    "name": "Hero",
                    "quantity": 40,
                    "unit_value_cents": 40_00,
                    "value_is_cost": True,
                    "price_cents": 100_00,
                    "cost_cents": 40_00,
                    "created_at": None,
                }
            ],
        )
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "OP-ADS-READY" in fired  # 60% margin, 40d cover
        assert fired["OP-ADS-READY"]["metrics"]["margin_pct"] == 60

    def test_op_ads_ready_skips_thin_margin(self):
        ctx = _base_ctx(
            products_28d=[
                {"product_id": "a", "units_sold": 28, "revenue_cents": 2_800_00}
            ],
            stock=[
                {
                    "product_id": "a",
                    "name": "Hero",
                    "quantity": 40,
                    "unit_value_cents": 80_00,
                    "value_is_cost": True,
                    "price_cents": 100_00,
                    "cost_cents": 80_00,
                    "created_at": None,
                }
            ],
        )
        assert "OP-ADS-READY" not in {f["rule_id"] for f in run_rules(ctx)}

    def test_op_due_customers(self):
        ctx = _base_ctx(due_customers=5, aov_cents=200_00)
        fired = {f["rule_id"]: f for f in run_rules(ctx)}
        assert "OP-DUE" in fired
        assert fired["OP-DUE"]["impact_cents"] == 20_000  # 5 * 0.2 * 20000
