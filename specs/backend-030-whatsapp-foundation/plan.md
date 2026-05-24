# Implementation Plan: WhatsApp Integration — Phase 1: Backend Foundation

**Branch**: `backend-030-whatsapp-foundation` | **Date**: 2026-05-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/backend-030-whatsapp-foundation/spec.md`

## Summary

Close the backend gaps that block the merchant-hub UI work (Phases 2–4) on the existing WhatsApp integration. Wire `OrderCreatedEvent` and `OrderPaidEvent` to send WhatsApp confirmations through the existing per-store `get_whatsapp_service` resolver. Introduce three new tables (`whatsapp_opt_ins`, `whatsapp_scheduled_sends`, `whatsapp_dead_letters`) with RLS and Alembic migrations. Detect inbound STOP/UNSUBSCRIBE/إلغاء keywords in the existing `/webhooks/whatsapp/callback` route and flip opt-out. Add a Celery-beat `whatsapp_scheduled_send_dispatcher` task that fires due rows within ±2 minutes. Refactor send paths to flow through a single `WhatsAppSendGuard` that enforces opt-in (two-tier: utility templates bypass active opt-in but respect opt-out; marketing requires active opt-in), credentials, merchant settings, and the 24h window. Add per-store BYO connection endpoint with three-step Meta validation (phone metadata + WABA scopes + template list). Add template submission to Meta (POST `/{waba_id}/message_templates`) and Meta `message_template_status_update` webhook field handling alongside the existing polling sync. Switch existing WhatsApp Celery tasks from `max_retries=1` to exponential backoff with a dead-letter sink and 90-day purge task.

## Technical Context

**Language/Version**: Python 3.13
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 (async), asyncpg, Pydantic v2, Celery 5.x + Redis, httpx (for Meta Graph API), structlog, alembic
**Storage**: PostgreSQL 15+ (tenant-scoped tables with RLS; existing pattern in `src/infrastructure/database/models/tenant/`)
**Testing**: pytest (async via `pytest-asyncio`), respx for HTTP mocking, existing fixtures under `tests/fixtures/`
**Target Platform**: Linux (Docker Compose on droplet; per-env stack — see `envs-layout.md`)
**Project Type**: web-service (FastAPI backend; merchant-hub/storefront frontends are separate repos)
**Performance Goals**: Order/payment confirmation send dispatched within 30s of event (SC-001/002); scheduled-send dispatcher fires within ±2 min of `scheduled_for` (SC-005)
**Constraints**: Tenant isolation via RLS (Principle V); async-only I/O (Principle IV); mypy strict; secrets AES-256 in `service_credentials`; idempotent Celery tasks; no raw PII in cross-store tables (Principle I — N/A here, all new tables are tenant-scoped)
**Scale/Scope**: ~50 stores in pilot, ~5k pending scheduled sends in queue (steady-state target), ~100 dead-letters/day worst case before purge

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **I. Privacy by Hashing (NON-NEGOTIABLE)** | ✅ N/A | All new tables (`whatsapp_opt_ins`, `whatsapp_scheduled_sends`, `whatsapp_dead_letters`) are **tenant-scoped**, not network-scoped. Phones are stored E.164 plaintext within a tenant boundary (same as existing `whatsapp_conversations.customer_phone`). No cross-store identifier writes. |
| **II. GDPR Recital 47 Fidelity (NON-NEGOTIABLE)** | ✅ Pass | Legitimate-interest basis (Recital 47): customer initiated commerce relationship; utility messages are transactional. Marketing requires active opt-in (FR-011). DSAR: opt-in rows + dead-letter rows + scheduled-send rows joined to `customer_id` are included in the existing customer-data export. Erasure: `customers/redact` handler purges these tables alongside existing customer tables (covered in data-model.md cascade rules). Opt-out: STOP keyword + endpoint flip `opted_out_at`; no retention of marketing sends after opt-out. |
| **III. Spec-First, Tests From Spec (NON-NEGOTIABLE)** | ✅ Pass | spec.md + plan.md + this plan → merged before `/speckit-tasks` (plan-review-gate hook). All 42 FRs + 12 SCs map to tests in `tests/integration/whatsapp/` and `tests/unit/whatsapp/`. |
| **IV. Async-First, Strictly Typed** | ✅ Pass | All new code `async def`; SQLAlchemy 2.0 async sessions; Pydantic v2 schemas at every API boundary; mypy strict. |
| **V. Tenant Isolation by RLS, Always** | ✅ Pass | All three new tables get RLS policies filtering by `store_id` (via `current_setting('app.current_store_id')`) in the same Alembic migration. |

Additional constraint pass-through:
- **Alembic discipline**: one migration per new table with `up()` + `down()`; RLS in same migration.
- **Celery task naming**: new tasks live under `src/infrastructure/messaging/tasks/whatsapp_*_tasks.py`, fully qualified names `numu_api.whatsapp.dispatch_scheduled_sends`, `numu_api.whatsapp.purge_dead_letters`.
- **Contract-versioned API**: all new endpoints under `/api/v1/stores/{store_id}/whatsapp/...` — additive only, no breaking changes to existing whatsapp endpoints.
- **Secret hygiene**: BYO credentials encrypted via existing `infrastructure/external_services/secrets/` (AES-256-GCM) stored in `service_credentials`.

**Gate result**: PASS. No violations. No Complexity Tracking entries needed.

## Project Structure

### Documentation (this feature)

```text
specs/backend-030-whatsapp-foundation/
├── plan.md                                 # This file
├── spec.md                                  # Feature specification (clarified)
├── research.md                              # Phase 0 output (created here)
├── data-model.md                            # Phase 1 output (created here)
├── quickstart.md                            # Phase 1 output (created here)
├── contracts/                               # Phase 1 output (created here)
│   ├── whatsapp-connection.openapi.yaml
│   ├── whatsapp-templates.openapi.yaml
│   ├── whatsapp-scheduled-sends.openapi.yaml
│   ├── whatsapp-dead-letters.openapi.yaml
│   ├── whatsapp-opt-ins.openapi.yaml
│   └── whatsapp-webhook-meta.md             # webhook payload contract (not OpenAPI)
├── checklists/
│   └── requirements.md                      # already exists from /speckit-specify
├── version-guard-report.md                  # already exists (skipped — npm-focused)
└── tasks.md                                 # Created by /speckit-tasks, NOT here
```

### Source Code (repository — additions only; no restructure)

```text
src/
├── core/
│   ├── events/
│   │   └── order_events.py                   # EXISTS — OrderCreatedEvent, OrderPaidEvent, OrderStatusChangedEvent
│   └── interfaces/
│       └── services/messaging_service.py     # EXISTS — IMessagingService / MessageRecipient
│
├── domain/
│   └── whatsapp/                              # NEW — domain primitives, no I/O
│       ├── __init__.py
│       ├── send_guard.py                      # WhatsAppSendGuard pure logic + reason codes
│       ├── stop_keyword_detector.py           # first-word STOP/UNSUBSCRIBE/إلغاء/الغاء detection
│       └── template_category.py               # UTILITY / MARKETING / AUTHENTICATION enum + classifier
│
├── application/
│   └── use_cases/
│       └── whatsapp/
│           ├── connect_byo_credentials.py     # NEW — orchestrates 3-step Meta validation + write
│           ├── disconnect_byo_credentials.py  # NEW — reverts to platform; resets toggles to DISABLED for next BYO
│           ├── submit_template.py             # NEW — POST to Meta + persist PENDING row
│           ├── opt_in_customer.py             # NEW — storefront checkout entry point
│           ├── opt_out_customer.py            # NEW — used by webhook STOP handler and merchant API
│           ├── schedule_send.py               # NEW — create whatsapp_scheduled_sends row
│           ├── cancel_scheduled_send.py       # NEW — single + bulk (by order_id) cancel
│           └── replay_dead_letter.py          # NEW — operator replay with double-send guard
│
├── infrastructure/
│   ├── database/
│   │   └── models/tenant/
│   │       ├── whatsapp_opt_in.py             # NEW
│   │       ├── whatsapp_scheduled_send.py     # NEW
│   │       ├── whatsapp_dead_letter.py        # NEW
│   │       ├── whatsapp_conversation.py       # EXISTS
│   │       ├── whatsapp_template.py           # EXISTS — extend with system_template seed list
│   │       ├── whatsapp_campaign.py           # EXISTS
│   │       └── message_log.py                 # EXISTS
│   │
│   ├── repositories/
│   │   ├── whatsapp_opt_in_repository.py      # NEW
│   │   ├── whatsapp_scheduled_send_repository.py # NEW
│   │   └── whatsapp_dead_letter_repository.py # NEW
│   │
│   ├── external_services/
│   │   ├── meta/
│   │   │   ├── whatsapp_client.py             # EXISTS — extend with submit_template, get_phone_number_info, get_waba_info, list_templates, subscribe_app_to_waba
│   │   │   └── whatsapp_template_status_webhook.py # NEW — handler for message_template_status_update webhook field
│   │   └── whatsapp/
│   │       ├── __init__.py                    # EXISTS — get_whatsapp_service resolver (UNCHANGED)
│   │       ├── messaging_service.py           # EXISTS — wrap all send_* in WhatsAppSendGuard
│   │       └── template_service.py            # EXISTS — extend submit + webhook-driven status update
│   │
│   ├── events/
│   │   ├── setup.py                           # EXISTS — register new handlers
│   │   └── handlers/
│   │       ├── whatsapp_notification_handler.py # EXISTS — extend with OrderCreatedEvent + OrderPaidEvent handlers
│   │       └── whatsapp_scheduled_cancel_handler.py # NEW — subscribes to OrderStatusChangedEvent for cancelled/refunded → cascade cancel scheduled sends
│   │
│   └── messaging/tasks/
│       ├── whatsapp_scheduled_send_dispatcher.py # NEW — Celery beat: scan + dispatch due rows
│       ├── whatsapp_dead_letter_purge.py      # NEW — Celery beat: 90-day purge
│       ├── whatsapp_campaign_tasks.py         # EXISTS — switch to exponential backoff
│       ├── whatsapp_nudge_task.py             # EXISTS — switch to exponential backoff
│       └── abandoned_cart_tasks.py            # EXISTS — switch to exponential backoff
│
├── api/v1/routes/
│   ├── stores/
│   │   ├── whatsapp.py                        # EXISTS — extend: connect/disconnect BYO, status with mode + last_validated
│   │   ├── whatsapp_templates.py              # EXISTS — extend: submit endpoint actually POSTs to Meta
│   │   ├── whatsapp_chat.py                   # EXISTS
│   │   ├── whatsapp_campaigns.py              # EXISTS
│   │   ├── whatsapp_opt_ins.py                # NEW — list, manual create (admin/imports), revoke
│   │   ├── whatsapp_scheduled_sends.py        # NEW — list, create, cancel
│   │   └── whatsapp_dead_letters.py           # NEW — list, get, replay
│   └── webhooks/
│       ├── whatsapp.py                        # EXISTS — extend: STOP-keyword detector + opt-out flip; route template_status events
│       └── storefront/                        # (if exists) — storefront checkout opt-in write goes through stores/whatsapp_opt_ins
│
├── api/v1/schemas/
│   └── tenant/
│       ├── whatsapp_opt_in.py                 # NEW
│       ├── whatsapp_scheduled_send.py         # NEW
│       ├── whatsapp_dead_letter.py            # NEW
│       └── whatsapp.py                        # EXISTS — extend with ConnectionStatus, BYOConnectRequest, ValidationFailure schemas
│
└── config/
    └── settings.py                            # EXISTS — extend: platform Meta app webhook config + whatsapp_app_id

