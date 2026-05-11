# Backend Spec 022: Positive Trust Factors in Risk Scoring

**Feature Branch:** `backend-022-positive-trust-factors`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api` (Python / FastAPI / Postgres / Celery)
**Sibling spec:** `numu-payments-intelligence/specs/010-positive-trust-signals` (the Shopify-app-side surface that consumes this scoring extension)
**Input:** Spec 010 (Positive Trust Signals + Trusted Buyer Badge) declares NUMU-api as the owner of the `customer_trust` factor computation, the bidirectional reputation network writes, and the auto-approve evaluation. This backend spec defines the schema extensions, scoring formula (with negative-event subtraction), event consumers, and API contract that spec 010 depends on. Spec 010 is hard-blocked until at least `/speckit.plan` of this spec is signed off.

> **Format note:** Authored using the SpecKit spec-template format from `numu-payments-intelligence/.specify/templates/spec-template.md` for cross-repo consistency, pending NUMU-api SpecKit bootstrap.

> **Constitutional alignment:** numu-payments-intelligence constitution v1.2.0 governs the cross-repo contract. This spec satisfies spec 010's FRs, especially: spec 010 FR-001 (the trust factor formula), FR-003 (positive event writes through the existing HMAC pipeline), FR-008 (Flow trigger payload extension — implemented by backend-020), CL-003 (positive/negative formula reconciliation — backend-022 owns the signed score), CL-002 (kill-switch auto-disable behavior). Constitution Principle II (Privacy by Hashing) and Principle IV (Explainable Scoring) are load-bearing.

## Why this feature exists

Spec 000 (NUMU Trust Network Foundation) shipped a deterministic 5-factor risk model that exclusively *penalizes* — every factor's contribution can only raise the risk score. The model has no concept of "this customer has earned trust." A repeat customer who has paid prepaid 10 times across the network gets the same friction as a brand-new buyer.

Spec 010 fixes this by adding a 6th deterministic factor `customer_trust` (positive contribution) plus extending the existing `network_reputation` infrastructure to write positive events alongside negative ones. The Shopify-app-side spec 010 covers the badge UI, auto-approve toggles, and Trusted Buyer surfaces. This backend spec covers:

1. **Schema extensions to `network_reputation`** (or the parallel positive-events table — design choice resolved here) so the existing HMAC pipeline writes positive signals without breaching Principle II.
2. **The signed `customer_trust` formula** — including the negative-event subtraction term that resolves spec 010 CL-003's positive/negative formula contradiction.
3. **Event consumers** for `RecoverySucceededEvent` (from backend-021), `OrderDeliveredEvent` (from the existing shipment status machine), and a future `WhatsAppRespondedEvent` (the 4th positive factor input).
4. **Auto-approve evaluation logic** including the CL-002 kill-switch (auto-disable on RTO rate > 5% over rolling 30 days, with the maintainer-confirmed thresholds: ≥20-sample minimum, one-click re-enable with interstitial, after-disable + email notification).
5. **API extension to `RiskAssessment`** — the read response gains `customer_trust`, `trust_tier`, `negative_adjustment_count`, and the trust-tooltip payload structure spec 010 CL-003 requires.

## User Scenarios & Testing *(mandatory)*

The "user" of this backend spec is the spec 010 implementation + downstream consumers. Acceptance is API + scoring + event-emission behavior.

### User Story 1 — Trust factor computed on every assessment (Priority: P1)

As the spec 010 implementation, when a `RiskAssessment` is computed for an order, the response includes `customer_trust` (0-100), `trust_tier` (one of `none|new|bronze|silver|gold`), and `negative_adjustment_count` (the number of negative events subtracted from the raw positive score).

**Why this priority:** without the trust factor in the response, the badge UI never renders and auto-approve never fires.

**Independent Test:** seed a customer with N successful prepaid + delivered orders. Place a new risky COD order. Verify the assessment response includes a positive `customer_trust` score, the corresponding `trust_tier`, and the `negative_adjustment_count` (zero for a customer with no prior refusals).

**Acceptance Scenarios:**

1. **Given** a customer with `successful_deliveries=10`, `prepaid_orders=5`, `whatsapp_response_rate_pct=80`, `network_positive_events=8`, `network_negative_events=0`, **When** the risk assessment is computed, **Then** `customer_trust = clamp(0, 100, 10*4 + 5*6 + 80*0.1 + 8*3 - 0) = clamp(0, 100, 102) = 100`, `trust_tier = 'gold'`, `negative_adjustment_count = 0`.
2. **Given** the same customer with `network_negative_events=3` (3 RTOs across the network), **When** computed, **Then** `customer_trust = clamp(0, 100, 102 - 3*8) = 78`, `trust_tier = 'silver'`, `negative_adjustment_count = 3`.
3. **Given** a customer with no prior history (new buyer), **When** computed, **Then** `customer_trust = 0`, `trust_tier = 'none'` (no badge rendered).
4. **Given** the network signal lookup fails (Redis unreachable), **When** computed, **Then** `customer_trust` falls back to local-store-only computation (excludes `network_positive_events` and `network_negative_events` terms); the response includes `trust_lookup_degraded: true` so the UI can render a tooltip note (per spec 010 edge case "Network signal lookup failure").
5. **Given** a customer with `customer_trust >= 60` (Silver+), **When** the assessment response is rendered, **Then** the response includes `trust_tooltip: {factors: [{label_en, label_ar, value}, ...]}` per spec 010 CL-003 — enabling the UI tooltip without a second roundtrip.

---

### User Story 2 — Positive network events written on success (Priority: P1)

As the consumer of `RecoverySucceededEvent` (from backend-021) and `OrderDeliveredEvent` (from the existing shipment status machine), when these events fire for an order whose merchant has consented to the trust network, a positive `network_event` row is written through the existing HMAC pipeline.

**Why this priority:** the network-effect flywheel is the moat — without positive contribution, every merchant only ever benefits from network-wide negative signals.

**Independent Test:** trigger a successful recovery for a consented merchant. Verify a positive `network_event` row exists in the database with `event_type = 'recovery_succeeded'`, `polarity = 'positive'`, `phone_hash = HMAC(customer.phone, PLATFORM_SECRET_SALT)`. Verify the consumer is idempotent on `(store_id, shopify_order_id, event_type)`.

**Acceptance Scenarios:**

1. **Given** a `RecoverySucceededEvent` AND the merchant has consented to the trust network, **When** consumed, **Then** a positive `network_event` row is INSERTed with `polarity='positive'`, `event_type='recovery_succeeded'`, `phone_hash=HMAC(customer.phone, PLATFORM_SECRET_SALT)`. A corresponding `network_contribution_log` row is also written for GDPR rollback per spec 000 FR-008.
2. **Given** an `OrderDeliveredEvent` (the existing shipment status machine emits this on DELIVERED terminal state) AND the merchant has consented, **When** consumed, **Then** a positive `network_event` row is INSERTed with `event_type='order_delivered'`.
3. **Given** the merchant has NOT consented to the trust network, **When** any positive event fires, **Then** NO `network_event` row is written. (Per spec 010 FR-004: bidirectional consent.)
4. **Given** the same `RecoverySucceededEvent` is delivered twice (event-bus replay), **When** consumed the second time, **Then** the dedup-key `(store_id, shopify_order_id, event_type)` prevents a second `network_event` insertion (existing pattern).
5. **Given** a customer has accumulated positive `network_event` rows from 5 distinct merchants AND places a first-ever order at a 6th consented merchant, **When** the 6th merchant's risk assessment runs, **Then** `customer_trust` is computed including the 5 prior positive events as `network_positive_events = 5` — first-ever-order-at-store benefit.

---

### User Story 3 — Auto-approve with kill-switch (Priority: P1)

As the consumer of every `RiskAssessmentFinalisedEvent`, when the merchant has `auto_approve_on_trust_enabled = true` AND the customer's `customer_trust >= auto_approve_trust_threshold` (default 80) AND `risk_score < 90` AND the merchant is past the install-grace window AND the merchant has manually approved at least 5 risky orders, the order is auto-approved with the `numu-auto-approved-trusted` tag (additive). The kill-switch monitors RTO rate on auto-approved orders and auto-disables on threshold breach.

**Why this priority:** auto-approve is the operational time-saver that justifies the merchant paying for the trust signal. The kill-switch protects against false-positive RTO loss.

**Independent Test:** configure a merchant with `auto_approve_on_trust_enabled=true`, `auto_approve_trust_threshold=80`. Place 22 orders from customers with `customer_trust=85`. Auto-approve all. Mark 2 of them as RTO. Verify the kill-switch does NOT fire (2/22 = 9% — but we only count auto-approves, not the RTO ratio). Wait — the kill-switch uses RTO rate of *auto-approved orders*: 2 RTOs out of 22 auto-approved = 9.1% > 5% → fires. Verify `auto_approve_on_trust_enabled` flips to `false`, an in-app banner persists, and the email digest gains a "Trust auto-approval events" section.

**Acceptance Scenarios:**

1. **Given** all auto-approve preconditions are met, **When** an order with `customer_trust=85, risk_score=55` arrives, **Then** the order is tagged `numu-auto-approved-trusted` (additive) + `numu-risk-medium` (standard), the assessment's `action_taken='approved'` and `action_taken_by='system_trust_auto'`, no recovery flow is initiated, and no spec-000 automation rules fire (per spec 010 CL-004 short-circuit semantics).
2. **Given** an order with `customer_trust=85, risk_score=92` (high trust but very high risk), **When** the assessment runs, **Then** auto-approve does NOT fire (per spec 010 FR-002's `risk_score > 90` cap); the spec-000 risk-handling path runs instead; the dashboard surfaces the inline "High trust but high risk — review carefully" note per spec 010 US1 AS-4.
3. **Given** the merchant is within the first 30 days post-install (auto-cancel grace window per constitution v1.1.0), **When** an order with high trust arrives, **Then** auto-approve does NOT fire (parallel grace window per spec 010 FR-002).
4. **Given** the merchant has not manually approved at least 5 risky orders since install (per spec 010 CL-001 — counting approves not cancels), **When** an order with high trust arrives, **Then** auto-approve does NOT fire.
5. **Given** the merchant has 22 auto-approved orders in the last 30 days AND 2 of them have transitioned to RTO state, **When** the daily kill-switch evaluation runs, **Then** the RTO rate of 9.1% exceeds the 5% threshold AND the sample size 22 exceeds the 20-minimum (per spec 010 CL-002), **Then** `auto_approve_on_trust_enabled` is auto-disabled, `auto_disabled_at` and `auto_disabled_reason` are persisted, an in-app banner is displayed via the existing notification path, and the next weekly email digest includes a "Trust auto-approval events" section.
6. **Given** `auto_approve_on_trust_enabled` was auto-disabled, **When** the merchant toggles it back on via the settings page, **Then** the API returns the interstitial-modal payload (per CL-002): `{trigger_context: {auto_approves: 22, rtos: 2, period: '...'}, current_threshold: 80, recommended_threshold: 85, message: '...'}` so the UI can render the modal before applying the toggle.
7. **Given** the kill-switch runs daily AND only 8 auto-approves occurred in the last 30 days for a merchant (below the 20-minimum), **When** the evaluation runs, **Then** the kill-switch is dormant — no disable, no notification.

---

### Edge Cases

- **Customer with conflicting signals** (10 successful, 5 recent refusals). The signed formula handles this: positive contribution minus negative subtraction = net trust score. Decay is deterministic per the formula, not ad-hoc.
- **Phone-number change** (carrier port). New phone hash is treated as a new customer; trust history at the old hash persists but doesn't propagate. Known limitation per spec 010 Assumptions.
- **Network signal lookup p99 spike** (Redis or DB slow). Fallback to local-store-only per US1 AS-4. Surface the degradation in the response. Don't block the assessment.
- **Merchant-flag interaction.** If `auto_approve_on_trust_enabled` is `true` AND the merchant has also set a custom `WHATSAPP_CONFIRM` rule for high-risk orders, spec 010 CL-004 mandates auto-approve evaluates first and short-circuits the WHATSAPP_CONFIRM rule. Implementation: the auto-approve evaluator runs in the scoring pipeline BEFORE the spec-000 automation engine.
- **`shop/redact` mid-evaluation.** All auto-approve evaluations for the store cease; the existing GDPR pipeline deletes per-store data within 30 days. The `network_event` rows the merchant contributed are decremented from aggregates per spec 000 FR-008's existing pattern (already covered).
- **`customers/redact`.** Customer's `network_event` rows + child `network_contribution_log` entries are decremented within 30 days per the existing pattern. The customer's hashed phone re-appearing under a new merchant order is treated as new (zero history at that hash).
- **Merchant churns subscription mid-month.** Auto-approve evaluations stop firing immediately on subscription deactivation; the existing subscription-gate from spec 002 handles this. The `network_event` rows previously written remain (historical contribution; not removed on churn — only on `shop/redact`).

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** The risk-scoring path (existing `risk_scoring_tasks.py`) is extended to compute the `customer_trust` factor as a 6th deterministic factor with weight 25%. The signed formula:
  ```python
  positive = (
      successful_deliveries * 4 +
      prepaid_orders * 6 +
      whatsapp_response_rate_pct * 0.1 +
      network_positive_events * 3
  )
  negative_adjustment = (
      network_negative_events * 8 +     # RTOs across the network — heavily weighted
      local_recent_refusals * 6 +        # Local refusals in last 30d
      local_lifetime_refusals * 2        # Older local refusals — decayed weight
  )
  customer_trust = max(0, min(100, positive - negative_adjustment))
  ```
  Documented worked examples per Principle IV explainability requirement.
- **FR-002:** The schema choice for positive network events: **extend the existing `network_reputation` table with a `polarity` enum column** (`'positive' | 'negative'`) rather than create a parallel table. Justification: keeps the existing GDPR contribution-log pattern intact, single index for lookups, no schema-fork to maintain. Migration adds `polarity` defaulting to `'negative'` for all existing rows (correct — prior to this spec, all events were negative).
- **FR-003:** Two new event consumers in `src/infrastructure/events/setup.py`:
  - `RecoverySucceededEvent` → write positive `network_event` (US2 AS-1), gated by merchant network consent.
  - `OrderDeliveredEvent` → write positive `network_event` (US2 AS-2), gated by merchant network consent.
- **FR-004:** A new daily Celery task `kill_switch_evaluate_trust_auto_approve` runs once per store per day:
  - Counts auto-approves in the last 30 days where `action_taken_by='system_trust_auto'` and the order has reached terminal state (DELIVERED, RETURNED, CANCELLED).
  - If count < 20 (per spec 010 CL-002 minimum sample): no-op.
  - Else: compute RTO rate = `count(RETURNED) / count(*)`. If > 5%: flip `auto_approve_on_trust_enabled` to `false`, persist `auto_disabled_at` and `auto_disabled_reason` (a string with the trigger context), trigger the in-app banner via the existing notification path, queue the weekly email digest entry.
- **FR-005:** New API extension to `GET /api/v1/risk/assessments/{assessment_id}` (existing endpoint): the response gains four new fields:
  - `customer_trust: int | null` (0-100)
  - `trust_tier: string` (`'none' | 'new' | 'bronze' | 'silver' | 'gold'` — derived from `customer_trust`)
  - `negative_adjustment_count: int` (raw count of negative events subtracted, for tooltip enrichment per spec 010 CL-003)
  - `trust_tooltip: {factors: [{label_en, label_ar, value}, ...]} | null` (pre-localized factor labels for the badge tooltip)
  - `trust_lookup_degraded: bool` (true if network lookup fell back to local-only per US1 AS-4)
- **FR-006:** New API endpoint `POST /api/v1/shopify/{store_id}/settings/trust_auto_approve` for the spec 010 toggle interaction. Body: `{enabled: bool, threshold: int}`. Validation: threshold ∈ [70, 95]. Response: if the merchant is re-enabling after a kill-switch disable, includes the interstitial-modal payload (US3 AS-6).
- **FR-007:** Per Principle II (Privacy by Hashing): all positive `network_event` writes use the existing HMAC pipeline. No raw PII enters the network table. The `phone_hash` is computed exactly as in spec 000 (HMAC-SHA256 with `PLATFORM_SECRET_SALT`).
- **FR-008:** Per Principle III (GDPR Recital 47):
  - **Legitimate interest:** fraud prevention + order completion — the same justification as the existing negative-event pipeline. Positive events are the same data class (hashed phone + event type + timestamp), just with positive semantics.
  - **DSAR:** customer can request *aggregate* network signals about their hashed phone via the existing trust-network DSAR endpoint. Positive event aggregates are exported alongside negative event aggregates; per-merchant attribution stays internal to the contribution log.
  - **Erasure:** on `customers/redact`, the customer's positive `network_event` rows are decremented from aggregates per the existing `network_contribution_log` mechanism (spec 000 FR-008), within 30 days.
  - **Opt-out:** customers do not opt in or out of network contribution directly (it's a merchant-level consent per spec 010 FR-004). Customers can request erasure via DSAR.
- **FR-009:** Per Principle IV (Explainable Scoring): the formula in FR-001 is documented with worked examples in `docs/risk-scoring/customer-trust-formula.md`. Snapshot tests pin 20 representative customer profiles to ensure the formula doesn't silently drift between releases.
- **FR-010:** Per Principle V: every acceptance scenario above has an integration test in `tests/integration/test_customer_trust_*.py`. The kill-switch daily task has a time-traveling integration test that fast-forwards 30 days to verify the disable + notification path.
- **FR-011:** Spec 010 FR-008 requires the spec 004 Flow trigger payload (`risk_score_calculated`) to include `customer_trust` and `trust_tier` — implemented by backend-020 reading the new fields from this spec's API response. **Cross-spec dependency, not a new FR here, just a forward-flag.**

### Key Entities

```python
# src/core/entities/network_reputation.py — EXTENSION

