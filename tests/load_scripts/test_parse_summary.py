"""Unit tests for ``scripts/load/parse_summary.py``.

Covers the four paths the plan calls out:

* All-green: every threshold passes, no baseline / no regression → exit 0
* Threshold-fail: at least one metric breaches → exit 1
* Regression at the 20% boundary: ``+19.9%`` passes, ``+20.1%`` regresses
* Missing baseline: ``--baseline`` points at a non-existent file → no
  Δ column but no crash, exit code mirrors threshold pass/fail

Plus a couple of edge cases (missing metrics → ``n/a``, malformed
JSON → exit 1).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture
def parse_summary_main():
    """Import the parser's ``main`` function on demand so the test
    file can run without pre-installing the project package."""
    import importlib.util
    import sys

    repo = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location(
        "parse_summary_under_test", repo / "scripts" / "load" / "parse_summary.py"
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["parse_summary_under_test"] = mod
    spec.loader.exec_module(mod)
    return mod.main


def _green_summary() -> dict[str, Any]:
    """A summary that passes every threshold cleanly."""
    return {
        "metrics": {
            "http_req_failed": {"rate": 0.001},
            "http_req_duration": {"p(95)": 250.0},
            "home_latency_ms": {"p(95)": 200.0},
            "pdp_latency_ms": {"p(95)": 150.0},
            "plp_latency_ms": {"p(95)": 200.0},
            "cart_latency_ms": {"p(95)": 100.0},
            "lookup_latency_ms": {"p(95)": 80.0},
        }
    }


def _write(tmp: Path, name: str, obj: dict[str, Any]) -> Path:
    p = tmp / name
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


# ---------------------------------------------------------------- #
# All green                                                         #
# ---------------------------------------------------------------- #


def test_all_green_exits_zero(parse_summary_main, tmp_path: Path) -> None:
    summary = _write(tmp_path, "load-summary.json", _green_summary())
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main(["--summary", str(summary), "--output", str(output)])
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "all thresholds passed" in body
    # No baseline supplied → every row should show "no baseline"
    assert body.count("no baseline") == 7


# ---------------------------------------------------------------- #
# Threshold failure                                                 #
# ---------------------------------------------------------------- #


def test_threshold_fail_exits_one(parse_summary_main, tmp_path: Path) -> None:
    bad = _green_summary()
    bad["metrics"]["cart_latency_ms"]["p(95)"] = 350.0  # > 200ms threshold
    summary = _write(tmp_path, "load-summary.json", bad)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main(["--summary", str(summary), "--output", str(output)])
    assert rc == 1
    body = output.read_text(encoding="utf-8")
    assert "❌" in body
    assert "at least one threshold failed" in body


def test_http_req_failed_threshold(parse_summary_main, tmp_path: Path) -> None:
    bad = _green_summary()
    bad["metrics"]["http_req_failed"]["rate"] = 0.05  # > 0.01
    summary = _write(tmp_path, "load-summary.json", bad)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main(["--summary", str(summary), "--output", str(output)])
    assert rc == 1


# ---------------------------------------------------------------- #
# Regression detection at the 20% boundary                          #
# ---------------------------------------------------------------- #


def test_regression_just_under_boundary_does_not_trip(
    parse_summary_main, tmp_path: Path
) -> None:
    base = _green_summary()
    cur = _green_summary()
    # pdp_latency_ms: 150 -> 179.85 = +19.9%
    cur["metrics"]["pdp_latency_ms"]["p(95)"] = 179.85

    s = _write(tmp_path, "load-summary.json", cur)
    b = _write(tmp_path, "baseline.json", base)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main([
        "--summary",
        str(s),
        "--baseline",
        str(b),
        "--output",
        str(output),
        "--fail-on-regression",
    ])
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "no regressions" in body
    assert "REGRESSION" not in body


def test_regression_just_over_boundary_trips_with_fail_flag(
    parse_summary_main, tmp_path: Path
) -> None:
    base = _green_summary()
    cur = _green_summary()
    # pdp_latency_ms: 150 -> 180.15 = +20.1% — just over boundary
    cur["metrics"]["pdp_latency_ms"]["p(95)"] = 180.15

    s = _write(tmp_path, "load-summary.json", cur)
    b = _write(tmp_path, "baseline.json", base)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main([
        "--summary",
        str(s),
        "--baseline",
        str(b),
        "--output",
        str(output),
        "--fail-on-regression",
    ])
    assert rc == 1
    body = output.read_text(encoding="utf-8")
    assert "🚨 REGRESSION" in body
    assert "at least one regression" in body


def test_regression_without_fail_flag_does_not_exit_one(
    parse_summary_main, tmp_path: Path
) -> None:
    """Without ``--fail-on-regression``, regressions are reported
    but do not change the exit code (which still tracks thresholds)."""
    base = _green_summary()
    cur = _green_summary()
    cur["metrics"]["pdp_latency_ms"]["p(95)"] = 250.0  # +66% but still < 300

    s = _write(tmp_path, "load-summary.json", cur)
    b = _write(tmp_path, "baseline.json", base)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main([
        "--summary",
        str(s),
        "--baseline",
        str(b),
        "--output",
        str(output),
        # NOTE: no --fail-on-regression here
    ])
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "🚨 REGRESSION" in body
    # But the bottom line still says "no regressions" is FALSE
    assert "at least one regression" in body


# ---------------------------------------------------------------- #
# Missing baseline path                                             #
# ---------------------------------------------------------------- #


def test_missing_baseline_file_does_not_crash(
    parse_summary_main, tmp_path: Path
) -> None:
    summary = _write(tmp_path, "load-summary.json", _green_summary())
    output = tmp_path / "slack-summary.md"
    missing = tmp_path / "does-not-exist.json"

    rc = parse_summary_main([
        "--summary",
        str(summary),
        "--baseline",
        str(missing),
        "--output",
        str(output),
        "--fail-on-regression",
    ])
    # Threshold-clean summary + missing baseline → exit 0
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "no baseline" in body


def test_corrupt_baseline_json_does_not_crash(
    parse_summary_main, tmp_path: Path
) -> None:
    summary = _write(tmp_path, "load-summary.json", _green_summary())
    output = tmp_path / "slack-summary.md"
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("not-json", encoding="utf-8")

    rc = parse_summary_main([
        "--summary",
        str(summary),
        "--baseline",
        str(corrupt),
        "--output",
        str(output),
    ])
    assert rc == 0  # Threshold-clean; corrupt baseline degrades to "no baseline"
    body = output.read_text(encoding="utf-8")
    assert "no baseline" in body


# ---------------------------------------------------------------- #
# Edge cases                                                        #
# ---------------------------------------------------------------- #


def test_missing_metric_renders_as_na(parse_summary_main, tmp_path: Path) -> None:
    partial = _green_summary()
    del partial["metrics"]["cart_latency_ms"]
    summary = _write(tmp_path, "load-summary.json", partial)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main(["--summary", str(summary), "--output", str(output)])
    assert rc == 0
    body = output.read_text(encoding="utf-8")
    assert "| cart_latency_ms p(95) | n/a |" in body


def test_corrupt_summary_exits_one(parse_summary_main, tmp_path: Path) -> None:
    summary = tmp_path / "load-summary.json"
    summary.write_text("not-json", encoding="utf-8")
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main(["--summary", str(summary), "--output", str(output)])
    assert rc == 1
    body = output.read_text(encoding="utf-8")
    assert "Could not read summary file" in body


def test_zero_baseline_value_avoids_div_by_zero(
    parse_summary_main, tmp_path: Path
) -> None:
    base = _green_summary()
    base["metrics"]["pdp_latency_ms"]["p(95)"] = 0.0  # edge case
    cur = _green_summary()
    s = _write(tmp_path, "load-summary.json", cur)
    b = _write(tmp_path, "baseline.json", base)
    output = tmp_path / "slack-summary.md"

    rc = parse_summary_main([
        "--summary",
        str(s),
        "--baseline",
        str(b),
        "--output",
        str(output),
    ])
    assert rc == 0  # No crash
