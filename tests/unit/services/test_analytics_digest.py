"""Unit tests for the weekly analytics digest builder (pure, bilingual)."""

from src.application.services.analytics_digest_service import build_weekly_digest


def _fmt(cents: int) -> str:
    return f"{cents / 100:,.2f} EGP"


class TestHeadline:
    def test_growth_headline(self):
        d = build_weekly_digest(
            {"revenue_cents": 150000, "prev_revenue_cents": 100000, "orders": 10},
            "en",
            _fmt,
        )
        assert "up 50%" in d["headline"]
        assert "1,500.00 EGP" in d["headline"]
        assert d["has_sales"] is True

    def test_decline_headline(self):
        d = build_weekly_digest(
            {"revenue_cents": 80000, "prev_revenue_cents": 100000, "orders": 5},
            "en",
            _fmt,
        )
        assert "down 20%" in d["headline"]

    def test_flat_headline_under_3pct(self):
        d = build_weekly_digest(
            {"revenue_cents": 101000, "prev_revenue_cents": 100000, "orders": 5},
            "en",
            _fmt,
        )
        assert "Steady week" in d["headline"]

    def test_first_week_headline(self):
        d = build_weekly_digest(
            {"revenue_cents": 50000, "prev_revenue_cents": 0, "orders": 3}, "en", _fmt
        )
        assert "first week" in d["headline"].lower()

    def test_no_sales(self):
        d = build_weekly_digest({"revenue_cents": 0, "orders": 0}, "en", _fmt)
        assert d["has_sales"] is False
        assert "No sales" in d["headline"]
        assert d["highlights"] == []


class TestHighlights:
    def test_all_highlights(self):
        d = build_weekly_digest(
            {
                "revenue_cents": 200000,
                "prev_revenue_cents": 150000,
                "orders": 12,
                "prev_orders": 9,
                "new_customers": 5,
                "aov_cents": 16666,
                "top_product_name": "Solo Leveling Tee",
                "top_product_units": 8,
            },
            "en",
            _fmt,
        )
        joined = " | ".join(d["highlights"])
        assert "12 orders (+3 vs last week)" in joined
        assert "5 new customers" in joined
        assert "Solo Leveling Tee (8 sold)" in joined
        assert "166.66 EGP" in joined

    def test_order_delta_same(self):
        d = build_weekly_digest(
            {
                "revenue_cents": 100,
                "prev_revenue_cents": 100,
                "orders": 4,
                "prev_orders": 4,
            },
            "en",
            _fmt,
        )
        assert any("same" in h for h in d["highlights"])


class TestArabic:
    def test_arabic_headline_and_bullets(self):
        d = build_weekly_digest(
            {
                "revenue_cents": 150000,
                "prev_revenue_cents": 100000,
                "orders": 10,
                "new_customers": 3,
            },
            "ar",
            _fmt,
        )
        assert "زادت" in d["headline"]  # "increased"
        assert any("عميل جديد" in h for h in d["highlights"])  # "new customer"

    def test_unknown_lang_falls_back_to_en(self):
        d = build_weekly_digest(
            {"revenue_cents": 1000, "prev_revenue_cents": 0, "orders": 1}, "fr", _fmt
        )
        assert "first week" in d["headline"].lower()
