# Implementation Plan: COD Autopilot for Manual-Shipping Merchants

**Branch**: `004-cod-autopilot` | **Date**: 2026-07-18 | **Spec**: [spec.md](spec.md)
**Input**: Feature specification from `/specs/004-cod-autopilot/spec.md`

## Summary

Automate the `shipped` and `delivered+paid` hops of the COD order lifecycle for merchants using non-integrated external couriers, by capturing the two human-held signals over WhatsApp: a once-daily merchant ship-digest with an "All shipped" quick-reply (plus a strict numeric-exceptions text grammar), and a customer delivery-check with Received/Not-yet/Refused buttons, backed by celery-beat timers and an assumed-delivered fallback so 100% of orders reach a terminal state. Everything reuses the existing WhatsApp Meta Cloud pipeline (template registry → seed migration → submission script; send guard gates; inbound button-payload webhook dispatch), the existing `UpdateOrderStatusUseCase` choke point (extended with a `source` attribution), and the `cod_auto_rto_task` beat-task pattern. Assumed-delivered closures are excluded from trust-network delivery events (never full weight). Two new RLS-protected tenant tables hold conversation/scheduling state; a derived exception queue surfaces the orders Autopilot couldn't close.

## Technical Context

**Language/Version**: Python 3.11 (existing backend), TypeScript/React (merchant-hub view, separate repo)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Celery + beat (crontab schedules), Meta WhatsApp Cloud API (existing `messaging_service.py`), Alembic
**Storage**: PostgreSQL (Supabase) — 2 new tenant tables with RLS; JSONB extensions to `store.settings` and `order.metadata`; no `network_reputation` changes
**Testing**: pytest (unit + integration), existing layouts under `tests/unit/tasks/`, `tests/unit/core/whatsapp/`, `tests/integration/whatsapp/`, `tests/unit/use_cases/`
**Target Platform**: Linux server (existing prod EC2 compose: api + celery worker + beat + redis)
**Project Type**: web-service (backend) + thin frontend view in `numo-merchant-hub` (separate repository — tracked here, implemented there)
**Performance Goals**: beat sweeps are background work — hourly scans bounded (limit-batched like `cod_auto_rto_task`'s 500-row cap); webhook button handling stays within Meta's response expectations (handler already returns 200 immediately per existing pattern); customer-tap → order closed < 1 min (SC-003)
**Constraints**: WhatsApp sends only through the existing guard gates (FR-015); no n8n; no courier APIs; Meta template approval gates activation (long pole — submit first); no-emoji templates; UTILITY category (no marketing frequency cap)
**Scale/Scope**: all Autopilot-enabled stores platform-wide; ≤10 orders per digest message (Meta body limit, R-09); 3 delivery-check attempts max per order

## Constitution Check

*GATE: evaluated pre-Phase-0 and re-checked post-Phase-1 design. Constitution v1.0.0.*

| Principle | Status | Evidence |
|---|---|---|
| I. Privacy by Hashing | ✅ PASS | No new cross-store data flow. Only network write path remains the existing `write_network_event` with `extract_phone_hash_from_string`; new tables are tenant-scoped (raw phone allowed inside tenant). R-06 *narrows* network emission. |
| II. GDPR Recital 47 Fidelity | ✅ PASS | All four declarations in research.md R-13: legal basis (contract performance for delivery-check; B2B for digest), DSAR path (tenant-scoped rows join existing export), erasure path (delivery-check rows added to redaction sweep), opt-out effect (guard-blocked sends → fallback path; no new aggregates). |
| III. Spec-First, Tests From Spec | ✅ PASS | Spec merged on branch; acceptance scenarios map to test anchors (quickstart §8); tests ship with implementation in same PR. |
| IV. Async-First, Strictly Typed | ✅ PASS | All new code async (`AsyncSessionLocal`, async repos), Pydantic v2 boundary schemas for settings + exceptions endpoints, MyPy strict on new files. Beat tasks use the existing sync-wrapper/`_run_async` bridge (established pattern). |
| V. Tenant Isolation by RLS | ✅ PASS | Both new tables created with ENABLE+FORCE RLS and full policy set in the same migration (data-model §1–2, cloning `20260524_010000` `_add_rls_for_table`). Beat tasks use `enable_rls_bypass` scan + `narrow_to_tenant` writes (existing sanctioned pattern). |
| Alembic discipline | ✅ PASS | Two migrations (schema+RLS; template seed), each with `down()`; forward-only in prod. |
| Celery naming/idempotency | ✅ PASS | `tasks.cod_autopilot_*` in `src/infrastructure/messaging/tasks/cod_autopilot_tasks.py`; every task idempotent (unique constraints + metadata stamps + message_log event tags). |
| Contract versioning | ✅ PASS | Additive endpoints/fields only; no `/api/v1` shape changes (contracts/api.md §3). |
| Secret hygiene / mock isolation | ✅ PASS | No new secrets; templates use platform WABA creds via existing credential resolution; fixtures under `tests/fixtures/`. |

**Post-Phase-1 re-check**: PASS — design introduced no violations; Complexity Tracking empty.

## Project Structure

### Documentation (this feature)

```text
specs/004-cod-autopilot/
├── plan.md              # This file
├── spec.md              # Feature specification (committed)
├── research.md          # Phase 0 — 13 resolved decisions R-01..R-13
├── data-model.md        # Phase 1 — tables, state machines, settings block
├── quickstart.md        # Phase 1 — local E2E verification path
├── contracts/
│   └── api.md           # Phase 1 — HTTP + beat-task + webhook + template contracts
├── checklists/requirements.md
├── version-guard-report.md  # Skipped (Python repo, no npm sources)
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created by plan)
```

### Source Code (repository root: NUMU-api)

```text
src/
├── core/
│   ├── entities/order.py                          # transition_to(source=…) additive param (R-05)
│   ├── whatsapp_rich_templates.py                 # +cod_ship_digest_v1, +order_delivery_check_v1 (R-08)
│   ├── services/whatsapp_send_guard.py            # unchanged (pure guard reused)
│   └── enums/whatsapp.py                          # new pref keys / event tags if enum-backed
├── application/
│   ├── services/
│   │   ├── order_confirmation_service.py          # payload parser gains new actions (R-03)
│   │   └── cod_autopilot_service.py               # NEW — digest build/send, delivery-check lifecycle,
│   │                                              #   exceptions grammar (R-04), reply handlers
│   └── use_cases/orders/update_order_status.py    # source param; delivery event gated on source (R-06)
├── infrastructure/
│   ├── database/models/tenant/
│   │   ├── whatsapp_delivery_check.py             # NEW model
│   │   └── whatsapp_ship_digest.py                # NEW model
│   ├── repositories/
│   │   ├── whatsapp_delivery_check_repository.py  # NEW
│   │   └── whatsapp_ship_digest_repository.py     # NEW
│   ├── messaging/
│   │   ├── celery_app.py                          # +imports entry, +3 beat_schedule entries
│   │   └── tasks/cod_autopilot_tasks.py           # NEW — 3 tasks (contracts/api.md §4)
│   ├── events/handlers/whatsapp_notification_handler.py  # +pref keys in _WA_PREF_KEYS (R-10)
│   └── external_services/whatsapp/messaging_service.py   # +send_ship_digest, +send_delivery_check
├── api/v1/
│   ├── routes/stores/settings.py                  # GET/PATCH /settings/cod-autopilot (R-12)
│   ├── routes/stores/orders.py                    # GET/POST autopilot-exceptions (R-11)
│   ├── routes/webhooks/whatsapp.py                # _action_handlers += shipall/dlvyes/dlvnot/dlvref;
│   │                                              #   merchant free-text digest-reply branch
│   └── schemas/tenant/settings.py                 # CodAutopilotResponse / UpdateCodAutopilotRequest
alembic/versions/
├── <rev>_add_cod_autopilot_tables.py              # 2 tables + RLS (clone 20260524_010000 helper)
└── <rev>_seed_cod_autopilot_templates.py          # template seed (clone 20260601_002000)
scripts/submit_platform_whatsapp_templates.py      # picks up new registry entries (submit FIRST — long pole)

tests/
├── unit/tasks/test_cod_autopilot_tasks.py
├── unit/services/test_autopilot_reply_parsing.py
├── unit/core/whatsapp/test_send_guard.py          # additions
├── unit/use_cases/test_update_order_status_network.py  # additions (source-gated event)
└── integration/whatsapp/test_autopilot_webhook_actions.py
```

**Separate repository** (`numo-merchant-hub`, out of this repo's task scope but part of the feature): exceptions view on `src/pages/Orders.tsx` + `cod-autopilot` settings card; consumes contracts/api.md §1–2.

**Structure Decision**: Single-project backend layout (existing clean-architecture split core/application/infrastructure/api) — all new modules slot into existing directories; one new application service, one new task module, two new models/repos. No structural additions.

## Delivery sequencing (informs /speckit-tasks)

1. **Template registry + seed migration + Meta submission** — approval is the schedule's long pole; everything else proceeds while PENDING (guard blocks sends until APPROVED, which is also the rollout switch).
2. **Foundations**: `source` attribution (entity + DTO + use case + R-06 event gating) → tables/models/repos/migration → settings endpoint.
3. **Flows**: delivery-check service + beat task + webhook actions (P1 story) → digest service + beat task + shipall/free-text handling (P2) → assumed-delivered sweep (P3) → exceptions endpoints (P4).
4. **Merchant-hub view** (separate repo) + staging smoke test per quickstart §7.

## Complexity Tracking

*No constitution violations — table intentionally empty.*
