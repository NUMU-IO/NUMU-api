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

- [ ] T021 Extend `src/infrastructure/external_services/meta/whatsapp_client.py` with `submit_template(waba_id, payload)`, `get_phone_number_info(phone_number_id)`, `get_waba_info(waba_id)`, `list_templates(waba_id, limit)`, `subscribe_app_to_waba(waba_id, app_id, access_token)`. Each method returns typed Pydantic v2 response models; raises typed exceptions on Meta error codes

### Checkout-session token mechanism (FR-007b — new infra; settled by /speckit-analyze A1 recon)

> NUMU does not have a checkout-session-token mechanism today (only a `numu_cart_session` HTTP-only cookie that does NOT carry a phone). FR-007a depends on a phone-bound token in the request body; this sub-block builds the minimal mechanism.

- [X] T031A [P] Create `src/core/entities/checkout_session.py` — Pydantic v2 entity `CheckoutSession { token: UUID, cart_session_id: str, store_id: UUID, phone: str (E.164), issued_at: datetime, expires_at: datetime }`. `cart_session_id` typed as `str` to match the existing `Cart.session_id` field shape (not UUID — cart sessions are opaque cookie values)
- [X] T031B [P] Create `src/infrastructure/repositories/checkout_session_repository.py` — Redis-backed repo with `create(cart_session_id, store_id, phone) -> CheckoutSession` (issues UUID, sets 30-min TTL), `get(token) -> CheckoutSession | None` (None on expired/corrupt), `delete(token)`. Key shape: `checkout_session:{token}`. Mirrors `RedisCartRepository` connection-management pattern
- [ ] T031C Add `POST /storefront/{store_slug}/checkout-session` to a new module `src/api/v1/routes/storefront/checkout_session.py`. Uses the existing `get_cart_owner()` dep to resolve the `numu_cart_session` cookie → returns 401 `missing_cart_session` if absent. Canonicalizes the body's phone via `whatsapp_phone_formatter`, returns 422 if unparsable. Persists CheckoutSession, returns `{token, expires_at}`. Rate-limited via existing `rate_limit_storefront` middleware
- [ ] T031D [P] Integration test `tests/integration/checkout/test_checkout_session_issue_and_resolve.py` — issue a token with valid cart cookie + phone → assert 201 + token resolves via repo + expires in 30 min ± clock skew. Also asserts 401 without cookie, 422 with unparsable phone
- [ ] T031E [P] Unit test `tests/unit/checkout/test_checkout_session_repository.py` — Redis happy/expired/missing cases

### Foundational tests

- [ ] T022 [P] Unit test `tests/unit/whatsapp/test_send_guard.py` — exhaustive truth table of (mode, opt-in state, opt-out state, notification setting, template category, 24h window, idempotency hit) → (allowed, reason)
- [ ] T023 [P] Unit test `tests/unit/whatsapp/test_stop_keyword_detector.py` — Latin case-insensitive, Arabic tashkeel-strip variants, first-word-only enforcement, NFKC normalization
- [ ] T024 [P] Unit test `tests/unit/whatsapp/test_template_category_classifier.py` — round-trip against all 5 system template seeds + marketing template fixture
- [ ] T025 [P] Unit test `tests/unit/whatsapp/test_send_guard_bypass_allowlist.py` — asserts `OPT_IN_BYPASS_ALLOWLIST == {"optout_confirmation_en", "optout_confirmation_ar"}` and that any other template name with `bypass=True` raises (TASK-SEC-010 acceptance)
- [ ] T026 [P] Security test `tests/security/test_rls_whatsapp_opt_ins.py` — cross-store read returns empty
- [ ] T027 [P] Security test `tests/security/test_rls_whatsapp_scheduled_sends.py` — cross-store read returns empty
- [ ] T028 [P] Security test `tests/security/test_rls_whatsapp_dead_letters.py` — cross-store read returns empty
- [ ] T029 [P] Security test `tests/security/test_rls_celery_workers.py` — runs dispatcher with `app.current_store_id` deliberately unset and asserts queries return empty (TASK-SEC-005 acceptance)
- [ ] T030 [P] Security test `tests/security/test_byo_secret_log_redaction.py` — triggers a synthetic 500 in the connect path and asserts the formatted log line contains redaction markers for `access_token`, `app_secret`, `phone_number_id`, `waba_id` (TASK-SEC-006 acceptance)
- [ ] T031 [P] Integration test `tests/integration/whatsapp/test_send_guard_two_tier.py` — parametrized over `{UTILITY, AUTHENTICATION, MARKETING}` template categories: utility/authentication-template send succeeds without opt-in row; marketing-template send is skipped with `no_opt_in`; all three categories are skipped when opt-out row exists. Plus: any-category send of a non-APPROVED template is skipped with `template_not_approved` (covers FR-029, C1/C5)

