# Backend Spec 026: Platform Abstraction Layer (v1 — schema only)

**Feature Branch:** `backend-026-platform-abstraction`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api`
**Sibling spec:** `numu-payments-intelligence/specs/017-platform-abstraction-layer` (the larger v1 → v3 roadmap; this backend spec implements only the schema part)
**Input:** Spec 017 — *"Critical pre-launch action: Run this before P1 ships, not in Phase 3, even though the WooCommerce adapter waits. The schema migration is the cheap part; deferring it is the expensive part."* This spec is the cheap part: add a `source` discriminator column to every order-shaped table so future platform adapters (Salla, Zid, WooCommerce, TikTok Shops) write into the same schema without a destructive migration.

> **Constitutional alignment:** Principle I (Stateless Shopify Boundary — extends, doesn't violate; new adapters become sibling apps that write into the same `source='salla'` etc. rows); Principle V (acceptance scenarios → tests).

## Why this feature exists

Today every order, customer, and shipment in NUMU-api is implicitly Shopify-sourced. The risk-scoring pipeline, recovery flow, courier intelligence, and trust signals all assume a Shopify-shaped row. Adding WooCommerce / Salla / Zid later requires either:

(a) **A destructive migration** — backfill `source` columns post-hoc, find every implicit-Shopify code path, fix everything, ship. Slow and risky.

(b) **The cheap path now** — add a `source` enum column with `DEFAULT 'shopify'` to every order-shaped table. All existing rows take the default; all existing code stays working without changes. New adapters set their own value (`'salla'`, `'woocommerce'`, etc.) when inserting.

Option (b) is what the meta-roadmap explicitly recommends. This spec lands it before P1 ships, not in Phase 3.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — `source` column added with safe default (Priority: P1)

As a future platform adapter implementer, when I write a new `OrderModel` row, I can set `source='salla'` (or any other supported value) and downstream code (risk scoring, recovery, etc.) treats the row identically to a Shopify-sourced row.

**Independent Test:** create one Order with the default (`shopify`), one with `source='woocommerce'`, one with `source='salla'`. All three persist. Existing risk-scoring + recovery logic doesn't care about the value.

**Acceptance Scenarios:**

1. **Given** a new `OrderModel` row inserted without specifying `source`, **When** the row is read back, **Then** `source = 'shopify'`.
2. **Given** the `OrderSource` enum, **When** an adapter sets `source='woocommerce'`, **Then** the row persists and reads back with that exact value.
3. **Given** all existing pre-spec rows, **When** the migration is applied, **Then** every existing row's `source` is populated as `'shopify'` (server_default).
4. **Given** a SQL aggregation query that filters by `source`, **When** run, **Then** it returns only the matching subset (i.e., the column is queryable as a normal enum).

### User Story 2 — Same column on Customer + Shipment (Priority: P1)

`Customer` and `Shipment` carry the same discriminator so cross-table joins (risk scoring's customer_history, courier_stats per source) stay coherent.

**Acceptance Scenarios:**

1. Same as US1 but for `CustomerModel`.
2. Same as US1 but for `ShipmentModel`.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** A new `OrderSource` StrEnum is added at `src/core/entities/platform_source.py` with values: `shopify`, `woocommerce`, `salla`, `zid`, `numu_native`, `tiktok_shop`. Members are values (lowercase) per the project's enum convention; SQLAlchemy uses `values_callable=lambda e: [m.value for m in e]`.
- **FR-002:** Add `source` column to `OrderModel`, `CustomerModel`, `ShipmentModel`. Type `Enum(OrderSource, ..., values_callable=...)`, nullable=False, server_default `'shopify'`.
- **FR-003:** Migration backfills all existing rows with `'shopify'` via the column's server_default — no separate UPDATE pass needed.
- **FR-004:** Existing risk scoring, recovery flow, and courier stats code is NOT changed by this spec — they operate platform-agnostically already; the column is purely additive infrastructure.
- **FR-005:** Per Principle V — acceptance scenarios mapped to tests in `tests/integration/test_platform_source.py`.

### Key Entities

```python
# src/core/entities/platform_source.py
class OrderSource(StrEnum):
    SHOPIFY = "shopify"
    WOOCOMMERCE = "woocommerce"
    SALLA = "salla"
    ZID = "zid"
    NUMU_NATIVE = "numu_native"
    TIKTOK_SHOP = "tiktok_shop"
```

## Success Criteria *(mandatory)*

- **SC-001:** Migration applies cleanly to a Postgres instance with existing data; all pre-existing rows take `source='shopify'`.
- **SC-002:** All existing tests still pass after the migration. Verified by re-running the full backend suite.
- **SC-003:** SC-001 + SC-002 collectively prove the spec is non-disruptive — the cheap-pre-launch promise holds.

## Out of scope

- **The adapter contracts** (what shape a Salla webhook handler produces, etc.) — Phase 3 work, separate specs per platform.
- **Per-source business logic** — risk scoring, recovery, etc. stay source-agnostic for v1.
- **WooCommerce reference adapter** — the spec 017 P3 deliverable. Adding it requires the actual platform integration code, not just schema.
