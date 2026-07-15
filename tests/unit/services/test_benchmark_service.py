"""Benchmark engine (AI-7) — privacy math unit tests."""

from src.application.services.benchmark_service import (
    K_ANONYMITY,
    build_cells,
    n_bucket,
    percentile_cell,
    resolve_cell,
    segment_keys,
    size_tier,
    winsorize,
)


class TestSegmentation:
    def test_size_tiers(self):
        assert size_tier(1) == "1-50"
        assert size_tier(50) == "1-50"
        assert size_tier(51) == "51-300"
        assert size_tier(1000) == "301-1000"
        assert size_tier(1001) == "1000+"
        assert size_tier(0) == "0"

    def test_hierarchy_most_specific_first(self):
        assert segment_keys("fashion", "51-300") == [
            "industry:fashion|size:51-300",
            "size:51-300",
            "all",
        ]
        assert segment_keys(None, "1-50") == ["size:1-50", "all"]


class TestWinsorize:
    def test_outlier_clamped(self):
        values = [10.0] * 19 + [10_000.0]
        w = winsorize(values)
        assert max(w) < 10_000.0  # whale clamped to P95

    def test_small_samples_untouched(self):
        assert winsorize([1.0, 2.0]) == [1.0, 2.0]


class TestCells:
    def test_percentiles_ordered(self):
        cell = percentile_cell([float(i) for i in range(1, 101)])
        assert cell["p25"] < cell["p50"] < cell["p75"]
        assert cell["n_stores"] == 100

    def test_build_cells_fans_out_hierarchy(self):
        rows = [
            {
                "industry": "fashion",
                "size_tier": "1-50",
                "metrics": {"aov_cents": 100.0},
            },
            {"industry": None, "size_tier": "1-50", "metrics": {"aov_cents": 200.0}},
        ]
        cells = build_cells(rows)
        # fashion store contributes to 3 levels; no-industry store to 2
        assert cells[("industry:fashion|size:1-50", "aov_cents")]["n_stores"] == 1
        assert cells[("size:1-50", "aov_cents")]["n_stores"] == 2
        assert cells[("all", "aov_cents")]["n_stores"] == 2

    def test_none_metrics_skipped(self):
        rows = [{"industry": None, "size_tier": "1-50", "metrics": {"aov_cents": None}}]
        assert build_cells(rows) == {}


class TestKAnonymity:
    def _cells(self, n_specific, n_all):
        return {
            ("industry:fashion|size:1-50", "aov_cents"): {
                "p25": 1,
                "p50": 2,
                "p75": 3,
                "n_stores": n_specific,
            },
            ("all", "aov_cents"): {
                "p25": 4,
                "p50": 5,
                "p75": 6,
                "n_stores": n_all,
            },
        }

    def test_falls_back_when_cell_too_small(self):
        cells = self._cells(n_specific=3, n_all=25)
        got = resolve_cell(cells, "fashion", "1-50", "aov_cents")
        assert got["segment_key"] == "all"
        assert got["p50"] == 5

    def test_uses_specific_when_big_enough(self):
        cells = self._cells(n_specific=K_ANONYMITY, n_all=25)
        got = resolve_cell(cells, "fashion", "1-50", "aov_cents")
        assert got["segment_key"] == "industry:fashion|size:1-50"

    def test_nothing_publishable_returns_none(self):
        cells = self._cells(n_specific=2, n_all=9)
        assert resolve_cell(cells, "fashion", "1-50", "aov_cents") is None

    def test_n_buckets(self):
        assert n_bucket(10) == "10+"
        assert n_bucket(49) == "10+"
        assert n_bucket(50) == "50+"
        assert n_bucket(150) == "100+"