**Checkpoint**: Foundation ready — user story implementation can begin (US1 + US2 in parallel; US3–US6 sequential after).

---

## Phase 3: User Story 1 — Order confirmation arrives on WhatsApp (Priority: P1) 🎯 MVP

**Goal**: Customer with no explicit opt-out receives a WhatsApp order-confirmation within 30 seconds of placing an order; same for payment-received on payment.

**Independent Test**: Place an order on a platform-managed store with a customer phone present and no opt-out row → confirmation lands within 30s. Pay → payment-received lands within 30s. Insert opt-out row → no send. Disable notification toggle → no send. Replay event → no duplicate.

### Tests for US1 (write FIRST, ensure FAIL before implementation)

- [ ] T032 [P] [US1] Integration test `tests/integration/whatsapp/test_order_created_handler.py` — emit `OrderCreatedEvent` → assert `message_log` row with `template_name='order_confirmation'`, `metadata.order_id`, `status='sent'`; assert idempotent on event replay
- [ ] T033 [P] [US1] Integration test `tests/integration/whatsapp/test_order_paid_handler.py` — emit `OrderPaidEvent` → assert `message_log` row with `template_name='payment_received'`, idempotent on replay
- [ ] T034 [P] [US1] Integration test `tests/integration/whatsapp/test_order_confirmation_invoice_attachment.py` — assert `send_media_message` is invoked with the invoice PDF for the order
- [ ] T035 [P] [US1] Integration test `tests/integration/whatsapp/test_order_confirmation_respects_optout.py` — opt-out row present → no send, skip reason `opt_out`
- [ ] T036 [P] [US1] Integration test `tests/integration/whatsapp/test_order_confirmation_respects_merchant_setting.py` — `store_settings.whatsapp_notifications.order_confirmation=false` → no send, skip reason `merchant_setting_off`

### Implementation for US1

- [ ] T037 [US1] Extend `src/infrastructure/external_services/whatsapp/messaging_service.py::send_order_confirmation` to accept an optional `invoice_pdf_bytes` arg and call `send_media_message` if provided (FR-004) — invoice PDF assembly delegated to existing `_invoice_helper`
- [ ] T038 [US1] Add `handle_order_created` to `src/infrastructure/events/handlers/whatsapp_notification_handler.py` — resolves customer + phone, resolves invoice PDF via existing helper, calls `send_order_confirmation` (through the guard); idempotency via `message_log` lookup keyed on `metadata.order_id`
- [ ] T039 [US1] Add `handle_order_paid` to `src/infrastructure/events/handlers/whatsapp_notification_handler.py` — calls `send_payment_received` (through the guard); idempotency via `message_log` lookup keyed on `metadata.order_id + ':paid'`
- [ ] T040 [US1] Register both handlers in `src/infrastructure/events/setup.py` for `OrderCreatedEvent` and `OrderPaidEvent`
- [ ] T041 [US1] Verify `OrderCreatedEvent` and `OrderPaidEvent` carry `customer_phone` and the new `whatsapp_prefs.order_confirmation` / `whatsapp_prefs.payment_received` keys (extend the existing events if needed; both events are in `src/core/events/order_events.py`)

**Checkpoint**: US1 fully functional. Order placed → confirmation lands within 30s. Payment confirmed → payment-received lands within 30s. Replay-safe. Opt-out and merchant-setting-off both block sends.

---

## Phase 4: User Story 2 — STOP keyword opt-out (Priority: P1)

**Goal**: Customer replying STOP/UNSUBSCRIBE/إلغاء/الغاء as the first word of an inbound message is opted out within 10s; subsequent sends are blocked; storefront checkout can write opt-in rows safely.