class NetworkEventPolarity(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"

# Add to existing NetworkEvent / NetworkReputation entity:
polarity: Mapped[NetworkEventPolarity] = mapped_column(
    Enum(NetworkEventPolarity, values_callable=lambda e: [m.value for m in e]),
    default=NetworkEventPolarity.NEGATIVE,  # Existing rows are all negative; new positive rows must opt in
)

# Migration adds the column with DEFAULT 'negative'; existing rows correctly classified.
# Backfill: not required (the default handles it).

# src/core/entities/shopify.py — ShopifyAppSettings extension

# Add fields:
auto_approve_on_trust_enabled: Mapped[bool] = mapped_column(default=False)
auto_approve_trust_threshold: Mapped[int] = mapped_column(default=80)  # CHECK: 70 <= x <= 95
auto_disabled_at: Mapped[datetime | None] = mapped_column(nullable=True)
auto_disabled_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
first_recovery_celebration_dismissed: Mapped[bool] = mapped_column(default=False)  # Per spec 009 CL-012
```

### Default formula constants

```python
# src/application/services/customer_trust_formula.py
TRUST_WEIGHT_SUCCESSFUL_DELIVERIES = 4
TRUST_WEIGHT_PREPAID_ORDERS = 6
TRUST_WEIGHT_WA_RESPONSE_RATE = 0.1
TRUST_WEIGHT_NETWORK_POSITIVE = 3
TRUST_PENALTY_NETWORK_NEGATIVE = 8
TRUST_PENALTY_LOCAL_RECENT_REFUSAL = 6
TRUST_PENALTY_LOCAL_LIFETIME_REFUSAL = 2

TRUST_TIER_BOUNDARIES = {
    'none':   (0, 0),       # Customers with no history
    'new':    (1, 29),      # Some history but below Bronze
    'bronze': (30, 59),
    'silver': (60, 79),
    'gold':   (80, 100),
}

AUTO_APPROVE_KILL_SWITCH_MIN_SAMPLE = 20      # Spec 010 CL-002
AUTO_APPROVE_KILL_SWITCH_MAX_RTO_RATE = 0.05
AUTO_APPROVE_RISK_CAP = 90                    # Spec 010 FR-002
```

## Success Criteria *(mandatory)*

- **SC-001:** Per Principle IV: 100% of `customer_trust` computations are reproducible by the documented formula. Snapshot tests cover 20 representative customer profiles; a formula change requires an explicit "snapshot drift accepted" PR comment from the maintainer.
- **SC-002:** Per Principle II: 100% of positive `network_event` writes use the HMAC pipeline; zero rows contain raw PII. Quarterly audit of 100 random rows finds zero PII.
- **SC-003:** Auto-approve kill-switch correctness: integration tests cover (a) below-minimum-sample (no fire), (b) above-minimum-sample-below-rate (no fire), (c) above-both-thresholds (fire), (d) re-enable after fire returns interstitial payload. All pass in CI.
- **SC-004:** Network event consumer idempotency: 100,000 simulated duplicate-event injections produce zero duplicate `network_event` rows. Verified by integration test fixture.
- **SC-005:** API extension backward compatibility: existing consumers of `GET /api/v1/risk/assessments/{id}` continue to work without modification when the new fields are added (additive change). Verified by a snapshot test of the response shape with new fields nullable.
- **SC-006:** Performance: the trust-factor computation adds ≤ 50ms p95 to the existing risk-scoring path (one indexed query against `network_reputation`). Verified by k6 load test.
- **SC-007:** Trust-tier upgrade Flow trigger payload extension (per FR-011): the `risk_score_calculated` Flow trigger from backend-020 includes `customer_trust` and `trust_tier` in every emission. Verified by cross-spec integration test.
- **SC-008:** GDPR erasure: on `customers/redact` for a customer with N positive `network_event` rows, all rows + child `network_contribution_log` entries are decremented + deleted within 30 days. Verified by the existing data-retention test harness extended to cover the polarity column.

## Assumptions

- The existing `network_reputation` infrastructure from spec 000 is mature and supports a nullable polarity addition without breaking GDPR rollback. Confirmed by reading `src/infrastructure/messaging/tasks/trust_network_maintenance.py`.
- The existing risk-scoring path (`risk_scoring_tasks.py`) is extensible — adding a 6th factor doesn't break the 5-factor preliminary fast-score per the constitution's hybrid sync/async pattern. Trust factor computation runs in the *async full-score* path only, NOT in the 200ms preliminary; preliminary returns `customer_trust = null` and the UI handles the placeholder.
- The existing shipment status machine emits an `OrderDeliveredEvent` on DELIVERED terminal state, OR the event is added trivially as part of this spec. (Backend-021 already enumerates the event in its assumptions.)
- Spec 002 (Billing API) gates auto-approve evaluation by subscription status; this spec inherits that gate without additional logic.
- Native Arabic translations for the trust-tooltip factor labels (FR-005 `trust_tooltip.factors[].label_ar`) are authored under spec 010's locale-parity workflow before SC-007 can be marked done.
- The existing in-app notification path supports persistent banners (the kill-switch disable banner per US3 AS-5). If not, the banner is added as a follow-up small spec.
- Constitution Principle IV's "no opaque ML in v1" applies to this spec — the trust factor stays deterministic per FR-001. ML-based trust scoring is a future spec + constitution amendment.

## Out of scope

- **The Trusted Buyer badge UI, trust-tier rendering, settings page toggles** — all owned by spec 010 in the Shopify app.
- **Public-facing customer trust profile** — deferred to spec 018 (Network Reputation Marketplace).
- **Cross-platform trust portability** (e.g., Shopify trust → WooCommerce trust) — deferred to spec 017 platform-abstraction.
- **Merchant-tunable trust formula** — formula stays system-managed for consistency. Merchants disable auto-approve if they disagree.
- **Per-merchant trust isolation flag** (contribute negative but not positive, or vice versa). Trust network is bidirectional or off entirely per spec 010 FR-004.
- **Trust signal export to merchant CSV / data warehouse** — future spec.
- **Trust-based pricing tiers in spec 002** (e.g., pay more to enable Gold-tier auto-approve) — out of scope per spec 010.
- **Real-time auto-approve threshold tuning** based on observed RTO rate (e.g., automatic threshold raise when RTO rate climbs). Manual recommendation in the interstitial modal per CL-002 is sufficient for v1.
