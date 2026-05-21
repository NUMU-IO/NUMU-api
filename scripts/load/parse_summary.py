"""Parse the k6 ``--summary-export`` JSON into:

1. A Slack-friendly Markdown summary (the ``--output`` file).
2. A regression report comparing to a baseline summary file.

Exit codes:

* ``0`` — every threshold passes AND no regression beyond the configured
  percentage. The weekly workflow uses this as the gate for promoting
  the current summary to the new green baseline.
* ``1`` — at least one threshold breached, OR (with ``--fail-on-regression``)
  at least one metric regressed by more than ``--regression-threshold-pct``.

The script never raises on missing inputs / metrics — it degrades to
``n/a`` cells so the Slack post is still useful when k6 produced a
truncated summary.

Assumed k6 summary shape (defensive ``dict.get`` everywhere):

.. code-block:: json

    {
      "metrics": {
        "http_req_duration": {"p(95)": 412.7, "p(99)": 802.1, ...},
        "http_req_failed":   {"rate": 0.004, ...},
        "lookup_latency_ms": {"p(95)": 87.3, ...}
      }
    }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# (metric name, k6 stat key, human-readable threshold expression).
# The threshold expression mirrors the assertions baked into
# scripts/load/storefront_load.js. Keep these in sync if the k6
# script's options.thresholds ever drifts.
THRESHOLD_METRICS: list[tuple[str, str, str]] = [
    ("http_req_failed", "rate", "< 0.01"),
    ("http_req_duration", "p(95)", "< 500"),
    ("home_latency_ms", "p(95)", "< 400"),
    ("pdp_latency_ms", "p(95)", "< 300"),
    ("plp_latency_ms", "p(95)", "< 400"),
    ("cart_latency_ms", "p(95)", "< 200"),
    ("lookup_latency_ms", "p(95)", "< 200"),
]


def get_value(summary: dict | None, metric: str, stat: str) -> float | None:
    """Pull a single metric/stat out of the summary, or None on miss."""
    if not summary:
        return None
    m = summary.get("metrics", {}).get(metric)
    if not isinstance(m, dict):
        return None
    val = m.get(stat)
    if isinstance(val, int | float):
        return float(val)
    return None


def _evaluate_threshold(value: float, expr: str) -> bool:
    """``expr`` is one of: ``< N``, ``> N``. Anything else → True (skip)."""
    parts = expr.strip().split()
    if len(parts) != 2:
        return True
    op, num_str = parts
    try:
        num = float(num_str)
    except ValueError:
        return True
    if op == "<":
        return value < num
    if op == ">":
        return value > num
    if op == "<=":
        return value <= num
    if op == ">=":
        return value >= num
    return True


def _load_optional_json(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def build_report(
    cur: dict,
    base: dict | None,
    regression_threshold_pct: float,
) -> tuple[str, bool, bool]:
    """Build the Markdown report. Returns (text, any_fail, any_regress)."""
    lines: list[str] = ["# Load test summary", ""]
    lines.append("| Metric | Current | Threshold | Pass? | Δ vs baseline |")
    lines.append("| --- | --- | --- | --- | --- |")

    any_fail = False
    any_regress = False

    for metric, stat, threshold in THRESHOLD_METRICS:
        cur_val = get_value(cur, metric, stat)
        base_val = get_value(base, metric, stat)

        if cur_val is None:
            cell = "n/a"
            ok_cell = "—"
            delta_cell = "—"
        else:
            cell = f"{cur_val:.2f}"
            ok_bool = _evaluate_threshold(cur_val, threshold)
            ok_cell = "✅" if ok_bool else "❌"
            if not ok_bool:
                any_fail = True

            if base_val is None or base_val == 0:
                delta_cell = "no baseline" if base_val is None else "—"
            else:
                delta_pct = (cur_val - base_val) / base_val * 100
                delta_cell = f"{delta_pct:+.1f}%"
                if delta_pct > regression_threshold_pct:
                    delta_cell += " 🚨 REGRESSION"
                    any_regress = True

        lines.append(
            f"| {metric} {stat} | {cell} | {threshold} | {ok_cell} | {delta_cell} |"
        )

    lines.append("")
    lines.append(
        "**Thresholds**: "
        + (
            "❌ at least one threshold failed"
            if any_fail
            else "✅ all thresholds passed"
        )
    )
    lines.append(
        "**Regression**: "
        + (
            f"🚨 at least one regression of >{regression_threshold_pct:g}%"
            if any_regress
            else "✅ no regressions"
        )
    )

    return "\n".join(lines), any_fail, any_regress


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--summary", required=True, help="k6 --summary-export JSON")
    ap.add_argument(
        "--baseline",
        required=False,
        help="Prior green summary; missing/unreadable → no Δ column",
    )
    ap.add_argument("--output", required=True, help="Markdown output path")
    ap.add_argument(
        "--regression-threshold-pct",
        type=float,
        default=20.0,
        help="Δ%% above which a metric is flagged as regression (default: 20)",
    )
    ap.add_argument(
        "--fail-on-regression",
        action="store_true",
        help="Exit 1 on regression even if no threshold breached",
    )
    args = ap.parse_args(argv)

    cur = _load_optional_json(args.summary)
    if cur is None:
        # Pre-emptive failure — the summary file is the one thing we
        # genuinely need. Write a minimal report so the Slack post
        # still surfaces the issue.
        msg = (
            "# Load test summary\n\n"
            f"❌ Could not read summary file at `{args.summary}`."
        )
        Path(args.output).write_text(msg, encoding="utf-8")
        print(msg)
        return 1

    base = _load_optional_json(args.baseline)

    report, any_fail, any_regress = build_report(
        cur=cur, base=base, regression_threshold_pct=args.regression_threshold_pct
    )

    Path(args.output).write_text(report, encoding="utf-8")
    print(report)

    if any_fail:
        return 1
    if args.fail_on_regression and any_regress:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
