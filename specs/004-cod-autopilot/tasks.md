# Tasks: COD Autopilot for Manual-Shipping Merchants

**Input**: Design documents from `/specs/004-cod-autopilot/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/api.md (all merged to `dev` via PR #420)

**Tests**: INCLUDED — Constitution Principle III (Spec-First, Tests From Spec) requires acceptance-criteria tests to ship in the same PR as the business logic they cover.

**Organization**: Grouped by user story (spec.md P1–P5) so each story is an independently testable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1 delivery-check · US2 ship digest · US3 fallback · US4 exception queue · US5 settings

---

## Phase 1: Setup (Templates — the Meta-approval long pole, start FIRST)

**Purpose**: Get the two WhatsApp templates into the registry pipeline and submitted to Meta so approval runs concurrently with all implementation (R-08). The send guard blocks unapproved templates, so approval is also the rollout switch.

- [ ] T001 Add `cod_ship_digest_v1` (en+ar, UTILITY, 1 QUICK_REPLY "All shipped", vars: store_name/order_count/numbered_list/capped_note, no emoji) and `order_delivery_check_v1` (en+ar, UTILITY, 3 QUICK_REPLY Received/Not yet/Refused, vars: store_name/order_number, no emoji) to `RICH_TEMPLATES` in `src/core/whatsapp_rich_templates.py`, plus send-time param entries in `EGYPTIAN_TEMPLATES` in `src/core/interfaces/services/messaging_service.py`
- [ ] T002 Create Alembic data migration seeding both templates as PENDING rows in `whatsapp_templates` in `alembic/versions/<rev>_seed_cod_autopilot_templates.py` (clone `20260601_002000_seed_rich_wa_templates.py`; revision id ≤32 chars)
- [ ] T003 Verify `scripts/submit_platform_whatsapp_templates.py` picks up the two new registry entries; submit to Meta on staging WABA and record submission ids in the PR description
- [ ] T004 [P] Extend `tests/unit/core/whatsapp/test_rich_templates.py` to cover both new templates (structure, button payload prefixes `shipall:`/`dlvyes:`/`dlvnot:`/`dlvref:`, no-emoji rule)

**Checkpoint**: Templates submitted — Meta approval clock running while everything below proceeds.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, source attribution, payload parsing, and send plumbing that every story depends on.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T005 Create Alembic migration `alembic/versions/<rev>_add_cod_autopilot_tables.py` for `whatsapp_delivery_checks` and `whatsapp_ship_digests` per data-model.md §1–2, with ENABLE+FORCE RLS and full policy set in the SAME migration (clone `_add_rls_for_table` from `20260524_010000_add_whatsapp_optin_scheduled_dl.py:148`); indexes on `next_attempt_at`, `outcome`, `assumed_delivered_due_at`; UNIQUE `order_id` and UNIQUE `(store_id, digest_date)`; `down()` implemented
- [ ] T006 [P] Create `WhatsAppDeliveryCheckModel` in `src/infrastructure/database/models/tenant/whatsapp_delivery_check.py` (columns per data-model.md §1)
- [ ] T007 [P] Create `WhatsAppShipDigestModel` in `src/infrastructure/database/models/tenant/whatsapp_ship_digest.py` (columns per data-model.md §2)
- [ ] T008 [P] Create `WhatsAppDeliveryCheckRepository` in `src/infrastructure/repositories/whatsapp_delivery_check_repository.py` (async: get_by_order, list_due_sends, list_fallback_due, list_exceptions, create, update — typed, MyPy strict)
- [ ] T009 [P] Create `WhatsAppShipDigestRepository` in `src/infrastructure/repositories/whatsapp_ship_digest_repository.py` (async: get_open_for_phone, get_by_store_date, create, mark_processed)
- [ ] T010 Add optional `source: str | None = None` to `Order.transition_to()` appending `"source"` into status_history entries in `src/core/entities/order.py` (R-05; absent key = manual, backward-compatible)
- [ ] T011 Thread `source` through `UpdateOrderStatusDTO` and `UpdateOrderStatusUseCase.execute` in `src/application/use_cases/orders/update_order_status.py`, and gate `_record_network_event` so the `delivery` network event fires ONLY when source is `customer_confirmed` or absent (manual); on `assumed_delivered` set `order.metadata["network_delivery_skipped"]="assumed_delivered"` instead (R-06)
- [ ] T012 In `UpdateOrderStatusUseCase`, on any terminal transition (delivered/returned/cancelled/refunded) mark an open `whatsapp_delivery_checks` row for the order `superseded` and clear `next_attempt_at` (FR-018; repository injected as optional dependency so existing callers keep working)
- [ ] T013 [P] Extend `parse_quick_reply_action` valid-action set with `shipall`/`dlvyes`/`dlvnot`/`dlvref` in `src/application/services/order_confirmation_service.py` (payload grammar `<action>:<subdomain>/<uuid>` unchanged)
- [ ] T014 [P] Add pref keys `cod_autopilot_digest` and `delivery_check` to `_WA_PREF_KEYS` in `src/infrastructure/events/handlers/whatsapp_notification_handler.py` (R-10; default enabled)
- [ ] T015 [P] Add `send_ship_digest(...)` and `send_delivery_check(...)` template-send methods to `src/infrastructure/external_services/whatsapp/messaging_service.py` using the existing `_build_template_message` quick-reply component path (`:231-247`), returning `MessageResult`
- [ ] T016 [P] Add `cod_autopilot` settings defaults + typed reader helper (`get_cod_autopilot_settings(store_settings) -> CodAutopilotConfig`) in `src/application/services/cod_autopilot_service.py` (new module; defaults per data-model.md §4) with market-registry timezone resolution for `digest_hour` matching (R-02, default `Africa/Cairo`)
- [ ] T017 Unit tests for foundational behavior: source attribution in status_history + R-06 network-event gating in `tests/unit/use_cases/test_update_order_status_network.py` (additions), new-action parsing in `tests/unit/services/test_order_confirmation_service.py` (additions), guard pref keys in `tests/unit/core/whatsapp/test_send_guard.py` (additions)

**Checkpoint**: Foundation ready — user stories can begin (US1/US2 in parallel).

---

## Phase 3: User Story 1 — Customer confirms delivery, order closes itself (P1) 🎯 MVP

**Goal**: N days after `shipped`, the customer gets Received/Not yet/Refused buttons; **Received** → delivered + COD paid + full-weight trust signal, zero merchant touch (FR-010..FR-015).

**Independent Test**: quickstart.md §5 — ship a COD order, back-date `shipped_at`, run the beat task, simulate each button tap via webhook POST, verify status/payment/timeline-source/network outcomes.

### Tests for User Story 1

- [ ] T018 [P] [US1] Unit tests for delivery-check lifecycle (row creation eligibility per FR-001, due-send selection, attempt increment, response state machine, idempotent duplicate taps) in `tests/unit/services/test_cod_autopilot_delivery_checks.py`
- [ ] T019 [P] [US1] Integration tests for webhook button actions `dlvyes`/`dlvnot`/`dlvref` (phone match, RLS-bypass session → `narrow_to_tenant`, duplicate-tap ack-only, ack replies) in `tests/integration/whatsapp/test_autopilot_webhook_actions.py`

### Implementation for User Story 1

- [ ] T020 [US1] Implement delivery-check lifecycle in `src/application/services/cod_autopilot_service.py`: `create_due_checks` (scan newly-shipped eligible COD orders — autopilot enabled, no courier shipment, delay elapsed → insert rows), `send_due_checks` (guard-gated send via `send_delivery_check`, `event_tag=dlvcheck:{order_id}:{attempt}`, attempt/next_attempt_at bookkeeping, mark `response_exhausted` at max attempts), `handle_delivery_response(action, payload, from_phone)` (Received → `UpdateOrderStatusUseCase(delivered, source=customer_confirmed)` + outcome `delivered_confirmed`; Not yet → schedule retry; Refused → outcome `exception`/reason `refused`; late taps per FR-017; localized ack via `send_text_message`)
- [ ] T021 [US1] Create beat task `tasks.cod_autopilot_delivery_checks` in `src/infrastructure/messaging/tasks/cod_autopilot_tasks.py` (clone `cod_auto_rto_task` structure: sync wrapper + `_run_async`, `bind=True, max_retries=2, default_retry_delay=300`, `enable_rls_bypass` scan / `narrow_to_tenant` writes / reset in `finally`, batch cap 500) calling `create_due_checks` + `send_due_checks`
- [ ] T022 [US1] Register the task module in `imports` and add hourly `crontab(minute=20)` beat entry in `src/infrastructure/messaging/celery_app.py`; assert registration in `tests/api/test_celery_beat_registration.py`
- [ ] T023 [US1] Wire `dlvyes`/`dlvnot`/`dlvref` into `_action_handlers` in `src/api/v1/routes/webhooks/whatsapp.py` (each wrapped try/except so webhook always returns 200), delegating to `handle_delivery_response`
- [ ] T024 [P] [US1] Unit tests for the beat task (eligibility, idempotent re-run, guard-blocked send → row still progresses toward fallback) in `tests/unit/tasks/test_cod_autopilot_tasks.py`

**Checkpoint**: MVP — delivered+paid closes hands-free for responsive customers; refusals flagged internally (queue UI arrives in US4).

---

## Phase 4: User Story 2 — Merchant marks the whole day shipped with one tap (P2)

**Goal**: One daily digest listing confirmed orders; **All shipped** tap or numeric-exceptions reply moves them to `shipped` (FR-003..FR-009).

**Independent Test**: quickstart.md §4 — confirmed COD orders + digest task → one digest row + message; simulate `shipall:` tap and `"except 2"` free-text; verify transitions, skip semantics, replay ack-only, garbage-text no-op.

### Tests for User Story 2

- [ ] T025 [P] [US2] Unit tests for the exceptions-reply grammar (keywords `except`/`الا`/`ماعدا`/`بدون`, ASCII + Arabic-Indic digits, `,`/`،`/space separators, out-of-range → reject, ambiguity → no-op ALWAYS per FR-007) in `tests/unit/services/test_autopilot_reply_parsing.py`
- [ ] T026 [P] [US2] Integration tests for `shipall` tap and merchant free-text replies (sender-phone match vs digest `merchant_phone`, processed-once FR-008, expired digest ignored, help reply on garbage) in `tests/integration/whatsapp/test_autopilot_webhook_actions.py` (additions)

### Implementation for User Story 2

- [ ] T027 [US2] Implement digest build/send in `src/application/services/cod_autopilot_service.py`: `send_daily_digests` (stores where autopilot enabled ∧ `digest_hour` == current store-local hour ∧ ≥1 eligible confirmed order ∧ no digest row today; oldest-first cap 10 + `capped_count` per R-09; guard-gated send via `send_ship_digest` with `event_tag=digest:{date}`; insert digest row with `order_items`/`merchant_phone`/`expires_at=+48h`)
- [ ] T028 [US2] Implement digest response handling in `cod_autopilot_service.py`: `handle_shipall(payload, from_phone)` and `handle_digest_text_reply(text, from_phone)` — verify sender matches `merchant_phone`, enforce `processed_at` set-exactly-once, parse exceptions grammar (T025 module), transition listed-minus-excepted orders still CONFIRMED via `UpdateOrderStatusUseCase(shipped, source=merchant_digest)` skipping moved ones (FR-005), reply localized summary / help message
- [ ] T029 [US2] Create beat task `tasks.cod_autopilot_send_digests` in `src/infrastructure/messaging/tasks/cod_autopilot_tasks.py` (hourly `crontab(minute=5)`) + beat entry in `celery_app.py` + registration assertion in `tests/api/test_celery_beat_registration.py`
- [ ] T030 [US2] Wire `shipall` into `_action_handlers` and add the merchant free-text branch (text message from a phone matching an open unexpired digest's `merchant_phone` → `handle_digest_text_reply`; all other text untouched) in `src/api/v1/routes/webhooks/whatsapp.py`
- [ ] T031 [P] [US2] Unit tests for digest task (hour matching per market timezone, one-per-day uniqueness, zero-orders skip FR-003, cap semantics FR-009) in `tests/unit/tasks/test_cod_autopilot_tasks.py` (additions)

**Checkpoint**: US1 + US2 together = full loop for responsive customers, one merchant tap per day.

---

## Phase 5: User Story 3 — Silent orders still close: retries + assumed-delivered fallback (P3)

**Goal**: Exhausted, unanswered checks auto-close as `delivered` with explicit `assumed_delivered` marking after the configured window; NEVER a full-weight trust signal (FR-016..FR-019).

**Independent Test**: quickstart.md §6 — exhaust attempts, back-date `assumed_delivered_due_at`, run sweep; verify delivered + paid + `source: assumed_delivered` + `network_delivery_skipped` + no network increment; verify RTO-precedence and late-tap supersede.

### Tests for User Story 3

- [ ] T032 [P] [US3] Unit tests for the fallback sweep (due selection, still-SHIPPED guard, metadata idempotency stamp, RTO/cancel precedence FR-018, autopilot-disabled skip FR-023, R-06 network exclusion asserted) in `tests/unit/tasks/test_cod_autopilot_tasks.py` (additions)

### Implementation for User Story 3

- [ ] T033 [US3] Implement `close_assumed_delivered` in `src/application/services/cod_autopilot_service.py`: rows `outcome='response_exhausted'` (or never-sendable) past `assumed_delivered_due_at`, order still SHIPPED → stamp `order.metadata["autopilot_assumed_delivered_at"]` BEFORE the use case (clone `cod_auto_rto_task.py:177` idempotency), then `UpdateOrderStatusUseCase(delivered, reason="autopilot_assumed_delivered", source="assumed_delivered")`; outcome → `assumed_delivered`
- [ ] T034 [US3] Create beat task `tasks.cod_autopilot_assumed_delivered` (daily `crontab(hour=3, minute=30)` — AFTER the 03:00 RTO sweep, R-02/R-07) in `cod_autopilot_tasks.py` + beat entry + registration assertion
- [ ] T035 [US3] Implement late-response supersede in `handle_delivery_response`: taps on `response_exhausted` rows resolve per FR-017 (Received → full-confidence delivered; Refused → exception); taps on terminal orders → `late_contradiction` flag only, order untouched (data-model §1 state machine)

**Checkpoint**: 100% of Autopilot orders reach a terminal state (SC-006).

---

## Phase 6: User Story 4 — Merchant reviews only the exceptions (P4)

**Goal**: Dashboard view of refused / response-exhausted orders with reason + age, resolvable via existing actions (FR-021).

**Independent Test**: quickstart.md §5 refused path → order appears in `GET .../autopilot-exceptions`; resolve via existing status endpoint → disappears + automation cleared.

### Tests for User Story 4

- [ ] T036 [P] [US4] API tests for exceptions list + resolve (RLS isolation across tenants, empty state, dismiss 404 on no flag, clearing on order status change) in `tests/api/test_autopilot_exceptions.py`

### Implementation for User Story 4

- [ ] T037 [US4] Add `GET /stores/{store_id}/orders/autopilot-exceptions` (paginated, joins order summary, per contracts/api.md §2) and `POST .../autopilot-exceptions/{order_id}/resolve` (`action: dismiss` → `exception_resolved_at`) in `src/api/v1/routes/stores/orders.py` with Pydantic response schemas in `src/api/v1/schemas/tenant/orders.py`
- [ ] T038 [US4] (separate repo: `numo-merchant-hub`) Add "Needs attention" exceptions view to `src/pages/Orders.tsx` + `src/services/orderApi.ts` consuming the two endpoints — tracked here for feature completeness, implemented in the hub repository

**Checkpoint**: "You only touch exceptions" is now literally true in the UI.

---

## Phase 7: User Story 5 — Merchant controls Autopilot from settings (P5)

**Goal**: Single enable switch + digest_hour / delivery-check delay / assumed-delivered window with validated bounds and immediate-off semantics (FR-022/FR-023).

**Independent Test**: quickstart.md §2 — PATCH settings, verify persistence + bounds 422s; disable mid-flight → pending sends skipped, no order changes.

### Tests for User Story 5

- [ ] T039 [P] [US5] API tests for GET/PATCH cod-autopilot settings (defaults on missing section, bounds validation, `digest_deliverable` computation, disable-immediate skip asserted against beat-task run) in `tests/api/test_cod_autopilot_settings.py`

### Implementation for User Story 5

- [ ] T040 [US5] Add `CodAutopilotResponse`/`UpdateCodAutopilotRequest` schemas (bounds per data-model.md §4, read-only `digest_deliverable` + `auto_rto_days`) in `src/api/v1/schemas/tenant/settings.py`
- [ ] T041 [US5] Add `GET/PATCH /{store_id}/settings/cod-autopilot` with `_COD_AUTOPILOT_DEFAULTS` read-merge-write in `src/api/v1/routes/stores/settings.py` (clone cod-trust pattern `:513-560`)
- [ ] T042 [US5] (separate repo: `numo-merchant-hub`) COD Autopilot settings card (toggle + three inputs + deliverability warning) in the hub settings pages — tracked here, implemented in the hub repository

**Checkpoint**: All five stories independently functional.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T043 [P] Add `whatsapp_delivery_checks` to the customer-redaction/erasure sweep (GDPR erasure path, research.md R-13.3) in the existing redaction flow, with test coverage
- [ ] T044 [P] MyPy strict + Ruff clean on all new modules (`mypy --strict src/`); fix any violations (Constitution IV)
- [ ] T045 Run full quickstart.md §1–6 locally end-to-end; fix discrepancies between docs and behavior
- [ ] T046 [P] Update `docs/` (merchant-facing: how Autopilot works, defaults, template approval prerequisite; ops-facing: beat schedule map, runbook for stuck digests/checks)
- [ ] T047 After Meta approval lands: staging smoke test per quickstart.md §7 (real WABA, en+ar rendering, both flows) and record results in the PR

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: none — start immediately (T003 submission is the schedule's long pole)
- **Foundational (Phase 2)**: independent of Phase 1 completion except T015 needs T001 template names — BLOCKS all stories
- **US1 (Phase 3)** and **US2 (Phase 4)**: independent of each other after Phase 2 — parallelizable
- **US3 (Phase 5)**: depends on US1 (delivery-check rows/attempt state)
- **US4 (Phase 6)**: depends on US1 (exception flags exist); UI (T038) after T037
- **US5 (Phase 7)**: depends only on Phase 2 (T016 defaults helper); can run parallel to US1–US4
- **Polish (Phase 8)**: T043/T044/T046 anytime after their targets exist; T045 after US1–US3; T047 after Meta approval + US1/US2

### Within-story ordering

Tests may be written first (fail-first) or alongside; models → services → tasks/endpoints → webhook wiring. `cod_autopilot_tasks.py` and `test_cod_autopilot_tasks.py` are shared files across US1/US2/US3 — tasks touching them within one story are sequential, across stories append-only.

### Parallel Opportunities

```text
After Phase 2 completes:
  Developer A: T018–T024 (US1)      # MVP path
  Developer B: T025–T031 (US2)
  Developer C: T039–T042 (US5) then T036–T038 (US4 after US1 lands)
Within Phase 2: T006, T007, T008, T009, T013, T014, T015, T016 all [P] after T005/T010/T011 land
```

---

## Implementation Strategy

**MVP first (US1 only)**: Phase 1 (submit templates immediately) → Phase 2 → Phase 3 → validate via quickstart §5 → this alone automates delivered+paid for responsive customers and is demoable with a dev-approved template.

**Incremental delivery**: +US2 (one-tap shipped) → +US3 (100% closure) → +US4/US5 (queue + controls) → Phase 8 polish → staging smoke on Meta approval. Each checkpoint is deployable behind the per-store `enabled` flag (default off, FR-002) — merging early phases to `dev` is safe.

**Total**: 47 tasks (T001–T047) · US1: 7 · US2: 7 · US3: 4 · US4: 3 · US5: 4 · Setup: 4 · Foundational: 13 · Polish: 5. Two tasks (T038, T042) are cross-repo pointers into `numo-merchant-hub`.
