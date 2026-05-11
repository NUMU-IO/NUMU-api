# Backend Spec 021: RecoveryFlow Aggregate + State Machine

**Feature Branch:** `backend-021-recovery-flow-aggregate`
**Created:** 2026-05-11
**Status:** Draft
**Repo:** `NUMU-api` (Python / FastAPI / Postgres / Celery / Redis)
**Sibling spec:** `numu-payments-intelligence/specs/009-cod-recovery-engine` (the Shopify-app-side surface that consumes this aggregate)
**Input:** Spec 009 (COD Recovery Engine) declares NUMU-api as the owner of the RecoveryFlow aggregate per constitution Principle I (Stateless Shopify Boundary). This backend spec defines the entity model, state machine, API contract, event emissions, and Celery orchestration that spec 009 depends on. Spec 009 is hard-blocked until at least `/speckit.plan` of this spec is signed off.

> **Format note:** This spec is authored using the SpecKit spec-template format from `numu-payments-intelligence/.specify/templates/spec-template.md` for cross-repo consistency. NUMU-api's own SpecKit bootstrap is pending (`/speckit.brownfield.bootstrap` from this repo's root). Once bootstrap completes, this spec migrates into the NUMU-api SpecKit lifecycle without content changes — only the surrounding workflow tooling (extension hooks, version-guard, retrospective) is added.

> **Constitutional alignment:** numu-payments-intelligence constitution v1.2.0 governs the cross-repo contract. This spec is the NUMU-api implementation that satisfies its sibling's FRs, especially: spec 009 FR-001 (`RecoveryFlow` aggregate creation), FR-002 (cadence schema), FR-004 (event emissions), FR-006 (read API), constitution v1.2.0 FR-010 (canonical recovered-revenue event), FR-011 (timezone + refund handling), FR-012 (capped-amount reconciliation ledger), CL-004 (write-through monthly aggregate).

## Why this feature exists

Today, NUMU-api supports the building blocks of recovery — WhatsApp messaging, payment-link sessions, Instapay manual flow with OCR, deposit/balance-due fields on Order, Celery countdown jobs, event bus — but they are not orchestrated into a coherent multi-step recovery process per order. Spec 000 (NUMU Trust Network Foundation) ships a one-shot WhatsApp nudge automation rule that uses these primitives ad-hoc; spec 009 raises the bar to a structured multi-step cadence with multi-rail offers, deposit fallback, idempotent bookkeeping, and a merchant-visible timeline.

This spec creates the **`RecoveryFlow` aggregate** as the durable owner of that orchestration. The aggregate is responsible for:

1. **Lifecycle state machine** — the transitions from `pending_step_1` through terminal `succeeded` / `succeeded_deposit` / `abandoned` / `terminated_uninstall` / `blocked_no_gateway` / `blocked_no_template`.
2. **Cadence execution** — scheduling and firing each step via Celery countdown, with merchant-overridable cadence (per spec 009 CL-001) bounded by validation.
3. **Idempotent recovery measurement** — exactly-once recording of "this order was recovered" for both the dashboard tile and the rev-share billing path, deduped on `(store_id, shopify_order_id)` per constitution v1.2.0 FR-010.
4. **Write-through monthly aggregate** — atomic update of `RecoveryMonthlyRollup` so the dashboard read-path stays sub-50ms (per spec 009 CL-004).
5. **Event emission** — `RecoveryStartedEvent`, `RecoveryStepSentEvent`, `RecoverySucceededEvent`, `RecoveryAbandonedEvent`, `RecoveryBlockedEvent` to the existing NUMU-api event bus, consumed by spec 010 (positive trust signals), the dashboard rollup, and retrospective metrics.
6. **Cross-spec coordination** — listening to `RiskAssessmentFinalisedEvent` (the trigger to start a flow), `PaymentProofApprovedEvent` (Instapay success path from spec 012), and gateway webhook events (Paymob / Fawry success paths).

## User Scenarios & Testing *(mandatory)*

The "user" of this backend spec is the spec 009 implementation + downstream consumers. Acceptance is API + event-emission + state-machine behavior, not UI behavior.

