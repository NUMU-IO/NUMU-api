# Implementation Plan: Marketing Campaigns v2 — Shopify-style rebuild + NUMU extras

**Branch**: `002-marketing-campaigns-v2` | **Date**: 2026-05-24 | **Spec**: [spec.md](./spec.md)
**Input**: Feature specification from `specs/002-marketing-campaigns-v2/spec.md`

## Summary

Rebuild the merchant-hub campaign detail page to mirror Shopify's `Marketing > Campaigns > Create campaign` layout, restructure the marketing nav with a Marketing parent containing Campaigns + Attribution, and ship 6 NUMU-exclusive enhancements (auto-match rules, manual attribution backfill, one-click duplicate, cross-campaign comparison, AI optimization tips, best-time-to-send picker).

The implementation spans **2 repos**: NUMU-api (backend) for new aggregation queries, 2 new tables (`campaign_auto_match_rules`, `campaign_activities`), Celery backfill task, and 11+ new endpoints; numo-merchant-hub (frontend) for the Shopify-style detail page layout, sidebar restructure, comparison page, and Schedule-dialog chip integration. No storefront changes required (user-agent capture already exists on funnel events).

## Technical Context

**Language/Version**: Python 3.12 (NUMU-api), TypeScript 5.x + React 18.3 (numo-merchant-hub)
**Primary Dependencies**: FastAPI 0.115+, SQLAlchemy 2.0 async, Alembic, Pydantic v2, Celery 5, asyncpg (backend); Vite 6, React Router 7, TanStack Query 5, lucide-react, sonner, shadcn/ui components (frontend)
**Storage**: PostgreSQL 16 with RLS (existing); 2 new tables + 2 column additions on existing tables
**Testing**: pytest with pytest-asyncio (backend, ~121 existing test files); Vitest (frontend)
**Target Platform**: Linux server (test/stage droplets at 188.166.156.151), Chrome/Safari/Firefox on desktop + mobile (test storefront + hub)
**Project Type**: Multi-repo web service. Backend = FastAPI Python service, frontend = React SPA merchant hub. Storefronts untouched by this feature.
**Performance Goals**: Detail-page panel queries p95 ≤ 800ms over 30-day window (SC-003); cross-campaign compare render ≤ 1500ms p95 (SC-012); best-time chip render ≤ 200ms (SC-010); backfill ≤ 30s for 90-day single-store window (SC-007)
**Constraints**: All new endpoints must respect RLS (constitution V); all new code async + strict-typed (constitution IV); Alembic migrations include RLS policies in the same file as the table they protect (constitution V + additional constraints); idempotent backfills (FR-027)
**Scale/Scope**: Single-store scope per request. Comparison capped at 4 campaigns. Backfill window capped at 365 days. ~50-200 funnel events / order ratio in production data so far; new analytics queries must use partial indexes from feature 001's migrations (`ix_funnel_events_store_campaign_created`, `ix_orders_store_campaign_created`)

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
| --------- | ------ | ----- |
| **I. Privacy by Hashing** | ✅ N/A | Feature touches existing customer/order data; introduces NO new cross-store data flows. Auto-match rules + backfill operate purely within `tenant_id` scope. No new hashing needed. |
| **II. GDPR Recital 47 Fidelity** | ✅ Pass | All new data is per-tenant. Customer erasure already cascades via existing `customers.id` FK — `orders.campaign_id` and `funnel_events.campaign_id` are not customer-identifying. New `campaign_activities` log stores merchant action history (not customer data); not subject to customer-redact. DSAR: campaign attribution already included in order export. Documented in research.md §3. |
| **III. Spec-First, Tests From Spec** | ✅ Pass | spec.md is complete + merged-ready. Each FR has a corresponding acceptance scenario; `/speckit-tasks` will generate the test tasks. |
| **IV. Async-First, Strictly Typed** | ✅ Pass | All new endpoints `async def`, SQLAlchemy 2.0 async sessions, Pydantic v2 schemas. MyPy strict already enforced repo-wide. |
| **V. Tenant Isolation by RLS** | ✅ Pass | Two new tables (`campaign_auto_match_rules`, `campaign_activities`) — both will get `ENABLE ROW LEVEL SECURITY` + `FOR ALL TO PUBLIC USING (tenant_id = current_setting('app.tenant_id')::uuid)` policy in their own Alembic migration. Existing `orders`/`funnel_events` updates inherit existing RLS. |

