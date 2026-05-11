# Backend Spec 028: Merchant Benchmarking

**Feature Branch:** `backend-028-merchant-benchmarking`
**Created:** 2026-05-11
**Status:** Draft (gated — DO NOT implement until ≥30 production merchants)
**Repo:** `NUMU-api`
**Sibling spec:** `numu-payments-intelligence/specs/014-merchant-benchmarking`
**Input:** Spec 014 (P2): merchant sees their COD refusal %, recovery %, AOV vs cohort median + 75th percentile. Cohort = same vertical + similar volume. Strong retention driver, but only meaningful with cross-merchant data volume.

> **Constitutional alignment:** Principle II (peer cohorts are aggregates only — no per-merchant identification leaked); Principle III (k-anonymity ≥5 per cohort cell; opt-out toggle excludes the merchant from contributing AND receiving); Principle IV (deterministic — fixed cohort assignment by vertical + volume bucket); Principle V (tests).

## Why this feature exists + why it's gated

Per the meta-roadmap: "Powerful retention tool. Per-merchant analytics already exist; this adds anonymized peer cohorts." The catch is k-anonymity ≥ 5 — we can't show "your COD refusal is 14% vs cohort median 9%" until at least 5 merchants per cohort cell exist. Pre-launch this feature would render empty.

**Implementation gate:** at least 30 paying merchants live, distributed across at least 3 vertical cohorts with ≥ 5 merchants each. Author the spec now (this doc) so the design decisions are made; defer implementation.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — "How you compare" dashboard tile (Priority: P1)

As a merchant, when I open the dashboard, I see a tile comparing my COD refusal %, recovery %, and AOV against my cohort's median + p75.

**Acceptance Scenarios:**

1. **Given** the merchant's cohort has ≥ 5 contributing merchants, **When** the dashboard loads, **Then** the tile renders with the merchant's value + cohort median + p75 + percentile rank.
2. **Given** the cohort has < 5 contributing merchants, **When** the dashboard loads, **Then** the tile shows "Not enough peer data yet" with no leak of partial aggregates.
3. **Given** the merchant has opted out of benchmarking, **When** the dashboard loads, **Then** the tile is hidden entirely; the merchant's data also doesn't contribute to other cohorts.

### Edge Cases

- **Single-merchant cohort.** k-anonymity blocks display; merchant sees "Not enough peer data yet." Never shows aggregate-of-one.
- **Merchant changes vertical.** Re-cohorted on next nightly rollup; previous cohort contributions remain in the historical aggregates (anonymized; no merchant-id leak).
- **Outlier merchant skewing the median.** Per Principle II/III: medians + p75 are robust statistics by design; report rounds to 1 decimal place to avoid pinpointing identifiable outliers.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** New `MerchantCohortAssignmentModel` (per-store cohort assignment based on `vertical` + monthly-order-volume bucket).
- **FR-002:** New `BenchmarkSnapshotModel` (per-cohort-cell, per-period rollup of median + p75 per metric).
- **FR-003:** Nightly Celery task aggregates the snapshot. k-anonymity ≥ 5 enforced at compute time — cohort cells with < 5 contributing merchants emit no row.
- **FR-004:** Read endpoint `GET /api/v1/shopify/{store_id}/benchmarking` returns the merchant's cohort + the latest snapshot for that cohort + the merchant's own values + the percentile rank.
- **FR-005:** Opt-out flag on `ShopifyAppSettings.benchmarking_enabled` (default `true`). When `false`: merchant doesn't contribute AND doesn't receive — both directions of the consent.
- **FR-006:** Per Principle II: the snapshot row stores no merchant identifiers — only `cohort_key`, `metric_name`, `median`, `p75`, `contributor_count`. The contributor count is exposed so the UI can show "based on 12 stores like yours."

### Key Entities

```python
class MerchantCohortAssignmentModel(Base, ...):
    __tablename__ = "merchant_cohort_assignments"
    store_id: Mapped[UUID] = mapped_column(primary_key=True)
    vertical: Mapped[str] = mapped_column(String(32))  # 'fashion', 'electronics', 'beauty', 'fnb', 'home_goods', 'other'
    volume_bucket: Mapped[str] = mapped_column(String(16))  # 'micro', 'small', 'medium', 'large'
    cohort_key: Mapped[str] = mapped_column(String(48))  # f"{vertical}:{volume_bucket}"
    benchmarking_enabled: Mapped[bool] = mapped_column(default=True)


class BenchmarkSnapshotModel(Base, ...):
    __tablename__ = "benchmark_snapshots"
    cohort_key: Mapped[str] = mapped_column(primary_key=True)
    period_start: Mapped[date] = mapped_column(primary_key=True)
    metric_name: Mapped[str] = mapped_column(primary_key=True)  # 'cod_refusal_pct', 'recovery_pct', 'aov_cents'
    median_value: Mapped[float] = mapped_column()
    p75_value: Mapped[float] = mapped_column()
    contributor_count: Mapped[int] = mapped_column()  # >= 5
    last_refreshed_at: Mapped[datetime] = mapped_column()
```

## Success Criteria *(mandatory)*

- **SC-001:** k-anonymity violations: zero. Snapshot rows exist only for cohort cells with ≥ 5 contributors. Quarterly audit.
- **SC-002:** Opt-out enforcement: merchant who toggles off STOPS contributing within 24 hours (next nightly run); their dashboard tile hides immediately.
- **SC-003:** Cohort assignment stability: a merchant's cohort changes ≤ 1 time per quarter (avoid jitter).
- **SC-004:** Per Principle V: every acceptance scenario translates to a test in `tests/integration/test_benchmarking.py`.

## Assumptions

- The merchant declares `vertical` during onboarding (added to `ShopifyAppSettings.vertical` as part of this spec's implementation).
- The volume bucket is computed from `total_orders / month` of the trailing 90 days.
- "Recovery %" + "COD refusal %" come from existing `RecoveryMonthlyRollup` + Shipment data.

## Out of scope

- **Per-region cohorts** (Cairo vs Alexandria vs Hurghada). Future spec; needs city dimension.
- **Cohort comparisons by payment method.** Future spec.
- **Merchant-tunable cohort definition.** The taxonomy is system-managed for stability.
- **Historical trend overlay against cohort.** v1 shows only the latest period; v2 adds 12-week trend.