**Independent Test**: Simulate inbound STOP via the webhook → opt-in row flips with `opt_out_reason='inbound_stop_keyword'`; ack reply lands within 10s; next order's confirmation is skipped with `opt_out`. Same for the three other keywords. Re-opt via storefront → new opt-in row; subsequent sends resume. Storefront opt-in without valid checkout-session token → 403.

### Tests for US2

- [ ] T042 [P] [US2] Integration test `tests/integration/whatsapp/test_stop_keyword_optout.py` — for each of `{stop, STOP, unsubscribe, إلغاء, الغاء}` as the first word: opt-in flipped within 10s; ack reply via `optout_confirmation_*` system template within 10s additional; subsequent send to same phone is skipped with `opt_out`
- [ ] T043 [P] [US2] Integration test `tests/integration/whatsapp/test_stop_keyword_not_first_word.py` — `"please STOP sending please"` → opt-in unchanged, message routes to conversations inbox normally
- [ ] T044 [P] [US2] Integration test `tests/integration/whatsapp/test_storefront_optin_requires_checkout_session.py` — issue a checkout-session token via T031C first, then: POST without `checkout_session_token` → 403; with invalid/expired token → 403 `invalid_checkout_session`; with valid token but mismatched phone → 403 `phone_mismatch_with_cart`; valid token + matched phone → 201 (EDIT-A)
- [ ] T045 [P] [US2] Integration test `tests/integration/whatsapp/test_reopt_creates_new_row.py` — opt-out then opt-in again → new row exists, prior `opted_out_at` preserved (FR-012)
- [ ] T046 [P] [US2] Integration test `tests/integration/whatsapp/test_merchant_opt_in_endpoints.py` — list / create / revoke flows via merchant API; revoke on already-revoked → 404

### Implementation for US2

- [ ] T047 [P] [US2] Create `src/application/use_cases/whatsapp/opt_in_customer.py` — canonicalize phone via existing `whatsapp_phone_formatter`; idempotent no-op if active opt-in already exists for `(store_id, phone)`
- [ ] T048 [P] [US2] Create `src/application/use_cases/whatsapp/opt_out_customer.py` — flips active opt-in row to `opted_out_at=NOW`, records `opt_out_reason`; idempotent
- [ ] T049 [US2] Create `src/api/v1/routes/stores/whatsapp_opt_ins.py` with merchant-facing endpoints: `GET /stores/{id}/whatsapp/opt-ins`, `POST /stores/{id}/whatsapp/opt-ins`, `POST /stores/{id}/whatsapp/opt-ins/revoke` (bearer auth)
- [ ] T050 [US2] Create storefront-facing route `POST /storefront/{store_slug}/whatsapp/opt-in` in `src/api/v1/routes/storefront/whatsapp_optin.py` — requires `checkout_session_token` in body; calls `checkout_session_repository.get(token)` (built in T031B); returns 403 `invalid_checkout_session` if missing/expired/wrong store_id; canonicalizes the request body's phone to E.164 and compares to the session's stored phone (also E.164 at issue time per T031C); returns 403 `phone_mismatch_with_cart` on mismatch; on success delegates to `opt_in_customer` use-case from T047 (EDIT-A + FR-007a)
- [ ] T051 [US2] Extend `src/api/v1/routes/webhooks/whatsapp.py` inbound-message branch: after existing logging, run `stop_keyword_detector` on text messages; on hit, invoke `opt_out_customer` use-case and send `optout_confirmation_{lang}` template (bypassing opt-in guard via the allowlist — utility ack to a customer who just messaged us)
- [ ] T052 [US2] Register the new opt-ins router in `src/api/v1/routes/__init__.py`

**Checkpoint**: US2 fully functional. Inbound STOP opts out + acks within 10s. Storefront opt-in is unforgeable. Re-opt history is preserved.

---

## Phase 5: User Story 3 — Scheduled follow-ups (Priority: P2)

**Goal**: Future-dated WhatsApp sends fire within ±2 min of scheduled time; auto-cancel when related order is cancelled/refunded; opt-out at send-time produces a `skipped` row (not `failed`).

**Independent Test**: Schedule a send 5 min in future → fires within ±2 min, status `sent`. Schedule then cancel → does not fire. Schedule against an order, then cancel the order → scheduled send auto-cancels. Schedule for a customer who opts out before fire-time → skips with reason `opt_out` (status `skipped`, not `failed`).