| Additional Constraint | Status | Notes |
| --------------------- | ------ | ----- |
| Alembic discipline (separate migration per schema change) | ✅ Pass | 2 migrations planned (one per new table). Both include RLS policies + indexes. |
| Celery task naming convention | ✅ Pass | New task: `numu_api.marketing.backfill_campaign_attribution` under `src/infrastructure/messaging/tasks/marketing_tasks.py`. Idempotent by design (FR-027). |
| Contract-versioned API responses | ✅ Pass | All new endpoints additive under `/api/v1/...`. No field removal from existing endpoints. No version bump. |
| Secret hygiene | ✅ N/A | No new secrets, no credential changes. |
| Hybrid sync/async risk scoring | ✅ N/A | Not a risk-scoring feature. |
| Safe defaults for order mutations | ✅ N/A | Feature does not mutate orders beyond setting `campaign_id` (FK adjustment, not lifecycle change). |
| Additive Shopify mutations | ✅ N/A | No Shopify-app surface touched. |

**Result**: 0 violations. Proceed to Phase 0.

## Project Structure

### Documentation (this feature)

```text
specs/002-marketing-campaigns-v2/
├── plan.md                          # This file
├── spec.md                          # Feature spec
├── research.md                      # Phase 0 — tech decisions, library picks
├── data-model.md                    # Phase 1 — 2 new entities + impact on existing
├── contracts/                       # Phase 1 — 11 new endpoints
│   ├── analytics-breakdowns.md      # 5 chart-panel endpoints
│   ├── auto-match-rules.md          # 3 CRUD endpoints
│   ├── activities.md                # 2 backfill endpoints
│   ├── campaign-actions.md          # duplicate + compare endpoints
│   └── send-time-suggestions.md     # best-time chip endpoint
├── quickstart.md                    # Phase 1 — manual verification script
├── checklists/
│   └── requirements.md              # Existing — spec validation
├── version-guard-report.md          # Existing — skip (no npm in this repo)
└── tasks.md                         # Phase 2 — created by /speckit-tasks
```

### Source Code (existing repos)

