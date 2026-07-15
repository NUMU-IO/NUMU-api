"""Benchmark engine math (AI-7) — privacy-safe peer percentiles.

Pure functions the platform-level benchmark task and the read endpoint
share. Hard privacy rules live here so they cannot drift:

1. **Winsorize at P5/P95** before computing percentiles — a single
   whale or a test store can't distort the published numbers.
2. **Percentiles only** (P25/P50/P75) — never means, never store rows.
3. **k-anonymity floor**: a cell is publishable only with
   ``n >= K_ANONYMITY`` stores; readers fall back up the segment
   hierarchy (industry×size → size → all) until a cell clears it.
4. ``n_stores`` is shown in buckets ("10+", "50+"), never exactly.
"""

from __future__ import annotations

K_ANONYMITY = 10

# Monthly-order tiers (spec §7.6): the size axis of every segment.
SIZE_TIERS = ((1, 50, "1-50"), (51, 300, "51-300"), (301, 1000, "301-1000"))

BENCHMARK_METRICS = (
    "aov_cents",
    "conversion_rate_pct",
    "repeat_rate_pct",
    "cod_rejection_rate_pct",
    "refund_rate_pct",
)


def size_tier(orders_30d: int) -> str:
    """Monthly-order tier label for the size segmentation axis."""
    for lo, hi, label in SIZE_TIERS:
        if lo <= orders_30d <= hi:
            return label
    return "1000+" if orders_30d > 1000 else "0"


def segment_keys(industry: str | None, tier: str) -> list[str]:
    """Most-specific-first hierarchy for one store."""
    keys = []
    if industry:
        keys.append(f"industry:{industry}|size:{tier}")
    keys.append(f"size:{tier}")
    keys.append("all")
    return keys


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolated percentile on a pre-sorted list."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 >= len(sorted_values):
        return sorted_values[-1]
    return sorted_values[lo] * (1 - frac) + sorted_values[lo + 1] * frac


def winsorize(values: list[float]) -> list[float]:
    """Clamp everything outside [P5, P95] to those bounds."""
    if len(values) < 3:
        return list(values)
    s = sorted(values)
    lo, hi = _percentile(s, 0.05), _percentile(s, 0.95)
    return [min(max(v, lo), hi) for v in values]


def percentile_cell(values: list[float]) -> dict | None:
    """(p25, p50, p75, n) for one metric cell, or None when empty."""
    if not values:
        return None
    w = sorted(winsorize(values))
    return {
        "p25": round(_percentile(w, 0.25), 2),
        "p50": round(_percentile(w, 0.50), 2),
        "p75": round(_percentile(w, 0.75), 2),
        "n_stores": len(values),
    }


def build_cells(store_rows: list[dict]) -> dict[tuple[str, str], dict]:
    """All (segment_key, metric) cells from per-store metric rows.

    ``store_rows``: [{industry, size_tier, metrics: {metric: value}}].
    Every store contributes to each level of its hierarchy so the
    fallback cells are pre-computed, not derived at read time.
    Cells below the k-anonymity floor are still WRITTEN (they mature
    as the platform grows) — the read path filters them.
    """
    buckets: dict[tuple[str, str], list[float]] = {}
    for row in store_rows:
        for key in segment_keys(row.get("industry"), row["size_tier"]):
            for metric, value in row["metrics"].items():
                if value is None:
                    continue
                buckets.setdefault((key, metric), []).append(float(value))

    cells = {}
    for cell_key, values in buckets.items():
        cell = percentile_cell(values)
        if cell:
            cells[cell_key] = cell
    return cells


def n_bucket(n: int) -> str:
    """Coarse count shown to merchants — exact n leaks information."""
    if n >= 100:
        return "100+"
    if n >= 50:
        return "50+"
    return "10+"


def resolve_cell(
    cells_by_key: dict[tuple[str, str], dict],
    industry: str | None,
    tier: str,
    metric: str,
) -> dict | None:
    """Most specific publishable cell for a store, or None.

    Walks the hierarchy until a cell clears the k-anonymity floor.
    """
    for key in segment_keys(industry, tier):
        cell = cells_by_key.get((key, metric))
        if cell and cell["n_stores"] >= K_ANONYMITY:
            return {**cell, "segment_key": key}
    return None