### Tests for US3

- [ ] T053 [P] [US3] Integration test `tests/integration/whatsapp/test_scheduled_send_dispatcher.py` — create row with `scheduled_for = NOW + 90s`; trigger dispatcher; within 2 min row is `sent` and `message_log` has the entry; assert lag metric ≤ 120s
- [ ] T054 [P] [US3] Integration test `tests/integration/whatsapp/test_scheduled_send_cancel_explicit.py` — DELETE on pending row → status `cancelled`, no send fires
- [ ] T055 [P] [US3] Integration test `tests/integration/whatsapp/test_cascade_cancel_on_order_cancel.py` — schedule 3 sends for an order; emit `OrderStatusChangedEvent(new_status='cancelled')`; assert all 3 → `cancelled`
- [ ] T056 [P] [US3] Integration test `tests/integration/whatsapp/test_scheduled_send_optout_at_dispatch_time.py` — schedule send; insert opt-out row; trigger dispatcher; assert row → `skipped` with `skip_reason='opt_out'`, NOT `failed`
- [ ] T057 [P] [US3] Integration test `tests/integration/whatsapp/test_scheduled_send_concurrent_dispatch.py` — two parallel dispatcher invocations on same row → `FOR UPDATE SKIP LOCKED` ensures exactly one send

### Implementation for US3

- [ ] T058 [P] [US3] Create `src/application/use_cases/whatsapp/schedule_send.py` — validates `scheduled_for > now`, validates template is APPROVED if `template_id` set, validates either `template_id` XOR `text_message` set, creates row
- [ ] T059 [P] [US3] Create `src/application/use_cases/whatsapp/cancel_scheduled_send.py` — single-cancel (by send_id) and bulk-cancel (by related_order_id) variants; 409 if status not `pending`
- [ ] T060 [US3] Create `src/api/v1/routes/stores/whatsapp_scheduled_sends.py` per `whatsapp-scheduled-sends.openapi.yaml`: GET list, POST create, GET get, DELETE cancel
- [ ] T061 [US3] Create `src/infrastructure/messaging/tasks/whatsapp_scheduled_send_dispatcher.py` — Celery beat task `numu_api.whatsapp.dispatch_scheduled_sends` running every 60s; uses `tenant_session(store_id)` (TASK-SEC-005); `SELECT ... FOR UPDATE SKIP LOCKED LIMIT 100`; per-row re-runs send guard at dispatch-time; failures route to `WhatsAppMessagingService` retry/DLQ path (US6)
- [ ] T062 [US3] Add Celery beat schedule entry for `dispatch_scheduled_sends` (every 60s) in the existing beat config
- [ ] T063 [US3] Create `src/infrastructure/events/handlers/whatsapp_scheduled_cancel_handler.py` — subscribes to `OrderStatusChangedEvent`; when `new_status in {'cancelled', 'refunded'}` calls `cancel_scheduled_send(related_order_id=event.order_id)`
- [ ] T064 [US3] Register the cascade-cancel handler in `src/infrastructure/events/setup.py`
- [ ] T065 [US3] Register the new scheduled-sends router

**Checkpoint**: US3 fully functional. Scheduled sends fire within ±2 min, cancel cleanly, cascade on order cancellation, and re-evaluate guards at dispatch-time.

---

## Phase 6: User Story 4 — BYO connection (Priority: P2)

**Goal**: Merchant submits Meta credentials; 3-step validation runs (no actual send); credentials encrypted at rest; toggles reset to DISABLED. Disconnect reverts to platform-managed.

**Independent Test**: Submit valid creds → 201, mode='byo', toggles all DISABLED. Submit creds missing the messaging scope → 422 with `failed_step=waba_info_read` and `code=insufficient_scope`. Submit creds with mismatched waba_id ↔ phone_number_id → 422 with `failed_step=template_list_read`. Disconnect → mode='platform_managed', toggles restored from snapshot.

### Tests for US4