```text
NUMU-api (C:\Users\Yahia\NUMU\NUMU-api)
├── alembic/versions/
│   ├── 20260524_010000_add_campaign_auto_match_rules.py    (new)
│   └── 20260524_020000_add_campaign_activities.py          (new)
├── src/
│   ├── api/v1/routes/stores/
│   │   ├── marketing_campaigns.py                          (extend: 5 breakdown + duplicate + compare endpoints)
│   │   ├── marketing_campaign_rules.py                     (new: CRUD for auto-match rules)
│   │   ├── marketing_campaign_activities.py                (new: backfill endpoints)
│   │   └── marketing_send_times.py                         (new: best-time chip endpoint)
│   ├── application/services/
│   │   ├── campaign_auto_match.py                          (new: rule evaluation at ingest)
│   │   ├── campaign_backfill.py                            (new: attribution backfill service)
│   │   ├── campaign_tips.py                                (new: heuristic optimization tips)
│   │   ├── campaign_duplicate.py                           (new: duplicate copy semantics)
│   │   └── send_time_suggester.py                          (new: best-time analyzer)
│   ├── core/entities/
│   │   ├── campaign_auto_match_rule.py                     (new entity)
│   │   └── campaign_activity.py                            (new entity)
│   ├── infrastructure/
│   │   ├── database/models/tenant/
│   │   │   ├── campaign_auto_match_rule.py                 (new SQLAlchemy model)
│   │   │   └── campaign_activity.py                        (new SQLAlchemy model)
│   │   ├── repositories/
│   │   │   ├── analytics_repository.py                     (extend: 5 new aggregations)
│   │   │   ├── campaign_auto_match_repository.py           (new)
│   │   │   └── campaign_activity_repository.py             (new)
│   │   └── messaging/tasks/
│   │       └── marketing_tasks.py                          (new: backfill_campaign_attribution Celery task)
│   └── application/services/funnel_event_ingest.py         (extend: call auto_match before short_code resolution)
└── tests/
    ├── unit/
    │   ├── test_campaign_auto_match.py                     (new)
    │   ├── test_campaign_backfill.py                       (new)
    │   ├── test_campaign_tips.py                           (new)
    │   ├── test_campaign_duplicate.py                      (new)
    │   ├── test_send_time_suggester.py                     (new)
    │   └── test_analytics_campaign_breakdowns.py           (new)
    └── integration/
        ├── test_campaign_auto_match_e2e.py                 (new)
        ├── test_campaign_backfill_e2e.py                   (new)
        └── test_campaign_compare_e2e.py                    (new)

numo-merchant-hub (C:\Users\Yahia\NUMU\numo-merchant-hub)
├── src/
│   ├── components/layout/
│   │   └── AppSidebar.tsx                                  (restructure: Marketing parent)
│   ├── pages/
│   │   ├── MarketingCampaigns.tsx                          (extend: multi-select for compare)
│   │   ├── MarketingCampaignDetail.tsx                     (rebuild: Shopify-style layout)
│   │   ├── MarketingAttribution.tsx                        (new: consolidated LTV + Multi-touch)
│   │   └── MarketingCampaignsCompare.tsx                   (new: 2-4 campaign side-by-side)
│   ├── components/campaigns/
│   │   ├── CampaignDetailHeader.tsx                        (new: sticky header w/ pills)
│   │   ├── CampaignSidebar.tsx                             (new: 320px right panel)
│   │   ├── CampaignKpiCards.tsx                            (new: 4-card row)
│   │   ├── CampaignChartGrid.tsx                           (new: 8-panel grid)
│   │   ├── CampaignAutoMatchPanel.tsx                      (new: rule editor)
│   │   ├── CampaignActivitiesPanel.tsx                     (new: backfill log + runner)
│   │   ├── CampaignTipsPanel.tsx                           (new: AI tips card)
│   │   ├── ScheduleDialog.tsx                              (extend: best-time chips)
│   │   └── panels/
│   │       ├── SessionsByChannelPanel.tsx                  (new)
│   │       ├── SalesByChannelPanel.tsx                     (new)
│   │       ├── SessionsByUtmPanel.tsx                      (new)
│   │       ├── SalesByUtmPanel.tsx                         (new)
│   │       ├── OrdersNewVsReturningPanel.tsx               (new)
│   │       ├── SalesByOrderSizePanel.tsx                   (new)
│   │       ├── ItemsSoldByProductPanel.tsx                 (new)
│   │       └── SessionsByDevicePanel.tsx                   (new)
│   └── services/
│       └── campaignApi.ts                                  (extend: 11 new endpoints)
└── tests/
    └── unit/
        ├── CampaignAutoMatchPanel.test.tsx                 (new)
        ├── CampaignTipsPanel.test.tsx                      (new)
        └── ScheduleDialog.test.tsx                         (new — best-time chips)
```

**Structure Decision**: Multi-repo extension of existing structure. No new top-level directories in either repo. Backend follows the established Clean Architecture layering (core/entities → application/services → infrastructure/repositories + api/v1/routes). Frontend follows the established pages + components + services pattern under `src/`. Two new Alembic migrations (one per new table); both include RLS policies.

## Complexity Tracking

> No constitution violations — section omitted intentionally.

The 9 user stories are independently shippable in the order P1 (US1-3) → P2 (US4-5) → P3 (US6-9). Within P1, US3 has the largest surface area (8 chart panels + 5 new aggregations). Within P3, US7 (compare) and US9 (best-time) require precomputation paths but no schema changes; US6 (duplicate) is one endpoint; US8 (tips) is one heuristic service.