### User Story 1 — Risk-assessment-finalised triggers a flow (Priority: P1)

As the spec 009 implementation, when a `RiskAssessmentFinalisedEvent` fires for an order whose final `risk_score >= recovery_trigger_threshold` AND the merchant has at least one payment gateway connected AND the merchant has `recovery_enabled = true` AND the merchant has an active subscription, a `RecoveryFlow` row is created and step 1 is scheduled within 60 seconds.

**Why this priority:** without flow creation, no other downstream behavior fires. This is the entry point.

**Independent Test:** publish a `RiskAssessmentFinalisedEvent` to the event bus for a synthetic order. Within 60 seconds verify (a) a `RecoveryFlow` row exists with `state = 'pending_step_1'`, (b) a Celery task `recovery_send_step` is scheduled for the configured `step_1.delay_seconds`, (c) a `RecoveryStartedEvent` was emitted.

**Acceptance Scenarios:**

1. **Given** a `RiskAssessmentFinalisedEvent` with `score_type = 'final'` and `risk_score >= recovery_trigger_threshold`, **When** the event is consumed, **Then** the gating predicate is evaluated and (only if all conditions pass) a `RecoveryFlow` row is INSERTed with `state = 'pending_step_1'`, `cadence = merchant.recovery_cadence_override OR DEFAULT_CADENCE`, `current_step_index = 0`, `payment_link_session_id = (newly-created)`.
2. **Given** the merchant has `recovery_enabled = false`, **When** the event is consumed, **Then** no flow is created and a `RecoveryBlockedEvent { reason: 'feature_disabled' }` is emitted (so dashboard analytics can show how many would-be flows were suppressed).
3. **Given** the merchant has no active subscription per spec 002, **When** the event is consumed, **Then** no flow is created, no `RecoveryBlockedEvent` is emitted (avoids noise — billing state is not a recovery-engine concern in the metrics), but the gating decision is logged at INFO level for support troubleshooting.
4. **Given** the merchant has zero payment gateways connected, **When** the event is consumed, **Then** a `RecoveryFlow` row IS created with `state = 'blocked_no_gateway'`, no Celery task is scheduled, and a `RecoveryBlockedEvent { reason: 'no_gateway' }` is emitted so the merchant dashboard can render the "Connect a gateway to recover this order" prompt.
5. **Given** a `RiskAssessmentFinalisedEvent` with `score_type = 'preliminary'`, **When** the event is consumed, **Then** no flow is created (preliminary scores are not stable; we wait for final per spec 009 FR-001's reading of constitution v1.1.0 hybrid sync/async risk scoring).

---

### User Story 2 — Step execution with idempotent send (Priority: P1)

As the Celery worker firing step N of a flow, when I execute `recovery_send_step(flow_id, step_index)`, I send the corresponding WhatsApp template via the existing messaging service exactly once, persist the send result, and (if the flow is not terminal) schedule the next step.

**Why this priority:** without idempotent step execution, a Celery retry would double-send the customer-facing message — eroding trust and risking Meta template-rate-limit violations.

**Independent Test:** trigger step execution twice in quick succession (simulating a Celery worker retry after partial failure). Verify exactly one outbound WhatsApp message was sent, exactly one `RecoveryStep` row recorded `sent_at`, and exactly one `RecoveryStepSentEvent` was emitted.

**Acceptance Scenarios:**

1. **Given** a `RecoveryFlow` in state `pending_step_N`, **When** `recovery_send_step` is invoked with the matching `step_index`, **Then** the worker uses the flow's `step_idempotency_key_step_N` (a deterministic hash of `flow_id + step_index`) to lock the send via a Postgres advisory lock + dedupe table; only the first invocation actually calls the WhatsApp messaging service.
2. **Given** the same conditions, **When** the worker successfully sends the message, **Then** a `RecoveryStep` row is INSERTed with `sent_at = now()`, `step_index = N`, `template_key = cadence[N].template_key`, and the flow transitions to `pending_step_(N+1)` (or to a terminal state if N was the last send-step). A `RecoveryStepSentEvent` is emitted.
3. **Given** the WhatsApp service returns a recoverable error (5xx, network timeout), **When** the worker catches it, **Then** Celery's retry policy triggers (max 3 retries, exponential backoff 60s/120s/240s); the dedupe table holds the lock so the customer doesn't see a duplicate during retries.
4. **Given** the WhatsApp service returns a permanent error (template rejected, recipient opted out at Meta level), **When** the worker catches it, **Then** the flow transitions to `blocked_no_template` (template rejected) or `abandoned` (recipient opted out at Meta), a `RecoveryBlockedEvent` or `RecoveryAbandonedEvent` is emitted, and no further Celery tasks are scheduled.
5. **Given** the flow's `state` was changed to a terminal state between the Celery task being scheduled and the worker picking it up (e.g., the customer paid in the meantime), **When** the worker checks the state at lock acquisition, **Then** the worker exits without sending and emits no event (the flow is already done).

---

### User Story 3 — Payment success transitions flow to succeeded (Priority: P1)

As the gateway webhook handler (Paymob / Fawry / Instapay's `PaymentProofApprovedEvent`), when a payment is captured for an order that has an active `RecoveryFlow`, the flow transitions to `succeeded` (or `succeeded_deposit` if it's a deposit-amount payment), exactly one `RecoverySucceededEvent` is emitted, exactly one `RecoveryMonthlyRollup` increment occurs, and the Shopify order is updated additively.

**Why this priority:** this is the canonical recovered-revenue event per constitution v1.2.0 FR-010. Idempotency on this path is non-negotiable — duplicate emissions = duplicate billing.

**Independent Test:** simulate a Paymob webhook for an order with an active flow. Verify exactly one `RecoverySucceededEvent` is emitted, the `RecoveryMonthlyRollup` row for `(store_id, current_month)` was incremented exactly once, and the Shopify order has the additive tags `numu-recovered` + `numu-recovery-paymob`.

**Acceptance Scenarios:**

1. **Given** a `RecoveryFlow` in any non-terminal state AND a payment-captured event arrives for the flow's `shopify_order_id`, **When** the handler runs, **Then** the flow transitions to `succeeded` (full payment) or `succeeded_deposit` (partial ≥ deposit threshold per spec 009 CL-002) or stays in its current state (partial < deposit threshold — `abandoned_partial` per CL-002 with merchant notification).
2. **Given** the same payment event is delivered twice (webhook retry), **When** the handler runs the second time, **Then** the dedupe key `(store_id, shopify_order_id)` per constitution v1.2.0 FR-010 prevents a second transition; no second event, no second rollup increment, no second Shopify update.
3. **Given** a successful transition to `succeeded` or `succeeded_deposit`, **When** the rollup update fires, **Then** the `RecoveryMonthlyRollup` row for `(store_id, store_local_calendar_month)` is updated atomically via `INSERT ... ON CONFLICT UPDATE SET recovered_cents = recovered_cents + EXCLUDED.delta, recovered_count = recovered_count + 1`. Month boundary uses constitution v1.2.0 FR-011 store-local timezone.
4. **Given** the same transition, **When** the Shopify update fires, **Then** the existing additive-mutation client appends `numu-recovered` + `numu-recovery-{rail}` tags and `NUMU: Recovered to prepaid via {rail} on {timestamp}` note. Existing tags and notes are never overwritten (constitution Additive Shopify Mutations rule).
5. **Given** the same transition, **When** the event bus emits `RecoverySucceededEvent`, **Then** the payload contains `flow_id`, `store_id`, `shopify_order_id`, `rail`, `recovered_amount_cents`, `state` (final), and `dedupe_key = (store_id, shopify_order_id)` so spec 010's positive-trust-signal recorder can also dedupe on the same key.

---

### User Story 4 — Refund within bill cycle decrements rollup + bills (Priority: P2)

As the refund webhook handler, when a refund is processed for an order that previously triggered a `RecoverySucceededEvent`, the `RecoveryMonthlyRollup` is decremented (if within the same store-local calendar month) AND the spec 002 billing layer is notified to decrement the rev-share base for the current cycle. Refunds in a later month emit a `RecoveryRefundedEvent` that spec 002 consumes to issue a billing credit against the next invoice.

**Why this priority:** without refund handling the dashboard tile silently disagrees with the bill (the constitution v1.2.0 FR-011 failure mode that the amendment red-team caught). Marked P2 because P1 ships the success path; refund handling is the first follow-up.

**Independent Test:** trigger a successful recovery, then trigger a refund webhook for the same order in the same calendar month. Verify the `RecoveryMonthlyRollup` row's `recovered_cents` and `recovered_count` are both decremented; verify a `RecoveryRefundedEvent` is emitted with the `dedupe_key`.

**Acceptance Scenarios:**

1. **Given** an order with a `RecoveryFlow.state = succeeded` AND a refund webhook arrives in the same store-local calendar month, **When** the handler runs, **Then** the rollup is decremented atomically, the flow gains a `refunded_at` timestamp (state stays `succeeded` for audit), and a `RecoveryRefundedEvent` is emitted with `bill_cycle_match = true`.
2. **Given** the same setup but the refund arrives in a later calendar month, **When** the handler runs, **Then** the rollup is NOT decremented (it represents a closed period), the flow gains `refunded_at`, and `RecoveryRefundedEvent` is emitted with `bill_cycle_match = false` so spec 002 can issue a billing credit against the next invoice (capped at that next cycle's gross recovered revenue per constitution v1.2.0 FR-011).
3. **Given** a partial refund, **When** the handler runs, **Then** the rollup is decremented by the refund amount (not the full original recovered amount).
4. **Given** the refund is itself reversed (chargeback won by merchant, etc.), **When** the reversal handler runs, **Then** the rollup is re-incremented and a `RecoveryRefundReversedEvent` is emitted with the same dedupe semantics.

---

### Edge Cases

- **Two payment events arrive simultaneously** (e.g., customer's redirect-back and gateway webhook within milliseconds). The dedupe-key advisory lock on `(store_id, shopify_order_id)` ensures exactly one transition wins; the loser exits cleanly.
- **Customer pays via Paymob, then merchant manually marks the Shopify order as paid via a different rail.** The first event wins per dedupe; the second is logged at INFO level (the merchant action is recorded in Shopify's audit, not duplicated in the recovery flow).
- **`shop/redact` webhook arrives mid-flow.** All in-flight `RecoveryFlow` rows for the merchant transition to `terminated_uninstall`; scheduled Celery tasks check state at lock acquisition (per US2 acceptance 5) and exit. After 30 days, the rows are deleted per the existing GDPR retention pipeline.
- **`customers/redact` webhook arrives.** The customer's `RecoveryFlow` rows + child `RecoveryStep` rows are deleted within 30 days. The aggregate `RecoveryMonthlyRollup` is NOT decremented (it represents merchant-scoped revenue, not customer-scoped data).
- **Cadence override validation fails at flow-creation time** (e.g., merchant sets `step_1.delay_seconds = 60` violating CL-001's 1-hour minimum). The flow falls back to the default cadence, a WARNING is logged, and the merchant is notified once via email digest (not blocked from flow creation, since per CL-001 the validator should be on the settings save path, but defense-in-depth here in case the bypass fires).
- **Anthropic narrative service down when the spec 011 personalization is requested** for the flow's first message. Per spec 011 CL-004, falls through to spec 009's static template; no flow-state impact.
- **Shopify Admin API down when the additive update fires.** The update is queued via the existing webhook delivery service with retry/backoff; the recovery-success event is NOT held until Shopify confirms (the merchant trust depends on the dashboard tile updating fast). A monitoring alert fires if the Shopify update fails for > 5 minutes.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001:** A new `RecoveryFlow` SQLAlchemy model is added at `src/core/entities/recovery_flow.py` with the schema below (see Key Entities). Owns its own table `recovery_flows` with the migration declared as part of this spec's `/speckit.tasks`. Tenant-scoped via the existing RLS pattern (`store_id` is part of every query; `tenant_id` enforced by the RLS policy already present on every NUMU-api table).
- **FR-002:** A new `RecoveryStep` SQLAlchemy model with parent FK to `RecoveryFlow` records each scheduled / sent / failed step with full audit (template_key, channel, scheduled_for, sent_at, opened_at, delivered_at, failed_reason).
- **FR-003:** A new `RecoveryMonthlyRollup` SQLAlchemy model keyed by `(store_id, month_key)` where `month_key` is a `DATE` representing the first day of the store-local calendar month per constitution v1.2.0 FR-011. Updated atomically via `INSERT ... ON CONFLICT UPDATE`.
- **FR-004:** A new event-bus consumer subscribes to `RiskAssessmentFinalisedEvent` and creates `RecoveryFlow` rows per US1's gating predicate. Consumer registered in `src/infrastructure/events/setup.py` alongside existing handlers.
- **FR-005:** A new Celery task `recovery_send_step(flow_id, step_index)` is registered in `src/infrastructure/messaging/tasks/recovery_tasks.py`. The task acquires a Postgres advisory lock on `hashtext(f'recovery_step:{flow_id}:{step_index}')` for idempotency, checks the flow state, sends the message via the existing WhatsApp messaging service, persists `RecoveryStep`, transitions the flow, and (if non-terminal) schedules the next step via `apply_async(countdown=cadence[N+1].delay_seconds)`.
- **FR-006:** A new event-bus consumer subscribes to gateway-webhook success events (`PaymentCapturedEvent` from Paymob / Fawry / Kashier; `PaymentProofApprovedEvent` from Instapay per spec 012) and runs the US3 transition logic.
- **FR-007:** Read API endpoints exposed under `/api/v1/shopify/{store_id}/recovery/`:
  - `GET /flows?limit=50&state=...&order_by=created_at_desc` — paginated list of flows for the merchant's store.
  - `GET /flows/{flow_id}` — single flow with timeline (joined `RecoveryStep` rows ordered by `step_index`).
  - `GET /rollup?month=YYYY-MM` — current `RecoveryMonthlyRollup` row for the store + given month (defaults to current store-local month if omitted). Sub-50ms p99 by reading the indexed primary key directly.
- **FR-008:** All emitted events (`RecoveryStartedEvent`, `RecoveryStepSentEvent`, `RecoverySucceededEvent`, `RecoveryAbandonedEvent`, `RecoveryBlockedEvent`, `RecoveryRefundedEvent`, `RecoveryRefundReversedEvent`) are defined in `src/core/events/` following the existing event-class convention. Each carries `flow_id`, `store_id`, `shopify_order_id`, and the `dedupe_key = f'{store_id}:{shopify_order_id}'` for downstream consumer idempotency.
- **FR-009:** Per Principle II (Privacy by Hashing): NO new cross-store data flow is introduced. The `RecoveryFlow` table is per-tenant per-RLS; `RecoverySucceededEvent` consumers (spec 010) are responsible for hashing the customer's phone before any cross-store write — this spec emits raw store-scoped data and trusts the consumers.
- **FR-010:** Per Principle III (GDPR Recital 47):
  - **Legitimate interest:** order completion — the merchant has a contract with the customer to fulfil the order; recovery contact is necessary for that contract. Documented at `docs/gdpr/legitimate-interest-recovery.md` per `/speckit.implement` deliverable.
  - **DSAR:** customer can request a copy of their `RecoveryFlow` rows + child `RecoveryStep` rows + linked `MessageLog` entries. New endpoint `GET /api/v1/dsar/{customer_phone_hash}/recovery` (admin-authenticated; uses the existing DSAR auth pattern).
  - **Erasure:** on `customers/redact`, the customer's `RecoveryFlow` and `RecoveryStep` rows are deleted within 30 days via the existing `data_retention_task`. `RecoveryMonthlyRollup` is unaffected (merchant-scoped, not customer-scoped).
  - **Opt-out:** spec 009 CL-003's `(store_id, hashed_phone)` suppression list is owned by this backend; check the suppression at flow-creation time AND at each step send. Suppression duration: 90 days from opt-out reply.
- **FR-011:** Per constitution v1.2.0 Principle VI: `revenue_recovered_cents` is a derived sum from `RecoveryMonthlyRollup.recovered_cents` for the current month — NEVER computed from `delivery_success_rate × AOV × flagged_count` or any other derivation. Spec 002's billing path reads this same column.
- **FR-012:** Per constitution v1.2.0 FR-012: a `BillingReconciliationLedger` table is added (or, if spec 002 owns it, this spec writes to it) to record every uncharged-because-capped recovery event. The `RecoverySucceededEvent` consumer in spec 002 is responsible for the cap check; this spec emits the event with the data needed (`recovered_amount_cents`, `dedupe_key`).
- **FR-013:** State machine MUST be enforced at the database level via a CHECK constraint on `RecoveryFlow.state` AND in application code via an explicit transition table; invalid transitions raise `InvalidStateTransition` (a new exception type in `src/core/entities/recovery_flow.py`).
- **FR-014:** Cadence validation MUST happen at two surfaces: (a) at the Shopify-app settings save path (per spec 009 CL-001), via a new endpoint `POST /api/v1/shopify/{store_id}/settings/recovery_cadence` that validates and persists; (b) at flow-creation time as a defensive fallback to the default cadence with a logged WARNING.
- **FR-015:** Per Principle V (Spec-First, Tests From Spec): every acceptance scenario above translates to a test in `tests/integration/test_recovery_flow_*.py`. The test suite is part of `/speckit.tasks` and gates merge.

### Key Entities

```python
# src/core/entities/recovery_flow.py

class RecoveryFlowState(StrEnum):
    PENDING_STEP_1 = "pending_step_1"
    PENDING_STEP_2 = "pending_step_2"
    PENDING_STEP_3 = "pending_step_3"
    SUCCEEDED = "succeeded"
    SUCCEEDED_DEPOSIT = "succeeded_deposit"
    ABANDONED = "abandoned"
    ABANDONED_PARTIAL = "abandoned_partial"
    ABANDONED_BY_MERCHANT = "abandoned_by_merchant"
    TERMINATED_UNINSTALL = "terminated_uninstall"
    BLOCKED_NO_GATEWAY = "blocked_no_gateway"
    BLOCKED_NO_TEMPLATE = "blocked_no_template"

class RecoveryFlow(Base):
    __tablename__ = "recovery_flows"
    flow_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.store_id"), index=True)
    shopify_order_id: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[RecoveryFlowState] = mapped_column(
        Enum(RecoveryFlowState, values_callable=lambda e: [m.value for m in e])
    )  # values_callable required per project memory enum-details.md
    cadence: Mapped[dict] = mapped_column(JSONB)  # [{delay_seconds, template_key, fallback_action}, ...]
    current_step_index: Mapped[int] = mapped_column(default=0)
    payment_link_session_id: Mapped[UUID | None] = mapped_column(ForeignKey("payment_link_sessions.id"), nullable=True)
    recovered_amount_cents: Mapped[int | None] = mapped_column(nullable=True)
    recovered_via_rail: Mapped[str | None] = mapped_column(String(32), nullable=True)
    refunded_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("store_id", "shopify_order_id", name="uq_recovery_flow_per_order"),
        Index("ix_recovery_flow_state_created", "state", "created_at"),
    )

class RecoveryStep(Base):
    __tablename__ = "recovery_steps"
    step_id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    flow_id: Mapped[UUID] = mapped_column(ForeignKey("recovery_flows.flow_id", ondelete="CASCADE"))
    step_index: Mapped[int]
    template_key: Mapped[str] = mapped_column(String(128))
    channel: Mapped[str] = mapped_column(String(16), default="whatsapp")
    scheduled_for: Mapped[datetime]
    sent_at: Mapped[datetime | None] = mapped_column(nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(nullable=True)
    failed_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    __table_args__ = (UniqueConstraint("flow_id", "step_index", name="uq_recovery_step_per_flow_index"),)

class RecoveryMonthlyRollup(Base):
    __tablename__ = "recovery_monthly_rollups"
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.store_id"), primary_key=True)
    month_key: Mapped[date] = mapped_column(primary_key=True)  # First day of store-local calendar month
    recovered_cents: Mapped[int] = mapped_column(default=0)
    recovered_count: Mapped[int] = mapped_column(default=0)
    updated_at: Mapped[datetime] = mapped_column(default=datetime.utcnow, onupdate=datetime.utcnow)

# Added 2026-05-11 in response to spec 009 red-team finding F-019 (rollup write race) +
# spec 009 CL-006 (idempotency triple). Without this ledger, the rollup's
# (store_id, month_key) primary key prevents row duplication but NOT increment
# duplication on Celery retry — i.e., the same flow could increment the rollup
# twice if the rollup updater is retried.
class RecoveryRollupLedger(Base):
    __tablename__ = "recovery_rollup_ledger"
    store_id: Mapped[int] = mapped_column(ForeignKey("stores.store_id"), primary_key=True)
    shopify_order_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    event_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    # event_type ∈ {'succeeded', 'succeeded_deposit', 'balance_captured', 'refunded', 'refund_reversed'}
    # The composite PK prevents the same (store_id, order_id, event_type) tuple
    # from incrementing the rollup more than once even under Celery retry.
    applied_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    applied_amount_cents: Mapped[int]  # The cents delta applied to the rollup for this event
```

### Idempotent rollup update procedure (per spec 009 CL-006)

```python
# In recovery_tasks.py — runs when RecoverySucceededEvent (or sibling event) fires
def apply_to_rollup(store_id, shopify_order_id, event_type, amount_cents, store_local_month):
    with db.transaction():
        # Step 1: try to insert into the ledger; on conflict, exit silently
        try:
            db.execute(
                insert(RecoveryRollupLedger).values(
                    store_id=store_id,
                    shopify_order_id=shopify_order_id,
                    event_type=event_type,
                    applied_amount_cents=amount_cents,
                )
            )
        except IntegrityError:
            return  # Already applied — no-op (this is the idempotency guard)
        # Step 2: ledger insert succeeded → safe to mutate the rollup
        delta = -amount_cents if event_type in ('refunded',) else amount_cents
        db.execute(
            insert(RecoveryMonthlyRollup).values(
                store_id=store_id,
                month_key=store_local_month,
                recovered_cents=delta,
                recovered_count=1 if event_type in ('succeeded', 'succeeded_deposit') else 0,
            ).on_conflict_do_update(
                index_elements=['store_id', 'month_key'],
                set_=dict(
                    recovered_cents=RecoveryMonthlyRollup.recovered_cents + delta,
                    recovered_count=RecoveryMonthlyRollup.recovered_count + (1 if event_type in ('succeeded', 'succeeded_deposit') else 0),
                ),
            )
        )

# The Shopify additive-mutation path is NOT in this transaction — moves to a
# separate outbox-pattern worker per spec 009 CL-006 step 3. A Shopify 5xx
# retry never re-triggers the rollup write.
```

### Default cadence

```python
DEFAULT_RECOVERY_CADENCE = [
    {"delay_seconds": 0,      "template_key": "recovery_step_1_offer",       "fallback_action": None},
    {"delay_seconds": 7200,   "template_key": "recovery_step_2_reminder",    "fallback_action": None},
    {"delay_seconds": 86400,  "template_key": "recovery_step_3_deposit",     "fallback_action": "deposit_only"},
    # Terminal action (does not count toward 5-step ceiling per spec 009 CL-001)
    {"delay_seconds": 172800, "template_key": None,                          "fallback_action": "auto_cancel_or_hold"},
]
```

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001:** Idempotency: across 100,000 simulated duplicate-event injections (gateway webhook retries, customer redirect-back races), zero duplicate `RecoverySucceededEvent` emissions, zero duplicate `RecoveryMonthlyRollup` increments, zero duplicate Shopify order updates. Verified by integration test fixture.
- **SC-002:** State-machine integrity: zero invalid state transitions reach the database. Enforced by both DB CHECK constraint and application-level transition table; integration test injects every invalid transition and verifies `InvalidStateTransition` raised.
- **SC-003:** Read API performance: `GET /api/v1/shopify/{store_id}/recovery/rollup?month=YYYY-MM` returns p99 < 50ms server-side under load (10 RPS sustained, 100k rollup rows in the table). Verified by k6 load test.
- **SC-004:** Step send latency: median time from `RecoveryStartedEvent` emission to step 1 message sent ≤ 60 seconds. p95 ≤ 5 minutes (allows for Celery queue backpressure).
- **SC-005:** Refund-cycle alignment: a refund processed in the same store-local calendar month as the original recovery decrements the rollup atomically; cross-month refund emits `RecoveryRefundedEvent { bill_cycle_match: false }` for spec 002 to credit. Verified by store-local-timezone-edge-case tests (e.g., recovery captured 23:30 EET on month-end, refund 02:00 EET next day).
- **SC-006:** GDPR erasure: on `customers/redact` webhook for a customer with N `RecoveryFlow` rows, all rows + child `RecoveryStep` rows are deleted within 30 days (verified by the existing data-retention test harness). Aggregate `RecoveryMonthlyRollup` is unchanged.
- **SC-007:** Per Principle V: 100% of acceptance scenarios above have a corresponding integration test that runs in CI and gates merge.
- **SC-008:** Per Principle I (Stateless Shopify Boundary): zero new tables, columns, or persistent state in the `numu-payments-intelligence` Shopify app's Prisma schema as a result of this spec. Verified by a CI lint that diffs `prisma/schema.prisma` against the pre-spec baseline.

## Assumptions

- The existing NUMU-api event bus (`src/core/events/` + `src/infrastructure/events/setup.py`) supports adding new event classes + handlers without API contract changes. Confirmed by reading the existing `OrderCreatedEvent`, `OrderPaidEvent`, `PaymentProofApprovedEvent` definitions.
- The existing Celery infrastructure (Redis broker, task retry policy, advisory-lock pattern) is reusable as-is. Confirmed by reading `src/infrastructure/messaging/celery_app.py` and the existing `risk_scoring_tasks.py`.
- The existing Shopify additive-mutation client supports new tag patterns (`numu-recovered`, `numu-recovery-{rail}`) without modification. Confirmed by reading the existing `src/api/v1/routes/shopify/automation.py` tag-application logic.
- The `RiskAssessmentFinalisedEvent` exists or is added as a trivial event-class extension at `/speckit.plan` time. Spec 000's existing automation engine fires `risk_score_calculated`; this spec needs the *finalised* (not preliminary) variant. If renaming is preferred, that is a `/speckit.plan` decision.
- The store-local timezone for "this month" boundaries is available on the `Store` entity (existing `store.timezone` field; if absent, default to `Africa/Cairo` per project memory's MENA-focus).
- The `BillingReconciliationLedger` table is owned by spec 002 backend implementation. If spec 002 isn't yet implementing it at this spec's `/speckit.implement` time, this spec creates a stub table with the schema both specs need; spec 002 takes ownership when it lands.
- The `MessageLog` entity from spec 000 is the source of truth for WhatsApp delivery + read receipts; this spec consumes it via the existing read interface (no new write path).
- The `PaymentLinkSession` entity (existing) supports a 1:1 relationship with `RecoveryFlow` (a flow has at most one session). Confirmed by reading `src/api/v1/routes/shopify/payment_links.py`.

## Out of scope

- **The Shopify-app-side UI** (dashboard widget, recovery list page, per-flow timeline view). All owned by spec 009.
- **The recovery template content** (Arabic + English copy for each step). Authored under constitution V locale-parity workflow; not a backend spec concern.
- **Per-merchant template overrides** (spec 009 US5 P3). Backend support comes in a follow-up backend spec.
- **The Anthropic narrative integration** for personalized step-1 messages. Owned by spec 011 + a backend-024-claude-narrative-endpoint spec to be authored separately.
- **The Instapay panel UI** (QR rendering, screenshot upload). Owned by spec 012.
- **The spec 002 billing path** (capped-amount declaration, rev-share line item construction, billing credit issuance for cross-month refunds). This spec emits the events spec 002 consumes; spec 002 owns the consumer.
- **Cross-merchant suppression** (a customer with active flows at multiple merchants getting deduped messaging). Future spec; would extend spec 000's network reputation infrastructure.
- **SMS or email channel fallback.** WhatsApp-only in v1; channel column is reserved for future.
- **Voice-call escalation as a 4th step.** Phase 3 + spec 016 territory.