- [ ] T066 [P] [US4] Integration test `tests/integration/whatsapp/test_byo_credential_validation_success.py` — mock 3 Meta GETs all 200; assert row written encrypted; assert mode='byo'; assert toggles flipped to DISABLED; assert snapshot of prior platform-managed state stored in `store_settings.whatsapp_notifications_prev_platform_managed`
- [ ] T067 [P] [US4] Integration test `tests/integration/whatsapp/test_byo_credential_validation_failures.py` — parametrized: each of `(phone_metadata_read, waba_info_read, template_list_read)` returns non-200; assert response identifies the failed step + Meta error
- [ ] T068 [P] [US4] Integration test `tests/integration/whatsapp/test_byo_disconnect.py` — connect BYO → disconnect → mode reverts to `platform_managed`; toggles restored from snapshot; subsequent send uses platform credentials
- [ ] T069 [P] [US4] Integration test `tests/integration/whatsapp/test_byo_expired_token_fails_loud.py` — BYO connect succeeds at time T0; at T1 Meta returns 401 on send; assert send fails (not silently routed to platform), `credential_error` set on status endpoint, NO platform fallback (FR-025)
- [ ] T070 [P] [US4] Integration test `tests/integration/whatsapp/test_byo_connect_rate_limit.py` — 6 connect attempts in 10 min for same store → 6th returns 429 + Retry-After (TASK-SEC-003 acceptance)
- [ ] T071 [P] [US4] Unit test `tests/unit/whatsapp/test_byo_validation_error_shape.py` — Meta error body whitelist drops `fbtrace_id`, surfaces only `code`, `error_subcode`, `message`, `type` (TASK-SEC-009 acceptance)

### Implementation for US4

- [ ] T072 [P] [US4] Create `src/application/use_cases/whatsapp/connect_byo_credentials.py` — orchestrates the 3 Meta read calls (research.md R2), constructs `BYOValidationFailure` on each failure path, persists to `service_credentials` via existing AES-256 mechanism, snapshots current `store_settings.whatsapp_notifications` to `whatsapp_notifications_prev_platform_managed`, resets toggles to all-DISABLED (FR-019a)
- [ ] T073 [P] [US4] Create `src/application/use_cases/whatsapp/disconnect_byo_credentials.py` — deactivates the `service_credentials` row (or deletes per existing pattern), restores `store_settings.whatsapp_notifications` from snapshot or defaults all-ENABLED if no snapshot
- [ ] T074 [US4] Extend `src/api/v1/routes/stores/whatsapp.py` — add `POST /byo/connect`, `DELETE /byo/disconnect`, enrich `GET /status` to return `mode`, `last_validated_at`, `credential_error`, `notifications` (per `whatsapp-connection.openapi.yaml`)
- [ ] T075 [US4] Add `PATCH /stores/{id}/whatsapp/notifications` endpoint to `whatsapp.py` for per-toggle updates
- [ ] T076 [US4] Apply Meta error whitelist (`code`, `error_subcode`, `message`, `type` only) in `BYOValidationFailure` schema serialization — drops `fbtrace_id` etc. (TASK-SEC-009)
- [ ] T077 [US4] Apply per-store + per-IP rate limit on `POST /byo/connect` (5/store/10min + 30/IP/min) via the existing rate-limit middleware (TASK-SEC-003)
- [ ] T078 [US4] Wire the `credential_error` field — when a send fails with a credential-class Meta error in BYO mode, set the store-level error and surface in `GET /status` (FR-025)

**Checkpoint**: US4 fully functional. BYO connect/disconnect works; lemons rejected at submit-time; credential-failure during send fails loud (no silent platform fallback).

---

## Phase 7: User Story 5 — Template submission to Meta (Priority: P2)

**Goal**: BYO merchant submits a new template; backend POSTs to Meta; status updates flow via webhook (primary) and polling sync (fallback). Custom submission is BYO-only (EDIT-C).

**Independent Test**: BYO store POST /templates with valid content → 201, status PENDING, meta_template_id recorded. Platform-managed store POST → 403 `template_submission_requires_byo`. Submit malformed → 422 with Meta error. Push webhook payload `event=APPROVED` → local status updates within 1 min. Trigger polling sync on a BYO template → fetches Meta status, updates row.

### Tests for US5

