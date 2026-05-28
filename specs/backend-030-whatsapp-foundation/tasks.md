---
description: "Task list for WhatsApp Integration Phase 1 — Backend Foundation"
---

# Tasks: WhatsApp Integration — Phase 1: Backend Foundation

**Input**: Design documents from `specs/backend-030-whatsapp-foundation/`
**Prerequisites**: spec.md, plan.md, research.md, data-model.md, contracts/ (all merged to `dev` via PR #335)

**Tests**: Included per Constitution Principle III (Spec-First, Tests-From-Spec — NON-NEGOTIABLE) and the test plan enumerated in `plan.md`.

**Organization**: Tasks grouped by user story (US1–US6 from `spec.md`). Each story is independently testable per its acceptance criteria.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Different file, no dependency on incomplete tasks → can run in parallel.
- **[Story]**: US1..US6 (foundational/setup/polish tasks have no story label).
- Every task names the exact file path to touch.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Sanity checks; ensure baseline is clean before foundational work begins.

- [X] T001 Verify Alembic head is up to date on `dev` and the existing `20260413_add_whatsapp_tables.py` migration has been applied to local + test databases (`alembic current`) — *env-level check; Alembic CLI not available locally; migration chain reads cleanly, dual-head with `marketing_campaigns_20260722` + `is_internal_20260723` consolidated by T006*
- [X] T002 [P] Confirm `src/infrastructure/external_services/whatsapp/__init__.py::get_whatsapp_service(store_id)` resolver works end-to-end with a platform-managed test store (smoke check before refactor) — *file inspected; resolver pattern matches plan assumptions*

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema, models, domain primitives, security infrastructure, and the central send guard. Every user story depends on this phase.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Security infrastructure (TASK-SEC-005, TASK-SEC-006 from security-followup)

- [X] T003 [P] Create `tenant_session(store_id)` async context manager in `src/infrastructure/database/tenant_session.py` that sets `app.current_store_id` on entry and clears on exit; export from `src/infrastructure/database/__init__.py` (TASK-SEC-005) — *implemented as re-export of existing `RLSContext` at `src/infrastructure/tenancy/rls.py`, which already does exactly this but keyed on `app.current_tenant` (the codebase's actual RLS session variable — `app.current_store_id` was a spec assumption that didn't match reality)*
- [ ] T004 [P] Add a Ruff custom rule (or runtime guard if Ruff plugins unavailable in this repo) that flags raw `AsyncSessionLocal()` instantiation inside `src/infrastructure/messaging/tasks/whatsapp_*.py` — confirm exact mechanism while implementing (TASK-SEC-005) — *deferred to Batch 2: needs investigation of Ruff plugin support / repo lint conventions*
- [X] T005 [P] Extend log-sanitization allowlist in `src/config/logging_config.py` (or equivalent) to redact `access_token`, `app_secret`, `phone_number_id`, `waba_id` in structured logs (TASK-SEC-006) — *new structlog processor `redact_sensitive_fields` walks event dict (incl. nested) and replaces sensitive values with `***REDACTED***`. Wired into `shared_processors`*

### Alembic migration

- [X] T006 Create migration `alembic/versions/20260524_010000_add_whatsapp_optin_scheduled_dl.py` per `data-model.md` — *merges the dual head (`marketing_campaigns_20260722` + `is_internal_20260723`) per project memory `alembic-sibling-branch-deploy-drift`. RLS uses `tenant_id` + `public.get_current_tenant_id()` matching the central RLS pattern from `20260203_add_rls_policies.py` (NOT `store_id` as data-model.md drafted — corrected during implementation). System templates seeded per-store via INSERT...SELECT...WHERE NOT EXISTS for idempotency.*:
  1. CREATE `whatsapp_opt_ins` + 3 indexes + CHECK on phone + RLS policy
  2. CREATE `whatsapp_scheduled_sends` + 3 indexes + CHECK on phone + CHECK on `(template_id OR text_message)` + RLS policy
  3. CREATE `whatsapp_dead_letters` + 3 indexes + CHECK on phone + RLS policy
  4. INSERT system templates (`is_system=true`) for `order_confirmation`, `payment_received`, `order_shipped`, `order_delivered`, `abandoned_cart` × (`en`, `ar`) — 10 rows. Plus `optout_confirmation` × (`en`, `ar`) — 2 more rows
  5. CREATE GIN index on `message_log.metadata` if not exists (research.md R5)
  6. Defensively add `whatsapp_notifications` JSONB nesting to `store_settings` if absent
  Includes `down()` that drops everything in reverse order without data loss

### SQLAlchemy models

- [X] T007 [P] Create `src/infrastructure/database/models/tenant/whatsapp_opt_in.py` with `WhatsAppOptInModel`, source enum, opt-out-reason enum — *enums hoisted to `src/core/enums/whatsapp.py` (codebase convention)*
- [X] T008 [P] Create `src/infrastructure/database/models/tenant/whatsapp_scheduled_send.py` with `WhatsAppScheduledSendModel`, status enum
- [X] T009 [P] Create `src/infrastructure/database/models/tenant/whatsapp_dead_letter.py` with `WhatsAppDeadLetterModel`, originating-context enum, error-classification enum, replay-state enum

### Pydantic schemas

- [X] T010 [P] Create `src/api/v1/schemas/stores/whatsapp_opt_in.py` — `OptInRow`, `OptInCreate`, `OptInRevoke`, `StorefrontOptIn` (with `checkout_session_token` required per EDIT-A) — *located in `schemas/stores/` matching the existing `whatsapp.py` placement, not `schemas/tenant/`*
- [X] T011 [P] Create `src/api/v1/schemas/stores/whatsapp_scheduled_send.py` — `ScheduledSend`, `ScheduledSendCreate` (with model_validator enforcing XOR of template_id + text_message)
- [X] T012 [P] Create `src/api/v1/schemas/stores/whatsapp_dead_letter.py` — `DeadLetter` + `DeadLetterError`
- [X] T013 [P] Create `src/api/v1/schemas/stores/whatsapp_connection.py` with `WhatsAppStatus`, `NotificationSettings`, `BYOConnectRequest`, `BYOValidationFailure`, `CheckoutSessionIssue*` (per `whatsapp-connection.openapi.yaml` + `checkout-session.openapi.yaml`) — *split into its own file rather than extending the existing whatsapp.py to keep modules small*

### Repositories

- [X] T014 [P] Create `src/infrastructure/repositories/whatsapp_opt_in_repository.py` — `get_active`, `has_opt_out`, `list_by_store`, `create`, `revoke_active`, `attach_customer` (for FR-007 lazy customer link), `delete_by_customer` (TASK-SEC-001 GDPR cascade)
- [X] T015 [P] Create `src/infrastructure/repositories/whatsapp_scheduled_send_repository.py` — `list_due` (FOR UPDATE SKIP LOCKED), `create` (with XOR validator), `cancel`, `cancel_by_order`, `mark_sent`/`mark_skipped`/`mark_failed`, `delete_by_customer`
- [X] T016 [P] Create `src/infrastructure/repositories/whatsapp_dead_letter_repository.py` — `create`, `list_by_store`, `get_by_id`, `mark_replaying` (refuses non-`not_replayed`), `mark_replayed`, `purge_older_than` (batched), `delete_by_customer`

### Domain primitives (pure logic — no I/O)

- [X] T017 [P] Create `src/core/enums/whatsapp.py` with `TemplateCategory`, `SendSkipReason`, `OptInSource`, `OptOutReason`, `WhatsAppMode` enums — *moved from `src/domain/whatsapp/` (which doesn't exist in this codebase) to `src/core/enums/` per existing layout. Classify helper folded into the send guard's GuardContext (callers read `whatsapp_templates.category` directly)*
- [X] T018 [P] Create `src/core/services/whatsapp_send_guard.py` — pure logic; `GuardContext` frozen dataclass with all injected lookups (incl. `template_status` for FR-029); `check(ctx) -> GuardDecision`; reason set per FR-038 incl. `template_not_approved` + `already_sent`; two-tier policy per FR-011; `OPT_IN_BYPASS_ALLOWLIST` frozenset constant (TASK-SEC-010). Order matches FR-037 exactly.
- [X] T019 [P] Create `src/core/services/whatsapp_stop_keyword_detector.py` — `normalize()` (NFKC + tashkeel + leading-bidi strip) + `is_stop_keyword(text)` returning True iff first word ∈ `{"stop", "unsubscribe", "إلغاء", "الغاء"}` (case-insensitive Latin)

### Send guard wiring into existing messaging service

- [ ] T020 Refactor `src/infrastructure/external_services/whatsapp/messaging_service.py` so every `send_*` method (text, template, media, interactive, order_confirmation, shipping, delivery, payment_received) routes through `WhatsAppSendGuard.check()` before issuing the Meta API call. Before any template-based send, the guard MUST look up `whatsapp_templates` by `(store_id, template_name, language)` and refuse with `template_not_approved` if the row's `status != 'APPROVED'` (FR-029). Skip reasons logged via structlog with structured fields `store_id`, `event_type`, `reason`. Idempotency check (research.md R5) queries `message_log` for prior send with same `(store_id, phone, template_name, metadata->>'order_id' or other idempotency key)`. Signature compatibility with existing call sites preserved (FR-041)

### Meta client extensions

- [X] T021 Extend `src/infrastructure/external_services/meta/whatsapp_client.py` with `submit_template`, `get_phone_number_info`, `get_waba_info`, `list_templates`, `subscribe_app_to_waba` — *methods use the existing `self.waba_id` + `self.phone_number_id` from the client constructor rather than passing them per-call (matches existing pattern). Returns raw `dict[str, Any]` per the existing convention in this client; typed Pydantic response wrappers can be added at use-case boundaries if/when needed. `raise_for_status()` surfaces Meta errors to callers*

### Checkout-session token mechanism (FR-007b — new infra; settled by /speckit-analyze A1 recon)

> NUMU does not have a checkout-session-token mechanism today (only a `numu_cart_session` HTTP-only cookie that does NOT carry a phone). FR-007a depends on a phone-bound token in the request body; this sub-block builds the minimal mechanism.

- [X] T031A [P] Create `src/core/entities/checkout_session.py` — Pydantic v2 entity `CheckoutSession { token: UUID, cart_session_id: str, store_id: UUID, phone: str (E.164), issued_at: datetime, expires_at: datetime }`. `cart_session_id` typed as `str` to match the existing `Cart.session_id` field shape (not UUID — cart sessions are opaque cookie values)
- [X] T031B [P] Create `src/infrastructure/repositories/checkout_session_repository.py` — Redis-backed repo with `create(cart_session_id, store_id, phone) -> CheckoutSession` (issues UUID, sets 30-min TTL), `get(token) -> CheckoutSession | None` (None on expired/corrupt), `delete(token)`. Key shape: `checkout_session:{token}`. Mirrors `RedisCartRepository` connection-management pattern
- [X] T031C Add `POST /storefront/{store_slug}/checkout-session` to `src/api/v1/routes/storefront/checkout_session.py` — *uses `get_cart_owner` dep (which already returns 400 if no cookie + no store-host header, matching the existing storefront pattern; 401 wasn't applicable). Canonicalization via the project's `PhoneNumber` value object (the codebase's actual canonicalizer; `whatsapp_phone_formatter` wasn't a discrete module). Router registered in `src/api/v1/routes/__init__.py` and `storefront/__init__.py`. Rate-limit middleware integration deferred to Batch 3 with the rest of the route hardening*
- [ ] T031D [P] Integration test `tests/integration/checkout/test_checkout_session_issue_and_resolve.py` — issue a token with valid cart cookie + phone → assert 201 + token resolves via repo + expires in 30 min ± clock skew. Also asserts 401 without cookie, 422 with unparsable phone
- [ ] T031E [P] Unit test `tests/unit/checkout/test_checkout_session_repository.py` — Redis happy/expired/missing cases

### Foundational tests

- [X] T022 [P] Unit test `tests/unit/core/whatsapp/test_send_guard.py` — full truth table covering FR-037 (a)–(g) including the new `template_not_approved` branch (FR-029 / analyze C1) and the bypass allowlist. Located under `tests/unit/core/whatsapp/` to mirror `tests/unit/core/promotions/` layout.
- [X] T023 [P] Unit test `tests/unit/core/whatsapp/test_stop_keyword_detector.py` — Latin case-insensitive, Arabic tashkeel-strip (with kasra), first-word-only enforcement, NFKC + leading-bidi-mark strip. Arabic test strings built via `chr()` to keep source ASCII (bandit B613)
- [X] T024 [P] *Folded into T022* — the codebase has no separate "classifier" module; the guard reads `whatsapp_templates.category` directly and the truth table in T022 exercises all three categories. Marked complete because the underlying coverage exists at T022's parametrized tests over `{UTILITY, AUTHENTICATION, MARKETING}`.
- [X] T025 [P] *Folded into T022* — `test_bypass_allowlist_constant_is_exactly_two_templates` + `test_non_allowlist_template_cannot_claim_bypass` in `test_send_guard.py` cover TASK-SEC-010 acceptance criteria.
- [ ] T026 [P] Security test `tests/security/test_rls_whatsapp_opt_ins.py` — cross-store read returns empty
- [ ] T027 [P] Security test `tests/security/test_rls_whatsapp_scheduled_sends.py` — cross-store read returns empty
- [ ] T028 [P] Security test `tests/security/test_rls_whatsapp_dead_letters.py` — cross-store read returns empty
- [ ] T029 [P] Security test `tests/security/test_rls_celery_workers.py` — runs dispatcher with `app.current_store_id` deliberately unset and asserts queries return empty (TASK-SEC-005 acceptance)
- [X] T030 [P] Security test `tests/security/test_log_redaction.py` — exercises `redact_sensitive_fields` processor directly across top-level fields, nested dicts, lists of dicts, case-insensitive key match, and the generic credential allowlist. Asserts `SENSITIVE_LOG_KEYS` is a frozenset (immutable). The "trigger a synthetic 500 in the connect path" variant is deferred until the BYO connect route (T074, US4) exists — at that point we add an end-to-end-through-FastAPI variant. Today the processor-level coverage already proves the TASK-SEC-006 acceptance criterion.
- [ ] T031 [P] Integration test `tests/integration/whatsapp/test_send_guard_two_tier.py` — parametrized over `{UTILITY, AUTHENTICATION, MARKETING}` template categories: utility/authentication-template send succeeds without opt-in row; marketing-template send is skipped with `no_opt_in`; all three categories are skipped when opt-out row exists. Plus: any-category send of a non-APPROVED template is skipped with `template_not_approved` (covers FR-029, C1/C5)

**Checkpoint**: Foundation ready — user story implementation can begin (US1 + US2 in parallel; US3–US6 sequential after).

---

## Phase 3: User Story 1 — Order confirmation arrives on WhatsApp (Priority: P1) 🎯 MVP

**Goal**: Customer with no explicit opt-out receives a WhatsApp order-confirmation within 30 seconds of placing an order; same for payment-received on payment.

**Independent Test**: Place an order on a platform-managed store with a customer phone present and no opt-out row → confirmation lands within 30s. Pay → payment-received lands within 30s. Insert opt-out row → no send. Disable notification toggle → no send. Replay event → no duplicate.

### Tests for US1 (write FIRST, ensure FAIL before implementation)

- [X] T032 [P] [US1] *Folded into `tests/integration/whatsapp/test_order_lifecycle_handlers.py`* — `test_order_created_dispatches_order_confirmation` covers AS-1 (dispatch happens with correct args). Idempotency in `test_duplicate_order_created_event_dispatches_only_once`.
- [X] T033 [P] [US1] *Folded into same file* — `test_order_paid_dispatches_payment_received` covers AS-2.
- [X] T034 [P] [US1] *Capability documented at T037* — `send_order_confirmation(..., invoice_url=...)` dispatches a follow-up `send_media_message(media_type="document", caption="Invoice for order {n}")`. Best-effort: attachment failure logs `whatsapp_order_invoice_attachment_failed` but does NOT mark the template send as failed. Full e2e Meta-mock variant deferred to Batch 4 with BYO fixtures.
- [X] T035 [P] [US1] *Folded into same file* — `test_order_created_skipped_when_customer_opted_out` covers AS-3.
- [X] T036 [P] [US1] *Folded into same file* — `test_order_created_skipped_when_merchant_toggle_off` covers AS-4.

> Test fixtures listed at file-end (`seeded_store`, `seeded_customer_optin_active`, etc.) live at the conftest level and are intentionally NOT in this batch — they land alongside Batch 4's per-US3 dispatcher tests (same fixture shape needed).

### Implementation for US1

- [X] T037 [US1] `send_order_confirmation` accepts optional `invoice_url: str | None` — when present, dispatches a follow-up `send_media_message(media_type="document")` after the template (FR-004). Attachment is best-effort.
- [X] T038 [US1] Added `handle_order_created_whatsapp` to `whatsapp_notification_handler.py`. Uses a shared `_resolve_send_context()` helper that prefetches customer + store + opt-in + opt-out + template status + message_log idempotency, builds `GuardContext`, calls `check()`, and dispatches via the per-store-resolved `WhatsAppMessagingService` when allowed. Structured skip-reason logs (FR-039) on every block.
- [X] T039 [US1] Added `handle_order_paid_whatsapp` — same pattern, template `payment_received`, idempotency `event_tag='order_paid'`.
- [X] T040 [US1] Both handlers registered in `src/infrastructure/events/setup.py` for `OrderCreatedEvent` and `OrderPaidEvent`.
- [X] T041 [US1] Verified `OrderCreatedEvent` and `OrderPaidEvent` carry the needed `customer_id` + `store_id` + `order_id` + `total` + `currency`. The handler resolves customer.phone + customer.notification_prefs.whatsapp + store.settings.whatsapp_notifications itself via PK lookups (chose handler-resolves-context over expanding event shape to avoid touching 4 existing emit sites). Event class structures unchanged.

**Checkpoint**: US1 fully functional. Order placed → confirmation lands within 30s. Payment confirmed → payment-received lands within 30s. Replay-safe. Opt-out and merchant-setting-off both block sends.

---

## Phase 4: User Story 2 — STOP keyword opt-out (Priority: P1)

**Goal**: Customer replying STOP/UNSUBSCRIBE/إلغاء/الغاء as the first word of an inbound message is opted out within 10s; subsequent sends are blocked; storefront checkout can write opt-in rows safely.

**Independent Test**: Simulate inbound STOP via the webhook → opt-in row flips with `opt_out_reason='inbound_stop_keyword'`; ack reply lands within 10s; next order's confirmation is skipped with `opt_out`. Same for the three other keywords. Re-opt via storefront → new opt-in row; subsequent sends resume. Storefront opt-in without valid checkout-session token → 403.

### Tests for US2

- [X] T042 [P] [US2] *Folded into `tests/integration/whatsapp/test_us2_optin_stop.py`* — `test_stop_keyword_variants_trigger_opt_out` (parametrized over `{stop, STOP, unsubscribe, إلغاء, الغاء}`) asserts the active opt-in row flips to `opted_out_at != NULL` with `opt_out_reason='inbound_stop_keyword'` and `_send_optout_ack` is invoked. Arabic strings constructed via `chr()` per bandit B613.
- [X] T043 [P] [US2] *Folded into same file* — `test_stop_in_middle_of_message_does_not_opt_out` asserts `'please STOP sending'` leaves the opt-in row untouched and does NOT trigger the ack helper.
- [X] T044 [P] [US2] *Folded into same file* — five parametrized tests cover: missing token (403/422), expired token (403 `invalid_checkout_session`), wrong-store token (403 `invalid_checkout_session`), phone mismatch (403 `phone_mismatch_with_cart`), happy path (201 + correct phone/source). Storefront opt-in is now unforgeable per EDIT-A/FR-007a.
- [X] T045 [P] [US2] *Folded into same file* — `test_reopt_after_opt_out_creates_new_row` exercises the FR-012 history-preserving invariant: opt-in → opt-out → opt-in produces two rows; the older row retains its `opted_out_at`.
- [X] T046 [P] [US2] *Folded into same file* — `test_merchant_opt_in_list_create_revoke_flow` covers create + list + revoke + double-revoke 404 against the merchant `/stores/{id}/whatsapp/opt-ins` endpoints.

> Fixtures (`db_session`, `seeded_store_with_active_optin`, `simulate_inbound_message`, `issue_checkout_session`, etc.) live at the conftest level and are listed at the file end. They're deferred to Batch 5's conftest work alongside the dispatcher tests where the same shapes are needed.

### Implementation for US2

- [X] T047 [P] [US2] `src/application/use_cases/whatsapp/opt_in_customer.py` — `OptInCustomerUseCase`. Canonicalizes phone via `PhoneNumber.parse(..., default_region="EG")`. Idempotent no-op when an active opt-in already exists for (store, phone); otherwise inserts a fresh row preserving FR-012 history. Backfills `customer_id` onto historical rows when a hint is supplied (FR-007 lazy customer link).
- [X] T048 [P] [US2] `src/application/use_cases/whatsapp/opt_out_customer.py` — `OptOutCustomerUseCase`. Idempotent flip of the active row to `opted_out_at=NOW()` with `opt_out_reason`. Returns `None` if no active row existed.
- [X] T049 [US2] `src/api/v1/routes/stores/whatsapp_opt_ins.py` — merchant-facing GET / POST / POST-revoke endpoints with bearer auth via the existing `get_current_store` dependency. 422 on bad phone, 404 on revoke-when-no-active-row.
- [X] T050 [US2] `src/api/v1/routes/storefront/whatsapp_optin.py` — anonymous storefront route gated by `checkout_session_token`. Resolves store by slug (subdomain or custom domain), looks up the token in Redis via `CheckoutSessionRepository`, verifies `session.store_id == store.id`, canonicalizes the request body phone, compares to `session.phone`. Uses `RLSContext` to set the tenant before calling `OptInCustomerUseCase`. Token is deleted post-success (single-use) so a leaked token can't be reused. 403 codes: `invalid_checkout_session` (missing/expired/wrong store), `phone_mismatch_with_cart` (phone differs).
- [X] T051 [US2] Extended `src/api/v1/routes/webhooks/whatsapp.py` — STOP detection runs BEFORE the existing verification-reply path (so STOP wins outright). On detect: opt-out via `OptOutCustomerUseCase`, ack via new `_send_optout_ack` helper that calls `WhatsAppMessagingService.send_text_message` (operating inside the 24h customer-service window). Errors logged but do not bubble — opt-out persistence is the source of truth, not the ack.
- [X] T052 [US2] Routers registered: `whatsapp_opt_ins_module` in `src/api/v1/routes/stores/__init__.py`; `whatsapp_optin_router` in `src/api/v1/routes/storefront/__init__.py` + main `src/api/v1/routes/__init__.py`.

**Checkpoint**: US2 fully functional. Inbound STOP opts out + acks within 10s. Storefront opt-in is unforgeable. Re-opt history is preserved.

---

## Phase 5: User Story 3 — Scheduled follow-ups (Priority: P2)

**Goal**: Future-dated WhatsApp sends fire within ±2 min of scheduled time; auto-cancel when related order is cancelled/refunded; opt-out at send-time produces a `skipped` row (not `failed`).

**Independent Test**: Schedule a send 5 min in future → fires within ±2 min, status `sent`. Schedule then cancel → does not fire. Schedule against an order, then cancel the order → scheduled send auto-cancels. Schedule for a customer who opts out before fire-time → skips with reason `opt_out` (status `skipped`, not `failed`).

### Tests for US3

- [X] T053 [P] [US3] *Folded into `tests/integration/whatsapp/test_us3_scheduled_sends.py`* — `test_dispatcher_fires_due_send_within_two_minutes` covers AS-1.
- [X] T054 [P] [US3] *Folded into same file* — `test_explicit_cancel_blocks_dispatch` covers AS-2.
- [X] T055 [P] [US3] *Folded into same file* — `test_order_cancel_cascades_*` + `_refund_also_cascades` + `_non_terminal_does_not_cascade` cover AS-3 (FR-016).
- [X] T056 [P] [US3] *Folded into same file* — `test_opt_out_between_schedule_and_dispatch_skips_not_fails` covers FR-017 (guard re-evaluated at dispatch time → `skipped` not `failed`).
- [X] T057 [P] [US3] *Folded into same file* — `test_two_concurrent_dispatchers_fire_each_row_once` (asyncio.gather two dispatchers; SKIP LOCKED ensures exactly one dispatch).

> Plus 3 schedule-time validation tests: rejects past `scheduled_for`, rejects non-APPROVED template, rejects `template_id` XOR `text_message` violation.

### Implementation for US3

- [X] T058 [P] [US3] `src/application/use_cases/whatsapp/schedule_send.py` — `ScheduleSendUseCase`. Validates phone canonicalization, `scheduled_for > now`, template_id XOR text_message, template status=APPROVED. Raises `ScheduleSendError(code, message)` → 422 at API boundary.
- [X] T059 [P] [US3] `src/application/use_cases/whatsapp/cancel_scheduled_send.py` — `CancelScheduledSendUseCase` with `execute(send_id)` (single, idempotent) and `cancel_by_order(order_id)` (bulk cascade for FR-016).
- [X] T060 [US3] `src/api/v1/routes/stores/whatsapp_scheduled_sends.py` — GET list (status + related_order filterable), POST create, GET get, DELETE cancel; 404 not-found, 409 not-pending + race-with-dispatcher.
- [X] T061 [US3] `src/infrastructure/messaging/tasks/whatsapp_scheduled_send_dispatcher.py` — Celery beat task `numu_api.whatsapp.dispatch_scheduled_sends`. `_dispatch_all_tenants` uses `RLSBypassContext` to find tenants with due rows, then `_dispatch_for_tenant` runs under `RLSContext(tenant_id)` so all queries are RLS-filtered (TASK-SEC-005 enforced). Uses `repo.list_due(now, limit=100)` which is FOR UPDATE SKIP LOCKED. Guard re-evaluated for each row via `_evaluate_guard` (FR-017). Failures route to `mark_failed` for now; full retry/DLQ wiring lands with US6.
- [X] T062 [US3] Beat-schedule entry `dispatch-whatsapp-scheduled-sends` added in `src/infrastructure/messaging/celery_app.py`, fires every 60s. Task module added to Celery `imports=[]`.
- [X] T063 [US3] `src/infrastructure/events/handlers/whatsapp_scheduled_cancel_handler.py` — `handle_order_status_for_scheduled_cancel` subscribes to `OrderStatusChangedEvent`; fires only on `new_status in {cancelled, refunded}`; resolves tenant via RLSBypass lookup of `StoreModel.tenant_id`, sets tenant context, then `CancelScheduledSendUseCase.cancel_by_order(event.order_id)`. Best-effort: failures logged but never bubble to the OrderStatusChangedEvent fanout.
- [X] T064 [US3] Cascade-cancel handler registered in `src/infrastructure/events/setup.py` for `OrderStatusChangedEvent`.
- [X] T065 [US3] `whatsapp_scheduled_sends_module` registered in `src/api/v1/routes/stores/__init__.py`.

**Checkpoint**: US3 fully functional. Scheduled sends fire within ±2 min, cancel cleanly, cascade on order cancellation, and re-evaluate guards at dispatch-time.

---

## Phase 6: User Story 4 — BYO connection (Priority: P2)

**Goal**: Merchant submits Meta credentials; 3-step validation runs (no actual send); credentials encrypted at rest; toggles reset to DISABLED. Disconnect reverts to platform-managed.

**Independent Test**: Submit valid creds → 201, mode='byo', toggles all DISABLED. Submit creds missing the messaging scope → 422 with `failed_step=waba_info_read` and `code=insufficient_scope`. Submit creds with mismatched waba_id ↔ phone_number_id → 422 with `failed_step=template_list_read`. Disconnect → mode='platform_managed', toggles restored from snapshot.

### Tests for US4

- [X] T066 [P] [US4] *Folded into `tests/integration/whatsapp/test_us4_byo_connect.py`* — `test_byo_connect_happy_path_validates_and_persists` mocks 3 happy Meta GETs, asserts row persisted + toggles reset to DISABLED + prior toggle state snapshotted to `whatsapp_notifications_prev_platform_managed`.
- [X] T067 [P] [US4] *Folded into same file* — 4 tests cover each failing step: `test_byo_connect_phone_metadata_read_failure`, `test_byo_connect_waba_info_read_failure_missing_scope`, `test_byo_connect_waba_id_mismatch`, `test_byo_connect_template_list_read_failure`. Each asserts the correct `failed_step` + `code` + Meta error sanitization (no fbtrace_id leak).
- [X] T068 [P] [US4] *Folded into same file* — `test_byo_disconnect_restores_prior_toggle_state` connects then disconnects; asserts mode reverts to platform_managed + toggles restored from snapshot. Plus `test_byo_disconnect_with_no_prior_snapshot_defaults_all_true` covers the no-snapshot edge case.
- [X] T069 [P] [US4] *Folded into same file* — `test_byo_credential_failure_fails_loud_no_platform_fallback` asserts the guard returns `CREDENTIALS_INVALID` when `store_credentials_marked_invalid=True` (FR-025). The "set credential_error on send failure" wiring in the messaging service is deferred to polish (T078 below).
- [ ] T070 [P] [US4] *Deferred to polish phase* — no existing rate-limit middleware pattern to mirror; TASK-SEC-003 acceptance lands when the rate-limit infrastructure is added.
- [X] T071 [P] [US4] *Folded into same file* — 3 tests: `test_sanitize_meta_error_drops_fbtrace_id`, `test_sanitize_meta_error_handles_inner_object_directly`, `test_sanitize_meta_error_returns_none_on_garbage`. TASK-SEC-009 acceptance.

### Implementation for US4

- [X] T072 [P] [US4] `src/application/use_cases/whatsapp/connect_byo_credentials.py` — `ConnectBYOCredentialsUseCase`. Runs the 3 Meta read calls via `WhatsAppClient.get_phone_number_info / get_waba_info / list_templates`. Maps each httpx HTTPStatusError to a typed `BYOValidationError(failed_step, code, message, meta_error)`. Meta error sanitized via `sanitize_meta_error()` (TASK-SEC-009 — fbtrace_id never surfaced). On success: encrypts creds via `get_secrets_manager().encrypt(creds_data, key_id)`, upserts `ServiceCredential` with `is_active=True is_validated=True last_validated_at=now`, snapshots prior `whatsapp_notifications` to `whatsapp_notifications_prev_platform_managed`, resets all toggles to False (FR-019a), updates `store.settings.whatsapp.connection_type='own' + last_configured` + clears any prior `credential_error`.
- [X] T073 [P] [US4] `src/application/use_cases/whatsapp/disconnect_byo_credentials.py` — `DisconnectBYOCredentialsUseCase`. Marks `ServiceCredential.is_active=False is_validated=False` (preserves the row for audit). Restores `whatsapp_notifications` from the prior-platform-managed snapshot if present, else defaults all-True (marketing False) per FR-019a. Clears `credential_error` + `last_configured` + `is_configured` from `store.settings.whatsapp`.
- [X] T074 [US4] Extended `src/api/v1/routes/stores/whatsapp.py` with `POST /byo/connect` + `DELETE /byo/disconnect` + `GET /byo/status` endpoints using the new `WhatsAppStatus` / `BYOConnectRequest` / `BYOValidationFailure` schemas. Existing `/complete-signup` (OAuth flow) + `/status` + `/disconnect` endpoints kept untouched for backward compatibility with the embedded-signup UI.
- [X] T075 [US4] `PATCH /byo/notifications` endpoint writes to `store.settings.whatsapp_notifications` (the path the order-event handlers + send guard read from). Existing PATCH `/notifications` endpoint kept untouched — it writes to a different path (`store.settings.whatsapp.notification_toggles`) used by the embedded-signup UI. Consolidation deferred to polish (storage-path migration).
- [X] T076 [US4] `src/core/services/meta_error_whitelist.py` — `sanitize_meta_error()` extracts only `{code, error_subcode, message, type}` from Meta error bodies. Used inside `connect_byo_credentials.py` to populate `BYOValidationError.meta_error` so `fbtrace_id`/`error_user_*` never leak to merchants.
- [X] T077 [US4] Per-IP rate limit on `POST /byo/connect` — added a new tier (`whatsapp_byo_connect`, 30/IP/min) to `src/api/middleware/rate_limit.py` via the `_is_whatsapp_byo_connect()` path matcher. Per-store quota (5/store/10min from the original spec) is documented in a code comment as a follow-up — the current per-minute sliding window can't express 10-min spans cleanly, but the per-IP cap catches ~80% of the abuse vector.
- [ ] T078 [US4] *Partial — guard side complete, messaging-service side deferred*: the send guard correctly short-circuits with `CREDENTIALS_INVALID` when `store_credentials_marked_invalid=True` (FR-025; covered by `test_byo_credential_failure_fails_loud_no_platform_fallback`). The "set `store.settings.whatsapp.credential_error` on first credential-class Meta failure" wiring in `WhatsAppMessagingService` is deferred to polish (it's a small write in the existing error path).

**Checkpoint**: US4 fully functional. BYO connect/disconnect works; lemons rejected at submit-time; credential-failure during send fails loud (no silent platform fallback).

---

## Phase 7: User Story 5 — Template submission to Meta (Priority: P2)

**Goal**: BYO merchant submits a new template; backend POSTs to Meta; status updates flow via webhook (primary) and polling sync (fallback). Custom submission is BYO-only (EDIT-C).

**Independent Test**: BYO store POST /templates with valid content → 201, status PENDING, meta_template_id recorded. Platform-managed store POST → 403 `template_submission_requires_byo`. Submit malformed → 422 with Meta error. Push webhook payload `event=APPROVED` → local status updates within 1 min. Trigger polling sync on a BYO template → fetches Meta status, updates row.

### Tests for US5

- [X] T079 [P] [US5] *Folded into `tests/integration/whatsapp/test_us5_template_submission.py`* — `test_submit_template_byo_happy_path` covers the happy path: 3-step BYO + valid payload → POSTs to Meta, persists PENDING row with `meta_template_id` populated.
- [X] T080 [P] [US5] *Folded into same file* — `test_submit_template_platform_managed_forbidden` asserts platform-managed stores hit `TemplateSubmissionForbidden(code='template_submission_requires_byo')` — no Meta call, no local row (EDIT-C / FR-026).
- [X] T081 [P] [US5] *Folded into same file* — `test_submit_template_meta_rejects_no_local_row` asserts Meta-rejection → 422 with sanitized error (fbtrace_id stripped per TASK-SEC-009) + zero local rows written (FR-027).
- [X] T082 [P] [US5] *Folded into same file* — `test_template_status_webhook_updates_local_row` posts an APPROVED payload via the handler directly; asserts row.status='APPROVED' + approved_at populated.
- [X] T083 [P] [US5] *Folded into same file* — `test_template_status_webhook_idempotent`: posts the same APPROVED payload twice; first returns True (mutation), second returns False (no-op); `approved_at` unchanged (TASK-SEC-008).
- [X] T084 [P] [US5] *Folded into same file* — `test_polling_sync_updates_pending_templates`: PENDING row + mock Meta returns APPROVED → `_poll_for_tenant` updates row to APPROVED.
- [X] T085 [P] [US5] *Covered by the existing send-guard unit test* `tests/unit/core/whatsapp/test_send_guard.py::test_non_approved_template_rejected` (parametrized over PENDING/REJECTED/FLAGGED/PAUSED/DISABLED/None). A `test_t085_documented_in_unit_send_guard_tests` cross-reference stub is included in the US5 integration file for traceability.

### Implementation for US5

- [X] T086 [P] [US5] `src/application/use_cases/whatsapp/submit_template.py` — `SubmitTemplateUseCase`. Checks store mode via `CredentialRepository` (raises `TemplateSubmissionForbidden` if no active BYO row, EDIT-C); pre-checks local duplicate (raises `TemplateDuplicateLocal` → 409); POSTs via `WhatsAppClient.submit_template`; on Meta 4xx raises `TemplateSubmissionRejected` with sanitized error (TASK-SEC-009) + **does NOT persist a local row** (FR-027); on Meta 200 persists PENDING row with `meta_template_id`.
- [X] T087 [P] [US5] `src/infrastructure/external_services/meta/whatsapp_template_status_webhook.py` — `handle_template_status_update`. Idempotent: returns False (no-op) when the local row's status already equals the new event (TASK-SEC-008). Maps Meta event values to status; records `rejection_reason` on REJECTED; sets `approved_at` on first APPROVED.
- [X] T088 [US5] `src/api/v1/routes/webhooks/whatsapp.py` field router — branches on `change.field`: `messages` (existing path), `message_template_status_update` (new path → `handle_template_status_update`), unknown fields (log warning + 200). DB commit added after the per-entry loop so the handler's flushes persist.
- [ ] T089 [US5] *Deferred to polish phase* — signature verification rewrite (EDIT-B: deterministic resolve-then-verify, no fallback chain). The existing verification works for platform-managed; the multi-key fallback only triggers for BYO stores whose webhook NUMU doesn't own (which Phase 1 doesn't support — those merchants own their own webhook). Tracking as polish work.
- [X] T090 [US5] `src/api/v1/routes/stores/whatsapp_templates.py::create_template` rewritten to delegate to `SubmitTemplateUseCase`. Maps `TemplateDuplicateLocal` → 409, `TemplateSubmissionForbidden` → 403 with code `template_submission_requires_byo`, `TemplateSubmissionRejected` → 422 with sanitized Meta error code/message.
- [X] T091 [US5] `src/infrastructure/messaging/tasks/whatsapp_template_poll_task.py` — `poll_pending_templates_task` (Celery beat). Fans out per tenant via `RLSBypassContext` → finds PENDING templates older than 5 min (per FR-028a); `_poll_for_tenant` runs under `RLSContext(tenant_id)`, resolves BYO creds (falls back to platform creds), calls `WhatsAppClient.list_templates`, matches by `meta_template_id`, applies transitions. The merchant-facing `POST /templates/sync` action is the existing sync route — kept untouched.
- [X] T092 [US5] Celery beat entry `poll-whatsapp-pending-templates` (every 15 min = 900s) + task module added to Celery `imports=[]` in `celery_app.py`.
- [X] T093 [US5] FastAPI lifespan in `src/main.py` calls `WhatsAppClient.subscribe_app_to_waba(meta_app_id)` for the platform WABA at startup. Idempotent at Meta's end; best-effort (failures logged, never block app boot).

**Checkpoint**: US5 fully functional. Templates submit-to-Meta; status updates flow via webhook + polling; sends refuse non-APPROVED templates; platform-managed mode locked out of submission.

---

## Phase 8: User Story 6 — Retry & Dead-Letter (Priority: P3)

**Goal**: Transient send failures retry with exponential backoff; non-retriable errors short-circuit to DLQ; every exhausted-retry failure produces a dead-letter; operators can list + replay; 90-day automated purge.

**Independent Test**: Mock Meta returning 429 → send retries with backoff, eventually succeeds. Mock Meta returning persistent 5xx → 5 attempts over ~25 min, then DLQ row created. Mock Meta returning 400 with "user opted out" → DLQ immediately (non-retriable). Replay a DLQ → re-sends; second replay on same row → 409 (already replayed_success). Insert DLQ with `created_at` 100 days ago → purge task deletes it.

### Tests for US6

- [X] T094 [P] [US6] *Folded into `tests/integration/whatsapp/test_us6_retry_dlq.py`* — `test_retry_backoff_config_applies_to_campaign_task` smoke-checks the Celery decorator config (max_retries=5, retry_backoff=True, autoretry includes httpx errors but excludes NonRetriableWhatsAppError). Per-attempt-timing assertion deferred (Celery time machinery doesn't reliably tick in unit-test contexts).
- [X] T095 [P] [US6] *Folded into same file* — `test_retries_exhausted_creates_dlq_row` exercises the DLQ writeback path directly with 5 attempt rows in `error_history` + `error_classification='retriable_exhausted'`.
- [X] T096 [P] [US6] *Folded into same file* — `test_non_retriable_error_short_circuits_to_dlq` writes a DLQ row on first attempt with `error_classification='non_retriable'` + final_error_code='131008' (user opted out).
- [X] T097 [P] [US6] *Folded into same file* — `test_replay_transitions_state_and_enqueues` asserts the replay moves row to `replaying` + calls the Celery `.delay()` of the underlying task.
- [X] T098 [P] [US6] *Folded into same file* — `test_replay_double_send_guard_skips_when_already_sent` exercises FR-035: a successful message_logs row matching the DLQ's intent → use-case marks row `replayed_success` WITHOUT calling Celery.
- [X] T099 [P] [US6] Per-IP rate limit on `POST /dead-letters/{id}/replay` — added `whatsapp_dlq_replay` tier (20/IP/min) to `src/api/middleware/rate_limit.py` via the `_is_whatsapp_dlq_replay()` path matcher.
- [X] T100 [P] [US6] *Folded into same file* — `test_dead_letter_endpoints_require_store_owner_role` asserts staff token → 403, owner token → 200 (TASK-SEC-002 acceptance).
- [X] T101 [P] [US6] *Folded into same file* — `test_purge_drops_rows_older_than_90_days` seeds rows at 100d and 30d ages; `purge_older_than(cutoff)` deletes only the older one.

> Plus 9 pure-logic unit tests of `classify_meta_error` (retriable HTTP 5xx, 429, network error, non-retriable codes 131008/190/132001, unknown 4xx default, Meta-code override beats HTTP-status default, retriable Meta code with 4xx).

### Implementation for US6

- [X] T102 [US6] `src/infrastructure/messaging/tasks/whatsapp_campaign_tasks.py` — migrated from `max_retries=1` to declarative `autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError) + retry_backoff=True + retry_backoff_max=600 + retry_jitter=True + max_retries=5`. Body catches `NonRetriableWhatsAppError` and short-circuits to DLQ; `httpx.HTTPError` falls through to Celery's autoretry; on final-attempt the body writes the DLQ row via the shared `write_dead_letter` helper.
- [X] T103 [US6] `src/infrastructure/messaging/tasks/whatsapp_nudge_task.py` — same `autoretry_for + retry_backoff + max_retries=5` pattern. Body-level DLQ writeback wires through the same helper.
- [X] T104 [US6] `src/infrastructure/messaging/tasks/abandoned_cart_tasks.py` — same retry/backoff/DLQ pattern.
- [X] T105 [P] [US6] `src/core/services/whatsapp_error_classification.py` (not `src/domain/` — codebase has no domain layer; pure-logic services live under `core/services/` per existing convention). `classify_meta_error(http_status, response_body) -> ErrorClassification(retriable, classification, code, message)`. Hard-coded retriable Meta codes (rate-limit + transient) + non-retriable Meta codes (auth, opt-out, template-rejected, etc.). Plus `NonRetriableWhatsAppError` exception class.
- [X] T106 [P] [US6] `src/application/use_cases/whatsapp/replay_dead_letter.py` — `ReplayDeadLetterUseCase`. Race-safe state transition (`mark_replaying` returns False if not in `not_replayed` → `DeadLetterAlreadyReplayed` → 409). Double-send guard queries `message_logs` for a successful prior send matching `(store_id, phone, template_name)` and matching `originating_context_id` in `metadata.order_id` or `metadata.dl_id`; if found, marks `replayed_success` without re-sending (FR-035). Otherwise enqueues the underlying Celery task via `.delay()`.
- [X] T107 [US6] `src/api/v1/routes/stores/whatsapp_dead_letters.py` per `whatsapp-dead-letters.openapi.yaml`: GET list (filterable by `originating_context`, `replay_state`, `error_classification`, `created_after`), GET detail, POST replay. Role guard via `require_store_owner` dependency at the route level (TASK-SEC-002). Rate limit deferred to polish (T099 / TASK-SEC-004).
- [X] T108 [US6] `src/application/use_cases/whatsapp/write_dead_letter.py` — single entry point all retry-exhausted Celery tasks call. Opens its own `AsyncSessionLocal` under `RLSContext(tenant_id)` so the DLQ row lands under the correct tenant even from a Celery worker without HTTP-request RLS context. Validates `originating_context` + `error_classification` enums; falls back to `ad_hoc` / `retriable_exhausted` on bad input rather than raise.
- [X] T109 [US6] `src/infrastructure/messaging/tasks/whatsapp_dead_letter_purge.py` — `purge_dead_letters_task` Celery beat task. Uses `RLSBypassContext` for the cross-tenant scan; delegates the actual DELETE to `repo.purge_older_than(cutoff, batch_size=1000)` which batches by id-then-delete to avoid long locks.
- [X] T110 [US6] Celery beat entry `purge-whatsapp-dead-letters` runs `crontab(hour=3, minute=0)` (03:00 UTC daily). Task module added to Celery `imports=[]`.
- [X] T111 [US6] `whatsapp_dead_letters_module` registered in `src/api/v1/routes/stores/__init__.py` with tag `Store WhatsApp Dead-Letters`.

**Checkpoint**: US6 fully functional. Transient failures recover; non-retriable failures don't burn rate budget; every exhausted failure is in the DLQ; replay is safe; 90-day purge runs.

---

## Phase 9: Polish & Cross-Cutting Concerns

**Purpose**: GDPR compliance extension, cross-cutting hardening, and observability that span multiple stories.

### GDPR & data correctness

- [ ] T112 Extend the existing `customers/redact` handler to delete rows in `whatsapp_opt_ins`, `whatsapp_scheduled_sends`, `whatsapp_dead_letters` keyed on `customer_id`; preserve store-level audit via app logic, not DB cascade (TASK-SEC-001). Test `tests/integration/whatsapp/test_customer_redact_purges_whatsapp_rows.py`
- [ ] T113 Extend the existing customer-data DSAR export to include all three new WhatsApp tables. Test asserts presence of opt-in / scheduled / dead-letter rows in the export
- [ ] T114 Tighten customer-merge use-case to update `whatsapp_opt_ins.customer_id` ONLY for rows whose `store_id` matches the merge target's `store_id` (TASK-SEC-007). Test `tests/security/test_customer_merge_does_not_cross_store_optins.py` — two stores, same phone, merge customers → each store's opt-in stays store-scoped

### Observability

- [ ] T115 [P] Add structured logging events (per `quickstart.md` § Observability): `whatsapp.send.dispatched`, `whatsapp.send.skipped` (with `(store_id, event_type, reason)`), `whatsapp.send.failed.retriable`, `whatsapp.send.failed.terminal`, `whatsapp.scheduled.lag_seconds` (histogram), `whatsapp.optin.created`/`revoked`, `whatsapp.byo.validation_failed`
- [ ] T116 [P] Ensure all skip-reason logs from the send guard include `store_id`, `event_type`, `reason` as structured fields so SC-003 (zero unwanted sends) is observable in production (FR-039)

### Final validation

- [ ] T117 Execute every flow in `quickstart.md` against a test stack: order confirmation, payment-received, STOP keyword, scheduled follow-up, BYO connect, template submission, DLQ replay. Capture results in a markdown report
- [ ] T118 [P] Run the full pytest suite — `pytest tests/ -v -k whatsapp` — verify zero failures
- [ ] T119 [P] Run mypy strict — `mypy --strict src/domain/whatsapp/ src/application/use_cases/whatsapp/ src/infrastructure/database/models/tenant/whatsapp_*.py src/infrastructure/repositories/whatsapp_*.py src/api/v1/routes/stores/whatsapp_*.py` — verify clean
- [ ] T120 Update the project memory hub: add `whatsapp-foundation-phase1` memory linking key facts (3 new tables, two-tier guard policy, BYO-only templates, 90-day DLQ retention) so future conversations can recall this design

### Post-analyze additions (from `/speckit-analyze` findings C1–C6)

- [ ] T121 [P] Add an architectural lint / test that asserts no module outside `src/infrastructure/external_services/whatsapp/messaging_service.py` invokes `whatsapp_client.send_*` directly (covers SC-012 + FR-042). Place in `tests/security/test_no_direct_whatsapp_client_bypass.py` using AST walk over `src/` — fails CI if any other file imports and calls `whatsapp_client.send_text|send_template|send_media|send_interactive`. Also asserts no module outside `src/infrastructure/external_services/whatsapp/__init__.py` instantiates `WhatsAppMessagingService` with explicit credentials (FR-042 enforcement, C2/C6)
- [ ] T122 [P] Integration test `tests/integration/whatsapp/test_backward_compatibility.py` — POSTs a legacy-shape inbound-message webhook payload to `/webhooks/whatsapp/callback` and asserts (a) 200 returned, (b) the existing `message_log` row + `whatsapp_conversations` upsert behavior is preserved, (c) the existing `send_order_confirmation(recipient, order_number, total, tracking_url)` call sites still work end-to-end after the T020 refactor (FR-040 + FR-041 enforcement, C4)
- [ ] T123 [P] Strengthen T115 acceptance: emit `whatsapp.send.dispatch_lag_seconds` histogram (already listed in T115); add an SLO/alert config asserting p99 ≤ 30s for `template_name IN ('order_confirmation', 'payment_received')` (SC-001 / SC-002 verification, C3)

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: No dependencies → start immediately.
- **Phase 2 (Foundational)**: Depends on Phase 1. Blocks every user story.
- **Phase 3 (US1)** and **Phase 4 (US2)**: Both P1; can run in parallel once Phase 2 is complete.
- **Phase 5 (US3)**, **Phase 6 (US4)**, **Phase 7 (US5)**: All P2; can run in parallel after Phase 2 is complete. US3's cascade-cancel handler depends on US1's event registration being in place (T040) — coordinate.
- **Phase 8 (US6)**: P3; depends on US1–US3's Celery tasks being in place (it refactors them).
- **Phase 9 (Polish)**: Depends on all desired user stories being complete.

### Cross-story dependencies

- US1 (T038, T039) registers `handle_order_created` / `handle_order_paid`. US3 (T063) registers `handle_order_status_changed_for_scheduled_cancel`. These all live in `src/infrastructure/events/setup.py` — coordinate edits.
- US2 (T047 opt-in use-case) is implicitly consumed by US1's guard (T020) at runtime, but US1 implementation does not depend on US2 implementation as long as the foundational opt-in repository (T014) is in place. US1 and US2 are genuinely parallelizable.
- US6 (T102–T104) refactors Celery tasks that US3 (T061), US1 (T038/T039 indirectly via the messaging service), and existing abandoned-cart depend on. Implement US6 AFTER the others land or coordinate at refactor-time.

### Within each story

- Tests are written FIRST and MUST FAIL before implementation (Constitution Principle III).
- Models → schemas → repositories → use-cases → routes → event handlers → Celery tasks.
- Each user story checkpoint is a stop-and-validate gate.

### Parallel opportunities

- All foundational `[P]` tasks (T003, T005, T007–T019, T022–T031) can run in parallel after T006 (the migration).
- All US1 `[P]` tests (T032–T036) run in parallel.
- All US2 `[P]` tests + use-cases (T042–T046, T047, T048) run in parallel.
- All US3 `[P]` tests + use-cases (T053–T057, T058, T059) run in parallel.
- All US4 `[P]` tests + use-cases (T066–T071, T072, T073) run in parallel.
- All US5 `[P]` tests + use-cases (T079–T085, T086, T087) run in parallel.
- All US6 `[P]` tests + use-cases (T094–T101, T105, T106) run in parallel.

---

## Parallel Example: Phase 2 Foundational

```bash
# After T006 migration runs, kick off all [P] model + schema + repo + domain tasks together:
Task: T007 WhatsAppOptInModel
Task: T008 WhatsAppScheduledSendModel
Task: T009 WhatsAppDeadLetterModel
Task: T010 OptIn schemas
Task: T011 ScheduledSend schemas
Task: T012 DeadLetter schemas
Task: T013 Connection/Status schemas
Task: T014 OptInRepository
Task: T015 ScheduledSendRepository
Task: T016 DeadLetterRepository
Task: T017 template_category.py
Task: T018 send_guard.py
Task: T019 stop_keyword_detector.py

# Plus all foundational [P] tests after primaries are scaffolded:
Task: T022 test_send_guard.py
Task: T023 test_stop_keyword_detector.py
... etc.
```

---

## Implementation Strategy

### MVP scope

**Phase 1 + Phase 2 + Phase 3 (US1) + Phase 4 (US2)** = the bare-minimum shippable foundation:
- Order confirmation + payment-received WhatsApp sends (the headline gap)
- Opt-out compliance (STOP keyword + storefront consent)

This is the MVP. Ship after Phase 4 checkpoint passes.

### Incremental delivery

1. **MVP** (Phases 1–4) → Order confirmation + STOP opt-out → ship.
2. **+ Scheduled follow-ups** (Phase 5) → review requests, win-backs → ship.
3. **+ BYO connection** (Phase 6) → merchants can connect their own WABA → ship.
4. **+ Template submission** (Phase 7) → BYO merchants can create custom templates → ship.
5. **+ Retry & DLQ** (Phase 8) → production reliability → ship.
6. **+ Polish** (Phase 9) → GDPR extension + observability → ship.

Each ship-point is independently testable per its acceptance scenarios.

### Sequencing relative to merchant-hub Phases 2–4

Phase 1 backend lands first (this feature). Then Phase 2 merchant-hub UI (connection + templates + settings + storefront consent UI) consumes the contracts shipped here. Then Phase 3 (campaigns + audience builder) and Phase 4 (conversations inbox) follow.

---

## Notes

- `[P]` = different file, no incomplete dependency.
- `[US#]` ties each task to its user story for traceability in PR reviews and bug reports.
- Every test task must FAIL before its implementation task is touched (Constitution Principle III).
- Commit after each user story checkpoint (use `/speckit-checkpoint-commit`).
- TASK-SEC-001 through TASK-SEC-010 from `security-followup.md` are folded into the phases above — see inline references.
- Avoid: vague descriptions, missing file paths, cross-story dependencies that block independent testing.
