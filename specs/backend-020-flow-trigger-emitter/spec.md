# Backend Spec 020: Shopify Flow Trigger Emitter

**Feature Branch:** `backend-020-flow-trigger-emitter`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api` (Python / FastAPI / Postgres / Celery)
**Sibling spec:** `numu-payments-intelligence/specs/004-shopify-flow-connector` (registers the trigger schemas with Shopify; this spec emits the events into them)
**Input:** Spec 004 (Shopify Flow Connector) declares three triggers (`risk_score_calculated`, `cod_verification_completed`, `network_signal_threshold_crossed`) but explicitly notes in its "Out of scope" section that "Numu-api side `flowTriggerReceive` calls" are deferred to this follow-up. Without backend-020, spec 004's triggers are registered but never fire — merchants who build Flow workflows around them never see them trigger.

> **Format note:** Authored using the SpecKit spec-template format from `numu-payments-intelligence/.specify/templates/spec-template.md` for cross-repo consistency, pending NUMU-api SpecKit bootstrap.

> **Constitutional alignment:** numu-payments-intelligence constitution v1.2.0 governs the cross-repo contract. This spec is the NUMU-api implementation that satisfies spec 004's trigger emission obligation, especially: spec 004 §3 Out-of-scope ("Numu-api side `flowTriggerReceive` calls"), constitution Principle I (state in NUMU-api, Shopify app proxies only), Principle V (spec-derived tests).

## Why this feature exists

Spec 004 ships three Shopify Flow trigger registrations — the schemas Shopify uses to render trigger nodes in the Flow UI ("When `numu — risk_score_calculated`…"). When a merchant builds a Flow workflow on those triggers, Shopify expects NUMU-api (the source of truth for the underlying events) to *emit* the trigger occurrences via Shopify Admin GraphQL's `flowTriggerReceive` mutation.

Without an emitter, the merchant's Flow workflow is structurally complete but never executes — the trigger node is permanently silent. From the merchant's perspective, the integration looks broken; from the App Store reviewer's perspective, the app has registered triggers that don't actually fire (a known rejection vector for Built-for-Shopify status).

This spec wires up the three emitters:

1. **`risk_score_calculated`** — fired when `RiskAssessmentFinalisedEvent` (or `RiskAssessmentPreliminaryEvent`) lands. Payload includes the order, score, level, suggested action, customer trust score (per spec 010 FR-008), trust tier, and `score_type`.
2. **`cod_verification_completed`** — fired when an inbound WhatsApp customer reply triggers a `RiskAssessment.action_taken` flip to `customer_confirmed` or `customer_rejected`. Payload includes the order, the outcome, and the post-event customer_trust per spec 010 FR-008.
3. **`network_signal_threshold_crossed`** — fired when an order's customer crosses the cross-merchant trust threshold (default: 3+ stores reporting RTOs in 30 days). Payload includes the order, the crossed threshold, and the network metrics that crossed it (anonymized per Principle II — counts only, no merchant identities).

Plus the post-spec-009 additions:

4. **`recovery_succeeded`** — fired on `RecoverySucceededEvent` (per spec 009 FR-013 forward-flag). Payload includes the order, the rail, the recovered amount.
5. **`recovery_abandoned`** — fired on `RecoveryAbandonedEvent`. Payload includes the order and the abandonment reason.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Risk-score trigger fires on every assessment (Priority: P1)

As the spec 004 implementation, when a `RiskAssessmentFinalisedEvent` lands, a `flowTriggerReceive` mutation is sent to Shopify Admin GraphQL within 30 seconds for the corresponding store, carrying the trigger payload in spec 004 FR-002's declared shape.

**Why this priority:** without this trigger firing, the entire Flow connector is non-functional for the most-common workflow (cancel-on-critical-risk per spec 004 Story 1).

**Independent Test:** publish a `RiskAssessmentFinalisedEvent` with `score_type='final'` and `risk_score=92` for a synthetic order on a dev store with the Flow app installed. Within 30 seconds, verify a `flowTriggerReceive` mutation was sent (visible in NUMU-api logs + the merchant's Flow workflow execution log).

**Acceptance Scenarios:**

1. **Given** a `RiskAssessmentFinalisedEvent` with `score_type='final'`, **When** the event is consumed, **Then** a single `flowTriggerReceive` mutation is sent for the trigger handle `risk_score_calculated` with the payload `{order: {id, name}, score: <int>, level: <enum>, suggested_action: <enum>, customer_trust: <int|null>, trust_tier: <enum>, score_type: 'final'}`.
2. **Given** a `RiskAssessmentPreliminaryEvent` (the 200ms fast-score), **When** consumed, **Then** the same trigger fires with `score_type: 'preliminary'` so workflows that only act on final scores can filter by `score_type`.
3. **Given** the Shopify Admin GraphQL endpoint returns 5xx, **When** the emitter catches it, **Then** Celery's retry policy triggers (max 5 retries, exponential backoff 30s/60s/120s/240s/480s); after exhaustion, the failure is logged + alerted.
4. **Given** the merchant has not installed the Flow connector app, **When** the event would fire a trigger, **Then** the `flowTriggerReceive` returns 4xx (no subscription) — caught + logged at INFO level (not an error; expected for non-Flow-using merchants).
5. **Given** the same event is delivered twice (event-bus replay), **When** the emitter runs, **Then** the second invocation is deduped via the `(store_id, event_id, trigger_handle)` idempotency key persisted in a `FlowTriggerEmissionLog` table; only one Shopify mutation is sent.

---

### User Story 2 — COD verification trigger fires on customer reply (Priority: P1)

As the spec 004 implementation, when an inbound WhatsApp customer reply causes a `RiskAssessment.action_taken` to transition to `customer_confirmed` or `customer_rejected`, a `cod_verification_completed` trigger fires within 30 seconds.

**Independent Test:** simulate an inbound WhatsApp reply "yes" against an order in `pending` state with a recovery flow active. Verify the assessment's `action_taken` transitions to `customer_confirmed` AND a `flowTriggerReceive` for `cod_verification_completed` fires with `outcome='confirmed'`.

**Acceptance Scenarios:**

1. **Given** an inbound WhatsApp reply maps to `customer_confirmed`, **When** the assessment is updated, **Then** the trigger fires with payload `{order, outcome: 'confirmed', customer_trust_after_event: <int|null>}` per spec 010 FR-008.
2. **Given** the customer reply is part of a recovery STOP per spec 009 CL-003 / CL-008, **When** the inbound is attributed to the right `(store_id, hashed_phone)`, **Then** the trigger fires with `outcome: 'rejected'` and `rejection_reason: 'opt_out'`.
3. **Given** the customer has active recovery flows at multiple stores AND the reply is in the CL-008 ambiguous window, **When** the disambiguation auto-reply is sent, **Then** the trigger does NOT fire yet (waits for the customer's clarification reply); fires only after the unambiguous attribution is resolved.

---

### User Story 3 — Network-signal trigger fires on threshold crossing (Priority: P2)

As the spec 004 implementation, when an order's customer carries a network reputation that crosses the configured threshold (default: 3+ stores reporting RTOs in 30 days), the trigger fires once per (customer-hash, threshold) crossing.

**Why this priority:** P2 because the network reputation feature is most valuable to merchants who proactively triage; the headline P1 risk-score trigger covers the immediate need.

**Acceptance Scenarios:**

1. **Given** an order's customer's hashed phone has accumulated negative `network_event` rows from 3 distinct stores in the last 30 days, **When** the assessment is computed, **Then** the trigger fires once with payload `{order, flagged_stores_count: <int>, threshold_crossed: 3, lookback_days: 30}`.
2. **Given** the same customer's hash has already triggered this `(merchant, customer_hash, threshold=3)` combination in the last 30 days, **When** another order arrives, **Then** the trigger does NOT re-fire (deduped via the same `FlowTriggerEmissionLog` table).
3. **Given** the merchant has configured a custom threshold (e.g., 5+ stores) via Flow workflow settings, **When** the assessment is computed, **Then** the trigger fires only at the merchant's configured threshold — NOT at the default 3.

---

### User Story 4 — Recovery-succeeded / recovery-abandoned triggers (Priority: P2 — extends spec 004)

As the spec 009 + spec 004 follow-up integration, `RecoverySucceededEvent` and `RecoveryAbandonedEvent` emit Flow triggers `recovery_succeeded` and `recovery_abandoned` respectively. Spec 004 must register the new trigger schemas in a follow-up; this spec emits into them.

**Acceptance Scenarios:**

1. **Given** a `RecoverySucceededEvent { rail, dedupe_key, recovered_amount_cents }`, **When** the emitter consumes it, **Then** the trigger fires with payload `{order, rail, recovered_amount_cents, dedupe_key}` (the `dedupe_key` is included so workflows can correlate to the underlying order via the `(store_id, shopify_order_id)` tuple per constitution v1.2.0 FR-010).
2. **Given** a `RecoveryAbandonedEvent { reason }`, **When** consumed, **Then** the trigger fires with payload `{order, reason: <enum: customer_no_response|customer_explicit_stop|merchant_cancel|template_rejected|...>}`.

---

### Edge Cases

- **Shopify rate limits.** The Admin GraphQL endpoint enforces per-shop QPS caps. If the emitter is sending a burst (e.g., a backfill of risk scores for a day's orders after a system recovery), it MUST throttle to stay under the limit; persistent rate-limit responses trigger the same Celery retry path as 5xx.
- **Trigger schema drift between this emitter and spec 004's registered schema.** A field added here without spec 004 updating its registration → Shopify rejects the mutation with a schema error. Mitigated by a CI test that diffs spec 004's registered schemas against this emitter's payload builder.
- **Replay safety on event bus restart.** The dedup table protects against double-fire on event replay.
- **`shop/redact` mid-emission.** If the emission is in-flight when the merchant uninstalls, the in-flight Shopify call may 4xx (app uninstalled). The emitter catches this gracefully, marks the emission log entry as `terminated_uninstall`, and does not retry.
- **`customers/redact` interaction.** The `risk_score_calculated` payload includes `order.id` and `order.name` (Shopify identifiers) but NOT raw customer PII — the trigger fires from the assessment's deterministic factors, not the customer profile. So `customers/redact` does not require trigger-payload scrubbing; the customer's `RiskAssessment` rows are deleted per spec 000's existing GDPR pipeline within 30 days, and any previously-fired triggers are immutable by design (Shopify Flow execution logs are merchant-side).
- **Multiple triggers from one event.** A single `RiskAssessmentFinalisedEvent` may need to fire BOTH `risk_score_calculated` AND `network_signal_threshold_crossed` (if both conditions match). Each is a separate `flowTriggerReceive` call with its own dedup key.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** A new Celery handler subscribes to the event bus and emits `flowTriggerReceive` Shopify Admin GraphQL mutations for the matching trigger handle. Implementation in `src/infrastructure/messaging/tasks/flow_trigger_tasks.py`.
- **FR-002:** A new `FlowTriggerEmissionLog` SQLAlchemy model records each emission attempt: `(emission_id, store_id, source_event_id, trigger_handle, dedup_key, status, attempted_at, succeeded_at, error_reason)`. Unique constraint on `(store_id, dedup_key, trigger_handle)` enforces idempotency.
- **FR-003:** Dedup key construction:
  - `risk_score_calculated`: `dedup_key = f"{shopify_order_id}:{score_type}"` (one trigger per (order, preliminary|final) — accommodates the constitution's hybrid sync/async risk scoring).
  - `cod_verification_completed`: `dedup_key = f"{shopify_order_id}:verification:{transition_id}"` where `transition_id` is the assessment's monotonic transition counter (allows multiple confirmations if a customer flips state — though that's an edge case).
  - `network_signal_threshold_crossed`: `dedup_key = f"{customer_phone_hash}:{threshold}:{period_start}"` where `period_start` is the merchant-config rolling window start (e.g., `2026-05-01` for a calendar-month window) — natural per-period dedup.
  - `recovery_succeeded`: `dedup_key = f"{shopify_order_id}:recovery_succeeded"` matches constitution v1.2.0 FR-010 dedup pattern; one fire per recovered order.
  - `recovery_abandoned`: `dedup_key = f"{shopify_order_id}:recovery_abandoned"`; fires once even if the abandon transition is retried.
- **FR-004:** The emitter MUST verify the merchant has the Flow connector subscribed before sending. Implementation: cache a per-store `flow_subscribed` boolean (TTL 5 minutes); on cache miss, query `appSubscription` from the Shopify Admin API. On `false`, skip the emission and log at INFO.
- **FR-005:** Per Principle I (Stateless Shopify Boundary): all emission logic lives in NUMU-api. The Shopify app does NOT emit triggers; it only registers the schemas (spec 004's responsibility).
- **FR-006:** Per Principle II (Privacy by Hashing): the `network_signal_threshold_crossed` payload contains `flagged_stores_count` (an integer) but NEVER the names or identifiers of contributing stores. Constitution-aligned anonymization.
- **FR-007:** Per Principle III (GDPR Recital 47):
  - **Legitimate interest:** automation enablement — the merchant's Flow workflow is the merchant's own automation; trigger emission is necessary for the merchant to use a feature they have already configured.
  - **DSAR:** the `FlowTriggerEmissionLog` does not store customer PII (only `dedup_key` which is order-id-based or hashed-phone-based). Customer DSARs do not need to extend here.
  - **Erasure:** on `customers/redact`, no rows in `FlowTriggerEmissionLog` need deletion (no raw PII). On `shop/redact`, all rows for the store are deleted within 30 days.
  - **Opt-out:** customers cannot opt out of trigger emission directly (the trigger is between NUMU and the merchant's Shopify, not customer-facing). Customer opt-out (per spec 009 CL-003) suppresses the *upstream* events that would trigger emission.
- **FR-008:** Per Principle V: every acceptance scenario above translates to an integration test in `tests/integration/test_flow_trigger_emitter_*.py`.
- **FR-009:** Each emission MUST be observable in metrics: counter `flow_trigger_emissions_total{store_id, trigger_handle, status}`. Persistent failure rates above 5% per (store, trigger_handle) over a rolling 1-hour window page on-call.
- **FR-010:** Schema validation: at startup, the emitter loads spec 004's registered trigger schemas from a versioned JSON file checked into both repos (or fetched from a shared schema registry — design choice in `/speckit.plan`); if the emitter's payload builder produces a payload that doesn't validate, the emission fails fast with a clear error pointing at the schema mismatch. Prevents the "trigger schema drift" edge case.

### Key Entities

```python
# src/core/entities/flow_trigger_emission.py

class FlowTriggerEmissionStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED_RETRYABLE = "failed_retryable"
    FAILED_TERMINAL = "failed_terminal"
    TERMINATED_UNINSTALL = "terminated_uninstall"
    SKIPPED_NOT_SUBSCRIBED = "skipped_not_subscribed"

class FlowTriggerEmissionLog(Base):
    __tablename__ = "flow_trigger_emission_log"
    emission_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.store_id"), index=True)
    source_event_id: Mapped[str] = mapped_column(String(64))
    trigger_handle: Mapped[str] = mapped_column(String(64))
    dedup_key: Mapped[str] = mapped_column(String(256))
    status: Mapped[FlowTriggerEmissionStatus] = mapped_column(
        Enum(FlowTriggerEmissionStatus, values_callable=lambda e: [m.value for m in e])
    )
    attempted_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    succeeded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    error_reason: Mapped[str | None] = mapped_column(String(512), nullable=True)
    payload_snapshot: Mapped[dict] = mapped_column(JSONB)  # The exact payload sent (for debugging)
    __table_args__ = (
        UniqueConstraint("store_id", "dedup_key", "trigger_handle", name="uq_flow_trigger_dedup"),
        Index("ix_flow_trigger_status_attempted", "status", "attempted_at"),
    )
```

## Success Criteria *(mandatory)*

- **SC-001:** Median emission latency (event-bus consume → Shopify Admin GraphQL ack) ≤ 30 seconds; p95 ≤ 5 minutes.
- **SC-002:** Idempotency: across 100,000 simulated duplicate-event injections, zero duplicate Shopify mutations sent for the same `(store_id, dedup_key, trigger_handle)`. Verified by integration test fixture.
- **SC-003:** Schema validation catches 100% of payload-shape regressions before emission. Verified by a CI test that mutates the emitter's payload builder and asserts the validation step fails.
- **SC-004:** Subscription gating works: zero emission attempts to merchants without an active Flow connector subscription (caught by FR-004 cache + Shopify subscription check). Verified by integration test.
- **SC-005:** Retry behavior: 100% of recoverable Shopify 5xx responses are retried per the Celery exponential-backoff schedule; 100% of permanent 4xx responses are NOT retried. Verified by integration test injecting both error classes.
- **SC-006:** Per Principle V: 100% of acceptance scenarios have integration tests; all pass in CI before merge.
- **SC-007:** Observability: the `flow_trigger_emissions_total` counter is wired into the existing Prometheus / Grafana stack; the 5% failure-rate alert fires on an injected sustained-failure scenario.
- **SC-008:** Cross-repo schema parity: spec 004's registered schemas + this emitter's payload builders agree on every field. Verified by a CI test that loads both and diffs.

## Assumptions

- The existing NUMU-api event bus (`src/core/events/`) supports adding new consumers per the existing pattern.
- The `RiskAssessmentFinalisedEvent` and `RiskAssessmentPreliminaryEvent` are emitted by the existing risk-scoring path (or are added as part of backend-021 / backend-022).
- The `RecoverySucceededEvent` and `RecoveryAbandonedEvent` are emitted by backend-021 per its FR-008.
- Shopify Admin GraphQL `flowTriggerReceive` mutation contract is stable for the API version declared in `shopify.app.toml` (currently 2026-04). If a later API version changes the contract, version-guard catches it.
- The existing Shopify admin API client (`src/infrastructure/external_services/shopify/admin_client.py`) handles rate limiting, retry, and access-token refresh.
- Spec 004's trigger registrations + this emitter's payload-builder code are kept in sync via the CI schema-parity test (SC-008). If they drift, the test fails.

## Out of scope

- **Spec 004's trigger registrations and action implementations** — owned by spec 004 in the Shopify app.
- **Customer-facing UI for trigger configuration** — merchants configure their own Flow workflows in Shopify's UI; this spec only emits.
- **Per-event trigger payload customization by merchant** — payloads are constitutional; merchant-specific data lives in the Flow workflow's filter/action steps, not in our trigger payload.
- **Backfilling triggers for historical events** — only forward-looking emission; if a merchant subscribes to Flow after their app was already running, prior events do NOT replay.
- **Cross-platform trigger emission** (e.g., emitting Zapier-equivalent triggers for non-Shopify stores). Future spec; aligns with spec 017 platform-abstraction.