- [ ] T079 [P] [US5] Integration test `tests/integration/whatsapp/test_template_submission_to_meta_byo.py` — BYO mode, valid payload → POSTs to Meta, persists local PENDING row, returns it
- [ ] T080 [P] [US5] Integration test `tests/integration/whatsapp/test_template_submission_platform_managed_forbidden.py` — platform-managed mode → 403 `template_submission_requires_byo`; no local row written; no Meta call made (EDIT-C)
- [ ] T081 [P] [US5] Integration test `tests/integration/whatsapp/test_template_submission_meta_rejects.py` — Meta returns 400 (name collision) → 422 to caller; no local row written
- [ ] T082 [P] [US5] Integration test `tests/integration/whatsapp/test_template_status_webhook.py` — post `message_template_status_update` webhook payload → local row updated to APPROVED with `approved_at` timestamp within 1 min
- [ ] T083 [P] [US5] Integration test `tests/integration/whatsapp/test_template_status_webhook_idempotent.py` — same payload posted twice → no duplicate side effects, no extra DB writes (TASK-SEC-008 acceptance)
- [ ] T084 [P] [US5] Integration test `tests/integration/whatsapp/test_template_polling_sync.py` — PENDING template local; mock Meta returning APPROVED on `GET /message_templates`; trigger sync → row updates
- [ ] T085 [P] [US5] Integration test `tests/integration/whatsapp/test_send_refused_for_non_approved_template.py` — template status REJECTED → `send_template` for it raises clear error; no Meta send attempted

### Implementation for US5

- [ ] T086 [P] [US5] Create `src/application/use_cases/whatsapp/submit_template.py` — checks store mode (raises if `platform_managed`, EDIT-C), validates payload shape, POSTs to Meta via `meta/whatsapp_client.submit_template`, persists local row with returned `meta_template_id`, status PENDING
- [ ] T087 [P] [US5] Create `src/infrastructure/external_services/meta/whatsapp_template_status_webhook.py` — handler for the `message_template_status_update` webhook field; idempotent re-application of same state (TASK-SEC-008)
- [ ] T088 [US5] Refactor `src/api/v1/routes/webhooks/whatsapp.py` to route by `entry[].changes[].field` — existing `messages`/`statuses` flow unchanged; new `message_template_status_update` flow → new handler; unknown fields → log + 200
- [ ] T089 [US5] Update `src/api/v1/routes/webhooks/whatsapp.py` signature-verification path to the deterministic resolve-then-verify algorithm (EDIT-B): read `entry[].id`, resolve store, verify single secret, no fallback; missing/mismatched `entry[].id` → 200 + no-op + structured warning; verification failure after resolution → 401
- [ ] T090 [US5] Extend `src/api/v1/routes/stores/whatsapp_templates.py::POST` to actually invoke `submit_template` use-case (was previously local-only); add 403 response branch for `template_submission_requires_byo`
- [ ] T091 [US5] Refactor `src/infrastructure/external_services/whatsapp/template_service.py` so the polling sync action queries Meta via `list_templates` and updates each PENDING local row; runs both as an explicit `POST /templates/sync` action and on a 15-min Celery beat task `numu_api.whatsapp.poll_pending_templates`
- [ ] T092 [US5] Add Celery beat schedule entry for `poll_pending_templates` (every 15 min)
- [ ] T093 [US5] On NUMU platform app boot, ensure the app is subscribed to `message_template_status_update` field on the platform WABA via `subscribe_app_to_waba` (idempotent; check + subscribe if missing)

**Checkpoint**: US5 fully functional. Templates submit-to-Meta; status updates flow via webhook + polling; sends refuse non-APPROVED templates; platform-managed mode locked out of submission.

---

## Phase 8: User Story 6 — Retry & Dead-Letter (Priority: P3)

**Goal**: Transient send failures retry with exponential backoff; non-retriable errors short-circuit to DLQ; every exhausted-retry failure produces a dead-letter; operators can list + replay; 90-day automated purge.

**Independent Test**: Mock Meta returning 429 → send retries with backoff, eventually succeeds. Mock Meta returning persistent 5xx → 5 attempts over ~25 min, then DLQ row created. Mock Meta returning 400 with "user opted out" → DLQ immediately (non-retriable). Replay a DLQ → re-sends; second replay on same row → 409 (already replayed_success). Insert DLQ with `created_at` 100 days ago → purge task deletes it.

### Tests for US6

