# Feature Specification: Payment-Channel Trend Computation

**Feature Branch**: `backend-019-payment-channel-trend`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

The audit found `/payments/channels` returned `trend: "stable"` for
every channel — schema default, no calculation. Dashboard arrows
were always flat. Merchants couldn't spot payment-method
degradation; the analytics card was misleading.

## Requirements

- **FR-001**: `aggregate_channels` repository method MUST accept
  explicit `period_start` / `period_end` bounds in addition to the
  legacy `days` parameter, so the route can compute current-vs-prior
  windows.
- **FR-002**: New `compute_trend(current_rate, prior_rate)` returns
  `"up"` when delta > +5pp, `"down"` when delta < -5pp,
  `"stable"` otherwise. Pure function, no DB.
- **FR-003**: Route handler computes both windows, calls
  `compute_trend` per channel using the (channel, gateway) tuple as
  the join key, and returns the real label.
- **FR-004**: `aggregate_channels` MUST also return
  `avg_processing_ms` (mean of `processing_completed_at -
  processing_started_at` for completed transactions, in ms). The
  route forwards it as an integer; channels with no completion
  timestamps return `None`.

## Success Criteria

- **SC-001**: `pytest tests/api/test_payments_channels_trend.py -v`
  green.
- **SC-002**: For a store with 60+ days of data, hitting
  `/payments/channels?days=30` returns at least one channel with
  `trend != "stable"` when the 30-day windows differ by >5pp.

## Out of scope

- Configurable trend windows (rolling 7/30/90 day comparisons).
  The current 30-vs-prior-30 model is the dashboard's first cut.
- Per-channel custom thresholds (some channels are noisier than
  others, so the 5pp threshold could be miscalibrated for low-
  volume channels — a future spec can address that).