alembic/versions/
├── 20260413_add_whatsapp_tables.py            # EXISTS — initial whatsapp tables
└── 20260524_add_whatsapp_optin_scheduled_dl.py # NEW — three new tables + RLS + indexes + system template seeds

tests/
├── integration/whatsapp/
│   ├── test_order_created_handler.py          # NEW
│   ├── test_order_paid_handler.py             # NEW
│   ├── test_stop_keyword_optout.py            # NEW
│   ├── test_scheduled_send_dispatcher.py      # NEW
│   ├── test_cascade_cancel_on_order_cancel.py # NEW
│   ├── test_byo_credential_validation.py      # NEW (3-step + failure cases)
│   ├── test_template_submission_to_meta.py    # NEW
│   ├── test_template_status_webhook.py        # NEW
│   ├── test_dead_letter_replay.py             # NEW
│   ├── test_dead_letter_purge.py              # NEW
│   └── test_two_tier_optin_guard.py           # NEW (utility vs marketing)
├── unit/whatsapp/
│   ├── test_send_guard.py                     # NEW — pure-logic table of (state) → (allowed, reason)
│   ├── test_stop_keyword_detector.py          # NEW — first-word, normalization, Arabic variants
│   └── test_template_category_classifier.py   # NEW
└── fixtures/
    └── whatsapp.py                             # NEW — opt-in factories, scheduled-send factories, Meta API respx mocks