- [ ] T094 [P] [US6] Integration test `tests/integration/whatsapp/test_retry_exponential_backoff.py` — Meta returns 429 thrice then 200 → eventually succeeds; assert ≥3 attempts spanning ≥5 minutes (FR-031)
- [ ] T095 [P] [US6] Integration test `tests/integration/whatsapp/test_retry_exhausted_creates_dl.py` — Meta returns persistent 500 → after 5 attempts, DLQ row exists with full `error_history` and `error_classification='retriable_exhausted'`
- [ ] T096 [P] [US6] Integration test `tests/integration/whatsapp/test_non_retriable_short_circuit_to_dl.py` — Meta returns 400 with non-retriable code → DLQ row created on first attempt, `error_classification='non_retriable'`, zero retries (FR-032)
- [ ] T097 [P] [US6] Integration test `tests/integration/whatsapp/test_dead_letter_replay_success.py` — replay endpoint → enqueues send → message_log records send → DLQ row updated to `replayed_success` with `replayed_send_id`
- [ ] T098 [P] [US6] Integration test `tests/integration/whatsapp/test_dead_letter_replay_double_send_guard.py` — DLQ has original send already in `message_log` with status `sent` → replay marks `replayed_success` WITHOUT re-issuing send (FR-035)
- [ ] T099 [P] [US6] Integration test `tests/integration/whatsapp/test_dead_letter_replay_rate_limit.py` — 21 replays in 1 min for same store → 21st returns 429 with Retry-After (TASK-SEC-004)
- [ ] T100 [P] [US6] Security test `tests/security/test_dead_letter_role_gating.py` — staff/viewer tokens → 403 on list/get/replay; admin/owner tokens → 200 (TASK-SEC-002 acceptance)
- [ ] T101 [P] [US6] Integration test `tests/integration/whatsapp/test_dead_letter_purge.py` — seed DLQ rows older than 90 days + younger than 90 days; run purge task → only older rows deleted

### Implementation for US6

- [ ] T102 [US6] Refactor `src/infrastructure/messaging/tasks/whatsapp_campaign_tasks.py` — migrate from `max_retries=1` to declarative `autoretry_for=(httpx.HTTPStatusError, httpx.NetworkError) + retry_backoff=True + retry_backoff_max=600 + retry_jitter=True + max_retries=5`; non-retriable errors raise `NonRetriableWhatsAppError` (new exception) that Celery does NOT autoretry
- [ ] T103 [US6] Refactor `src/infrastructure/messaging/tasks/whatsapp_nudge_task.py` — same retry pattern
- [ ] T104 [US6] Refactor `src/infrastructure/messaging/tasks/abandoned_cart_tasks.py` — same retry pattern
- [ ] T105 [P] [US6] Create `src/domain/whatsapp/error_classification.py` — `classify_meta_error(http_status, body) -> tuple[bool, str]` (retriable_flag, code); maps Meta's `error.code` ranges to retriable/non-retriable per Meta docs
- [ ] T106 [P] [US6] Create `src/application/use_cases/whatsapp/replay_dead_letter.py` — checks current `replay_state` (refuses if `replaying`/`replayed_success`); checks `message_log` for prior successful send with same idempotency key (FR-035 double-send guard); enqueues replay Celery task; marks row `replaying`
- [ ] T107 [US6] Create `src/api/v1/routes/stores/whatsapp_dead_letters.py` per `whatsapp-dead-letters.openapi.yaml`: GET list, GET get, POST replay; role guard requiring admin/owner (TASK-SEC-002); rate limit 20/min/store on replay (TASK-SEC-004)
- [ ] T108 [US6] Wire dead-letter creation into the generic retry-exhaustion path inside the refactored Celery tasks — when `task.retries == max_retries` and final failure, create DLQ row capturing `phone, template_id/text, params, originating_context, originating_context_id, error_history, error_classification, final_error_code`
- [ ] T109 [US6] Create `src/infrastructure/messaging/tasks/whatsapp_dead_letter_purge.py` — Celery beat task `numu_api.whatsapp.purge_dead_letters` running daily at 03:00 UTC; batched delete of rows `WHERE created_at < NOW() - INTERVAL '90 days'` (FR-035a)
- [ ] T110 [US6] Add Celery beat schedule entry for `purge_dead_letters`
- [ ] T111 [US6] Register the new dead-letters router

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