```

**Structure Decision**: Standard NUMU-api layered structure (clean architecture — `domain/` pure logic, `application/use_cases/` orchestration, `infrastructure/` I/O, `api/` HTTP surface). The WhatsApp module already follows this; this feature adds three repositories, one orchestration use-case per FR group, three Celery tasks, three new tables, and four new API route modules. No restructure of existing code.

## Phase 0 — Research

See **[research.md](./research.md)**. All NEEDS-CLARIFICATION items from the spec were resolved during `/speckit-clarify` (5 questions answered). Phase 0 research covers operational unknowns:

1. Meta `message_template_status_update` webhook field — subscription mechanism + payload shape
2. Meta BYO credential validation calls — exact endpoints, scope strings, expected error shapes
3. Celery exponential backoff configuration in this codebase — existing pattern survey
4. PostgreSQL RLS policy template — how `app.current_store_id` is already wired
5. Idempotency key strategy for OrderCreatedEvent/OrderPaidEvent dedup — choose between `message_log` lookup vs new dedup table

## Phase 1 — Design & Contracts

See **[data-model.md](./data-model.md)**, **[contracts/](./contracts/)**, **[quickstart.md](./quickstart.md)**.

Post-design Constitution re-check: still PASS. No design choice introduced a violation; the new schema follows the same RLS / async / Pydantic-v2 / strict-typing patterns as existing tenant tables. No Complexity Tracking entries required.

## Complexity Tracking

No constitution gate violations to justify; this section is empty by design.
