---
description: "Task list for marketing-campaigns-v2 — 9 user stories, ~110 tasks across backend + frontend"
---

# Tasks: Marketing Campaigns v2 — Shopify-style rebuild + NUMU extras

**Input**: Design documents from `specs/002-marketing-campaigns-v2/`
**Prerequisites**: plan.md ✓, spec.md ✓, research.md ✓, data-model.md ✓, contracts/ ✓ (5 files)

**Tests**: Included per constitution Principle III ("Spec-First, Tests From Spec — NON-NEGOTIABLE"). Each user story has unit + integration tests written before the code under it.

**Organization**: Tasks are grouped by user story. After Foundational (Phase 2), P1 stories (US1, US2, US3) can run in parallel; P2 (US4, US5) can run in parallel once their migrations land; P3 (US6, US7, US8, US9) are fully independent and can run in any order/parallelism.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Different file, no in-flight dependency — can run in parallel
- **[Story]**: US1-US9 — only on user-story phase tasks
- All paths absolute or repo-relative; backend = `NUMU-api/`, frontend = `numo-merchant-hub/`

## Path Conventions

- **Backend**: `NUMU-api/src/...`, tests at `NUMU-api/tests/...`, migrations at `NUMU-api/alembic/versions/`
- **Frontend**: `numo-merchant-hub/src/...`, tests at `numo-merchant-hub/src/**/*.test.tsx`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Add new dependencies and scaffold empty files so subsequent tasks have somewhere to land.

- [ ] T001 Add `ua-parser` to `NUMU-api/requirements.txt` (or `pyproject.toml` if used); run `pip install -r requirements.txt` locally to verify install
- [ ] T002 [P] Create empty new-route page files in frontend so router compiles: `numo-merchant-hub/src/pages/MarketingAttribution.tsx`, `MarketingCampaignsCompare.tsx` (each exports a stub default component returning `<div>Coming soon</div>`)
- [ ] T003 [P] Create empty backend route module files so app imports succeed: `NUMU-api/src/api/v1/routes/stores/marketing_campaign_rules.py`, `marketing_campaign_activities.py`, `marketing_send_times.py` (each defines an empty `APIRouter`)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: DB schema changes + base SQLAlchemy models + nav-route registration. ALL user stories depend on these.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

### Migrations (sequential — same alembic head)

- [ ] T004 Create Alembic migration `NUMU-api/alembic/versions/20260524_010000_add_campaign_auto_match_rules.py` per data-model.md — table + 2 indexes + 1 unique constraint + RLS policy with tenant-scoping. `down_revision` = current head on dev. Include reverse `downgrade()`.
- [ ] T005 Create Alembic migration `NUMU-api/alembic/versions/20260524_020000_add_campaign_activities.py` per data-model.md — table + 2 indexes (one partial) + RLS policy. `down_revision` = T004's revision.
- [ ] T006 Create Alembic migration `NUMU-api/alembic/versions/20260524_030000_add_funnel_events_device.py` per data-model.md — adds nullable `device` column + partial index. `down_revision` = T005's revision.
- [ ] T007 Run all 3 migrations locally (`python -m alembic upgrade head`) and verify `alembic current` reports the T006 revision as the new head. Verify the 2 new tables + the device column exist via `\d+ campaign_auto_match_rules`, `\d+ campaign_activities`, `\d+ funnel_events`.

### Models + entities (parallel after T007)

- [ ] T008 [P] Create core entity `NUMU-api/src/core/entities/campaign_auto_match_rule.py` with dataclass fields matching data-model.md
- [ ] T009 [P] Create core entity `NUMU-api/src/core/entities/campaign_activity.py` with dataclass fields + status enum
- [ ] T010 [P] Create SQLAlchemy model `NUMU-api/src/infrastructure/database/models/tenant/campaign_auto_match_rule.py` — mirrors T008 entity, RLS-aware, uses existing `TenantScopedModelMixin`
- [ ] T011 [P] Create SQLAlchemy model `NUMU-api/src/infrastructure/database/models/tenant/campaign_activity.py` — mirrors T009 entity, with status enum using `values_callable` per the lowercase-enum convention noted in MEMORY.md
- [ ] T012 [P] Extend SQLAlchemy model `NUMU-api/src/infrastructure/database/models/tenant/funnel_event.py` — add nullable `device: Mapped[str | None]` column
- [ ] T013 [P] Register new SQLAlchemy models in `NUMU-api/src/infrastructure/database/models/tenant/__init__.py`

### Frontend nav stub (parallel after backend skeleton stable)

- [ ] T014 [P] Register the new routes in `numo-merchant-hub/src/App.tsx` — `/marketing/attribution` → `<MarketingAttribution />` and `/campaigns/compare` → `<MarketingCampaignsCompare />`. Plus redirects from `/analytics/ltv` and `/analytics/multi-touch` to `/marketing/attribution` with the appropriate `?tab=` query.

**Checkpoint**: Foundation ready — user story implementation can now begin.

---

## Phase 3: User Story 1 — Marketing nav restructure (Priority: P1)

**Goal**: A Marketing parent in the sidebar containing Campaigns + Attribution sub-items.

**Independent Test**: Open the hub, expand "Marketing", see "Campaigns" + "Attribution". Each navigates correctly. RTL pass.

### Tests for User Story 1

- [ ] T015 [P] [US1] Write `numo-merchant-hub/src/components/layout/AppSidebar.test.tsx` — assert (a) "Marketing" parent renders, (b) "Campaigns" + "Attribution" sub-items inside, (c) clicking parent collapses/expands, (d) RTL flips chevron direction, (e) Arabic locale shows "التسويق"

### Implementation for User Story 1

- [ ] T016 [US1] Modify `numo-merchant-hub/src/components/layout/AppSidebar.tsx` — wrap the existing top-level Campaigns entry inside a new collapsible `Marketing` parent group (icon `Send`), add `Attribution` sub-item linking to `/marketing/attribution`. Update translations object (`isAr ? "التسويق" : "Marketing"` etc.). Email Templates + WhatsApp stay top-level.

**Checkpoint**: US1 complete; sidebar grouping live.

---

## Phase 4: User Story 2 — Consolidated Attribution page (Priority: P1)

**Goal**: Single `/marketing/attribution` page hosting LTV + Multi-touch tabs under one date range picker.

**Independent Test**: Navigate to `/marketing/attribution`. See date picker + 2 tabs. Changing the range updates both. Old `/analytics/ltv` URL redirects.

### Tests for User Story 2

- [ ] T017 [P] [US2] Write `numo-merchant-hub/src/pages/MarketingAttribution.test.tsx` — assert (a) page renders with date picker + 2 tabs, (b) tab content is the existing LTV + Multi-touch component bodies, (c) date range state shared across tabs, (d) attribution model selector visible on Multi-touch tab only

### Implementation for User Story 2

- [ ] T018 [US2] Build `numo-merchant-hub/src/pages/MarketingAttribution.tsx` — page-level date range picker, shadcn/ui `Tabs` with two children. Tab 1 hosts the existing LTV component body (extracted from `LtvByChannelPage`). Tab 2 hosts the existing Multi-touch component body (extracted from `MultiTouchAttributionPage`). Pass date range as props.
- [ ] T019 [US2] Add redirect logic — when route loads as `/marketing/attribution?from=ltv`, default to LTV tab; from Multi-touch redirect, default to Multi-touch tab + preserve attribution model. Use `useSearchParams`.
- [ ] T020 [US2] Update `numo-merchant-hub/src/App.tsx` legacy routes: `/analytics/ltv` → `<Navigate to="/marketing/attribution?from=ltv" replace />`; `/analytics/multi-touch` → `<Navigate to="/marketing/attribution?from=multi-touch&model=..." replace />`
- [ ] T021 [US2] Delete the standalone `LtvByChannelPage.tsx` and `MultiTouchAttributionPage.tsx` pages (their bodies were extracted as tab content). Remove their App.tsx route entries.

**Checkpoint**: US2 complete; merchants navigate to a single Attribution page.

---

## Phase 5: User Story 3 — Shopify-style Campaign Detail layout (Priority: P1)

**Goal**: The biggest story — full layout rebuild + 8 new chart panels + 4 KPI cards + permanent right sidebar.

**Independent Test**: Open a campaign with attributed traffic. See sticky header, right sidebar, 4 KPI cards, 8-panel chart grid. Changing date range / attribution model refreshes everything. Mobile collapse works.

### Backend tests for User Story 3

- [ ] T022 [P] [US3] Write `NUMU-api/tests/unit/test_analytics_campaign_breakdowns.py` — parameterized tests for each of the 5 new aggregation methods. Cover: empty window → empty list, single-channel data, multi-channel split, NULL utm_source bucketed as "direct", new vs returning logic, device classification, order-size histogram bin boundaries.
- [ ] T023 [P] [US3] Write `NUMU-api/tests/integration/test_campaign_breakdowns_e2e.py` — fixture creates a campaign with synthetic orders + funnel events across 3 channels / 2 devices. Hit all 5 endpoints. Assert payload shape matches `contracts/analytics-breakdowns.md`.

### Backend implementation for User Story 3

- [ ] T024 [US3] Add `campaign_breakdown_channel()` method to `NUMU-api/src/infrastructure/repositories/analytics_repository.py` — single query that returns `(utm_source-coalesced-to-direct, sessions, sales_cents)` rows. Respects `_NON_REVENUE_STATUSES`. Tenant filter on both sides per Sentry-finding pattern from feature 001.
- [ ] T025 [US3] Add `campaign_breakdown_utm()` method — group by 5-tuple of UTM fields, top N by sessions, includes sales_cents. Same defense-in-depth tenant filter.
- [ ] T026 [US3] Add `campaign_breakdown_customer_type()` method — joins to `customers.first_touch_at`; "new" = first_touch_at inside window AND first attributed order is this campaign; "returning" otherwise. Defense-in-depth.
- [ ] T027 [US3] Add `campaign_breakdown_order_size()` method — fixed 10-bin histogram via `CASE WHEN total < 5000 THEN 0 WHEN ... END`. Returns counts per bin.
- [ ] T028 [US3] Add `campaign_breakdown_device()` method — group by `funnel_events.device`, coalesce NULL to "unknown".
- [ ] T029 [US3] Add UA classifier helper `NUMU-api/src/application/services/device_classifier.py` — `classify(user_agent: str | None) -> Literal['mobile', 'tablet', 'desktop'] | None` using `ua-parser`. NULL UA → NULL.
- [ ] T030 [US3] Modify `NUMU-api/src/application/services/funnel_event_ingest.py` — call `device_classifier.classify(request.user_agent)` and persist on `funnel_events.device` at insert time. Existing tests must still pass.
- [ ] T031 [US3] Add 5 GET routes to `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` under existing `/{campaign_id}/breakdown/*`. Each accepts `?date_from&date_to&attribution_model`. Wire to T024-T028 repository methods. 400 if date window > 365 days.
- [ ] T032 [US3] Write `NUMU-api/tests/unit/test_device_classifier.py` — fixture matrix of UA strings (iPhone, iPad, Android phone, Android tablet, Chrome desktop, curl, empty) → expected classifications

### Frontend tests for User Story 3

- [ ] T033 [P] [US3] Write `numo-merchant-hub/src/pages/MarketingCampaignDetail.test.tsx` — assert (a) sticky header with breadcrumb + status badge + date pill + attribution pill + 3 action buttons, (b) right sidebar with 6 sub-sections, (c) 4 KPI cards rendered, (d) 8 chart panels rendered, (e) panels show "No data for this date range." when API returns empty, (f) date-range change triggers refetch on all panels

### Frontend implementation for User Story 3

- [ ] T034 [P] [US3] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add 5 client functions: `getCampaignBreakdownChannel`, `getCampaignBreakdownUtm`, `getCampaignBreakdownCustomerType`, `getCampaignBreakdownOrderSize`, `getCampaignBreakdownDevice` matching `contracts/analytics-breakdowns.md` payloads
- [ ] T035 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/CampaignDetailHeader.tsx` — sticky header (Tailwind `sticky top-0 z-10`), breadcrumb + title + status badge + date-range pill (shadcn/ui `Popover`) + attribution-model pill (shadcn/ui `Select`) + existing action buttons (Send Now / Schedule / Cancel)
- [ ] T036 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/CampaignSidebar.tsx` — 320px container with shadcn/ui `Accordion` for: campaign name (inline editable via `Input` w/ debounced PUT to existing campaign-update endpoint), short_code + copy button, Shareable Links section (host `<TrackableLinkBuilder />`), placeholder slots for AutoMatch / Activities / Tips panels (populated in US4/US5/US8)
- [ ] T037 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/CampaignKpiCards.tsx` — 4-card grid using existing performance endpoint data: Sessions, Sales (formatted EGP), Orders, Avg Order Value (formatted EGP). Loading skeletons + empty-state "—" when null.
- [ ] T038 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SessionsByChannelPanel.tsx` — Recharts `BarChart`; consumes T034's `getCampaignBreakdownChannel` result; explicit "No data for this date range." when empty
- [ ] T039 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SalesByChannelPanel.tsx` — Recharts `BarChart` over same payload as T038, sorting by sales_cents
- [ ] T040 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SessionsByUtmPanel.tsx` — sortable table (shadcn/ui `Table`) showing top combos by sessions
- [ ] T041 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SalesByUtmPanel.tsx` — same payload as T040, sorted by sales_cents
- [ ] T042 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/OrdersNewVsReturningPanel.tsx` — Recharts `PieChart` (donut), 2 slices, percentage labels
- [ ] T043 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SalesByOrderSizePanel.tsx` — Recharts `BarChart` over the fixed 10-bin histogram; x-axis labels formatted as price ranges
- [ ] T044 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/ItemsSoldByProductPanel.tsx` — table over existing `top_products` from the performance endpoint (no new endpoint needed)
- [ ] T045 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/panels/SessionsByDevicePanel.tsx` — Recharts `PieChart` (donut), 4 slices (mobile/desktop/tablet/unknown)
- [ ] T046 [P] [US3] Create `numo-merchant-hub/src/components/campaigns/CampaignChartGrid.tsx` — 2-column responsive grid hosting the 8 panel components from T038-T045; 1-column on `< lg` breakpoint
- [ ] T047 [US3] Rebuild `numo-merchant-hub/src/pages/MarketingCampaignDetail.tsx` — replaces the existing tabbed layout with the new structure: `<CampaignDetailHeader />` + grid `[main-grid] [sidebar]` where main = `<CampaignKpiCards />` + `<CampaignChartGrid />` and sidebar = `<CampaignSidebar />`. Sidebar collapses to a `Sheet` drawer below 1024px. Preserve all existing state (Send/Schedule/Cancel handlers).
- [ ] T048 [US3] Add inline name-edit handler in `<CampaignSidebar />` — debounced PUT to `/marketing/campaigns/{id}` with `{ name }`; on success, update local state + breadcrumb.

**Checkpoint**: US3 complete. Detail page is now Shopify-style. Merchants see KPI cards + 8 chart panels + permanent sidebar.

---

## Phase 6: User Story 4 — Auto-match rules (Priority: P2)

**Goal**: Per-campaign rules that auto-attribute incoming traffic by UTM patterns.

**Independent Test**: Create a rule via the sidebar editor. Send a matching funnel event. Verify the event's `campaign_id` is stamped without a short_code.

### Backend tests for User Story 4

- [ ] T049 [P] [US4] Write `NUMU-api/tests/unit/test_campaign_auto_match.py` — matrix: single rule equals/starts_with/contains; AND group; OR group; priority order across campaigns; short_code overrides rule (FR-019); no match → NULL campaign_id
- [ ] T050 [P] [US4] Write `NUMU-api/tests/integration/test_campaign_auto_match_e2e.py` — full ingest flow: POST `/track` with various UTM combos; assert correct `funnel_events.campaign_id` per fixture

### Backend implementation for User Story 4

- [ ] T051 [P] [US4] Create `NUMU-api/src/infrastructure/repositories/campaign_auto_match_repository.py` — `list_for_store(store_id) -> list[Rule]` (ordered by priority), `create_group(...)`, `delete_group(group_id)`
- [ ] T052 [P] [US4] Create `NUMU-api/src/application/services/campaign_auto_match.py` — `match(store_id, utms) -> campaign_id | None` evaluates groups in priority order, AND/OR combination, first-match-wins, request-scoped `lru_cache` on `(store_id, request_id)`
- [ ] T053 [US4] Modify `NUMU-api/src/application/services/funnel_event_ingest.py` — call `campaign_auto_match.match(...)` ONLY when short_code resolution returns None (per FR-019)
- [ ] T054 [US4] Add overlap-detection helper in T051's repository — `overlaps_existing(rule_conditions) -> list[ConflictingRule]` for the editor warning (FR-021)
- [ ] T055 [US4] Create CRUD endpoints in `NUMU-api/src/api/v1/routes/stores/marketing_campaign_rules.py` per `contracts/auto-match-rules.md` — GET list, POST create, DELETE; mount under `/{store_id}/marketing/campaigns/{campaign_id}/auto-match-rules`. Include the `warnings` field in POST responses.
- [ ] T056 [US4] Register the new router in `NUMU-api/src/api/v1/__init__.py` (or wherever the route registry lives)

### Frontend implementation for User Story 4

- [ ] T057 [P] [US4] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `listAutoMatchRules`, `createAutoMatchRule`, `deleteAutoMatchRule`
- [ ] T058 [P] [US4] Write `numo-merchant-hub/src/components/campaigns/CampaignAutoMatchPanel.test.tsx` — empty state, adding a rule, editing combinator, deleting
- [ ] T059 [US4] Create `numo-merchant-hub/src/components/campaigns/CampaignAutoMatchPanel.tsx` — list rules + "Add rule" CTA opens a shadcn/ui `Dialog` with field/operator/value selectors + AND/OR toggle + priority input + condition list (1-10 conditions). Surface overlap warnings inline.
- [ ] T060 [US4] Mount `<CampaignAutoMatchPanel />` inside the existing accordion slot in `CampaignSidebar.tsx` (T036's placeholder)

**Checkpoint**: US4 complete. Merchants can auto-attribute traffic without per-link short_codes.

---

## Phase 7: User Story 5 — Manual attribution backfill (Priority: P2)

**Goal**: Merchant runs a backfill to retroactively attribute past traffic to a campaign.

**Independent Test**: Run a backfill with `utm_source=instagram` filter over last 30 days. Activity log shows status=running → completed with affected_count. Re-run is idempotent (0 affected, X skipped).

### Backend tests for User Story 5

- [ ] T061 [P] [US5] Write `NUMU-api/tests/unit/test_campaign_backfill.py` — service function tests: filter SQL build, chunked UPDATE behavior, idempotency, skip-already-attributed semantics, error-path status=failed
- [ ] T062 [P] [US5] Write `NUMU-api/tests/integration/test_campaign_backfill_e2e.py` — fixture creates 100 historical orders/events with various UTMs. POST backfill, poll until status=completed, assert affected_count, query DB to verify `campaign_id` set.

### Backend implementation for User Story 5

- [ ] T063 [P] [US5] Create `NUMU-api/src/infrastructure/repositories/campaign_activity_repository.py` — `create(activity)`, `update_status(id, status, affected, skipped, error_message)`, `list_for_campaign(campaign_id, limit)`, `running_for_campaign(campaign_id)`
- [ ] T064 [P] [US5] Create `NUMU-api/src/application/services/campaign_backfill.py` — pure function `build_update_filter(utm_filters, starts_at, ends_at) -> sa.ColumnElement`. Idempotency clause `WHERE campaign_id IS NULL` included.
- [ ] T065 [US5] Create Celery task `NUMU-api/src/infrastructure/messaging/tasks/marketing_tasks.py::backfill_campaign_attribution` per `contracts/activities.md`. Uses T064 to build the filter, executes against orders + funnel_events in 5,000-row chunks via SAVEPOINT, updates activity row on completion/failure. Task name: `numu_api.marketing.backfill_campaign_attribution`.
- [ ] T066 [US5] Create endpoints in `NUMU-api/src/api/v1/routes/stores/marketing_campaign_activities.py` per `contracts/activities.md` — GET list, POST backfill (202 + enqueue task). Mount under `/{store_id}/marketing/campaigns/{campaign_id}/activities`. 409 on concurrent backfill.
- [ ] T067 [US5] Register the new router in the API package init.

### Frontend implementation for User Story 5

- [ ] T068 [P] [US5] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `listActivities`, `runBackfill`
- [ ] T069 [P] [US5] Write `numo-merchant-hub/src/components/campaigns/CampaignActivitiesPanel.test.tsx` — empty state, listing past activities, kicking off a backfill, polling behavior
- [ ] T070 [US5] Create `numo-merchant-hub/src/components/campaigns/CampaignActivitiesPanel.tsx` — list of past activities (most recent first) + "Run backfill" CTA opens a `Dialog` with filter editor (1-5 conditions) + date range. Polls every 3s while a `running` entry exists.
- [ ] T071 [US5] Mount `<CampaignActivitiesPanel />` inside the existing accordion slot in `CampaignSidebar.tsx`

**Checkpoint**: US5 complete. Merchants can retroactively attribute past traffic.

---

## Phase 8: User Story 6 — One-click duplicate (Priority: P3)

**Goal**: Duplicate button on list row + detail header creates a Draft clone.

**Independent Test**: Click Duplicate, see a new Draft with "(Copy)" suffix, same body/channel/audience, fresh short_code.

### Tests for User Story 6

- [ ] T072 [P] [US6] Write `NUMU-api/tests/unit/test_campaign_duplicate.py` — service test: copies the right fields, excludes the right fields, generates fresh short_code, status=draft
- [ ] T073 [P] [US6] Write `NUMU-api/tests/integration/test_campaign_duplicate_e2e.py` — POST /duplicate on a complete campaign, assert response shape + DB state

### Implementation for User Story 6

- [ ] T074 [US6] Create `NUMU-api/src/application/services/campaign_duplicate.py` — `duplicate(campaign_id, store_id, user_id) -> Campaign` reads source, applies copy/skip semantics per FR-029/030, generates new short_code via existing `generate_short_code()`, persists
- [ ] T075 [US6] Add POST endpoint in `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` at `/{campaign_id}/duplicate` per `contracts/campaign-actions.md` (201 + body)
- [ ] T076 [P] [US6] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `duplicateCampaign`
- [ ] T077 [US6] Add Duplicate button in `numo-merchant-hub/src/pages/MarketingCampaignDetail.tsx` header (alongside Send Now/Schedule/Cancel)
- [ ] T078 [US6] Add Duplicate icon-button on row hover in `numo-merchant-hub/src/pages/MarketingCampaigns.tsx` — calls T076, toasts success, navigates to the new draft

**Checkpoint**: US6 complete.

---

## Phase 9: User Story 7 — Cross-campaign comparison (Priority: P3)

**Goal**: Compare 2-4 campaigns side-by-side on a dedicated page.

**Independent Test**: Multi-select 3 campaigns → click Compare → see 3 columns + overlaid line chart.

### Backend tests for User Story 7

- [ ] T079 [P] [US7] Write `NUMU-api/tests/unit/test_campaign_compare_service.py` — KPI rollup, time-series bucketing (day vs week granularity), graceful handling of unknown ids (returns `found: false` row)
- [ ] T080 [P] [US7] Write `NUMU-api/tests/integration/test_campaign_compare_e2e.py` — fixture 3 campaigns, hit endpoint, assert payload shape per `contracts/campaign-actions.md`

### Backend implementation for User Story 7

- [ ] T081 [US7] Add `compare(store_id, campaign_ids, date_from, date_to, attribution_model, granularity)` method to `NUMU-api/src/infrastructure/repositories/analytics_repository.py` — one query fetches KPIs for all campaigns, second query fetches the series. Returns the response shape from the contract.
- [ ] T082 [US7] Add GET endpoint `/marketing/campaigns/compare` to `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` per `contracts/campaign-actions.md` — validates ids count 2-4, handles missing ids gracefully with warnings field
- [ ] T083 [US7] Add granularity helper — `pick_granularity(date_from, date_to) -> 'day' | 'week'` (day if < 60 days else week, overridable via query)

### Frontend implementation for User Story 7

- [ ] T084 [P] [US7] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `compareCampaigns`
- [ ] T085 [P] [US7] Write `numo-merchant-hub/src/pages/MarketingCampaignsCompare.test.tsx` — renders N columns for N ids; banner for missing ids
- [ ] T086 [US7] Build `numo-merchant-hub/src/pages/MarketingCampaignsCompare.tsx` — N columns each with 4 KPI cards + 1 overlaid Recharts `LineChart`; date-range picker at top; banner when warnings present
- [ ] T087 [US7] Extend `numo-merchant-hub/src/pages/MarketingCampaigns.tsx` — add multi-select checkboxes on each row, "Compare selected" CTA (disabled unless 2-4 selected) navigating to `/campaigns/compare?ids=a,b,c`

**Checkpoint**: US7 complete.

---

## Phase 10: User Story 8 — AI optimization tips (Priority: P3)

**Goal**: 0-3 heuristic recommendation cards on the campaign detail right sidebar.

**Independent Test**: Open a campaign with multi-channel data — see a "boost channel" tip. Dismiss it, navigate away, return → tip stays dismissed in same session.

### Backend tests for User Story 8

- [ ] T088 [P] [US8] Write `NUMU-api/tests/unit/test_campaign_tips.py` — matrix: each of the 4 trigger types fires under the right conditions and stays silent otherwise; severity ordering; combined firings

### Backend implementation for User Story 8

- [ ] T089 [US8] Create `NUMU-api/src/application/services/campaign_tips.py` — pure function `compute_tips(channel_breakdown, coupon_stats, device_breakdown, top_products, total_revenue) -> list[Tip]`. Implements 4 heuristics from the contract.
- [ ] T090 [US8] Add GET endpoint `/{campaign_id}/tips` to `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` per `contracts/campaign-actions.md` — orchestrates the breakdown queries + passes through to `compute_tips`

### Frontend implementation for User Story 8

- [ ] T091 [P] [US8] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `getCampaignTips`
- [ ] T092 [P] [US8] Write `numo-merchant-hub/src/components/campaigns/CampaignTipsPanel.test.tsx` — renders tips, dismiss persists in sessionStorage, empty state
- [ ] T093 [US8] Create `numo-merchant-hub/src/components/campaigns/CampaignTipsPanel.tsx` — collapsible card list, sessionStorage-backed dismissals keyed by `(campaign_id, tip_id)`
- [ ] T094 [US8] Mount `<CampaignTipsPanel />` inside the existing accordion slot in `CampaignSidebar.tsx`

**Checkpoint**: US8 complete.

---

## Phase 11: User Story 9 — Best-time-to-send picker (Priority: P3)

**Goal**: Chip row in the Schedule dialog showing top 3 historical send times.

**Independent Test**: Open Schedule dialog on a draft email campaign — see 3 chips. Click one → picker jumps to next occurrence. SMS draft → fallback or hidden.

### Backend tests for User Story 9

- [ ] T095 [P] [US9] Write `NUMU-api/tests/unit/test_send_time_suggester.py` — fixture matrix: 30 mock sends with varied open rates, assert top 3 chips correct; insufficient data → empty + helper; SMS-only history → fallback `based_on=send_count`

### Backend implementation for User Story 9

- [ ] T096 [US9] Create `NUMU-api/src/application/services/send_time_suggester.py` — `suggest(store_id, channel, tz) -> SuggestionResult` queries last 90d sends + Resend open events, groups by weekday × hour, computes avg_open_rate or fallback rank
- [ ] T097 [US9] Add GET endpoint `/stores/{store_id}/marketing/send-time-suggestions` per `contracts/send-time-suggestions.md` in a new route file `NUMU-api/src/api/v1/routes/stores/marketing_send_times.py`. In-memory `cachetools.TTLCache` keyed on `(store_id, channel)`, 1-hour TTL.
- [ ] T098 [US9] Register the new router in API package init

### Frontend implementation for User Story 9

- [ ] T099 [P] [US9] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `getSendTimeSuggestions`
- [ ] T100 [P] [US9] Write tests for ScheduleDialog chip behavior in `numo-merchant-hub/src/components/campaigns/ScheduleDialog.test.tsx`
- [ ] T101 [US9] Extend the existing Schedule dialog (currently embedded in `MarketingCampaignDetail.tsx`) — extract into `numo-merchant-hub/src/components/campaigns/ScheduleDialog.tsx`, add a chip row above the datetime picker that fetches on dialog open. Implement the `nextOccurrence(weekday, hour, tz)` helper. Helper text for fallback / insufficient-data modes per spec.

**Checkpoint**: US9 complete. All 9 user stories shipped.

---

## Phase 12: Polish & Cross-Cutting Concerns

- [ ] T102 [P] RTL visual regression pass — manually verify Marketing nav, Campaign detail, Attribution page, Compare page in Arabic locale (no overlapping text, mirrored chevrons, correct chart-legend ordering)
- [ ] T103 [P] Performance smoke — run `ab` or `wrk` against the 5 new breakdown endpoints with synthetic data; verify p95 ≤ 800ms over a 30-day window (SC-003)
- [ ] T104 [P] Performance smoke — compare endpoint with 4 campaigns over 30 days, verify p95 ≤ 1500ms (SC-012)
- [ ] T105 [P] Performance smoke — send-time chip render via `time curl ...`, verify ≤ 200ms (SC-010) on a 90-day cache hit
- [ ] T106 Run `quickstart.md` end-to-end on test env — all 54 steps pass
- [ ] T107 [P] Memory-write: drop a project note about the new aggregation patterns (5 breakdowns + tenant filter shape) into `C:\Users\Yahia\.claude\projects\C--Users-Yahia-NUMU\memory\` if non-obvious
- [ ] T108 Bandit + Ruff clean on backend; ESLint + tsc --noEmit clean on frontend (pre-commit hooks already enforce this — confirm green)
- [ ] T109 `/speckit-security-review-branch` on the merged feature branch — investigate any high/critical findings before final merge

---

## Dependencies & Execution Order

### Phase dependencies

- **Phase 1 (Setup)**: independent — can start immediately
- **Phase 2 (Foundational)**: depends on Phase 1; T007 (migrations) BLOCKS everything below
- **Phase 3+ (User Stories)**: all depend on Phase 2 complete

### Inter-story dependencies

| Story | Depends on | Why |
| ----- | ---------- | --- |
| US1 (nav) | Phase 2 | Routes must exist |
| US2 (Attribution page) | Phase 2 | Route + page stub |
| US3 (Detail rebuild) | Phase 2 + ua-parser installed (T001) | Device classification needs the lib |
| US4 (Auto-match) | Phase 2 (T004 migration) + US3's CampaignSidebar (T036) | Panel mounts into sidebar |
| US5 (Activities) | Phase 2 (T005 migration) + US3's CampaignSidebar (T036) | Same as US4 |
| US6 (Duplicate) | Phase 2 | Independent |
| US7 (Compare) | Phase 2 + US3's CampaignChartGrid components for visual consistency (optional reuse) | List page extension |
| US8 (Tips) | Phase 2 + US3's CampaignSidebar (T036) | Panel mounts into sidebar |
| US9 (Best-time) | Phase 2 + US6's extracted ScheduleDialog (T101 extracts it) | Dialog component to extend |

### Within a story

- Tests written first (per constitution Principle III). Tests SHOULD fail before implementation lands. Run `pytest -k <task_id>` after writing the test to confirm red.
- Models → Services → Endpoints → Frontend wiring (where applicable)

### Parallel opportunities

- **Phase 1**: T002 + T003 in parallel
- **Phase 2 models**: T008-T013 in parallel after T007 commits
- **All P1 stories** (US1, US2, US3): can run in parallel after Phase 2 completes
- **All P2 stories** (US4, US5): can run in parallel after their migrations land in Phase 2
- **All P3 stories** (US6, US7, US8, US9): fully independent — full parallelism
- **Within US3**: T024-T028 in parallel (different repository methods, same file but distinct functions); T034-T045 in parallel (different files)
- **Tests within a story**: all `[P]`-marked test tasks can run in parallel

---

## Parallel Example — Phase 5 (US3)

After T032 (device classifier tests) + T031 (5 endpoints) merge, the 8 chart panel components can be built in parallel by separate developers:

```bash
# Frontend developer A
Task: T038 SessionsByChannelPanel
Task: T039 SalesByChannelPanel
Task: T040 SessionsByUtmPanel
Task: T041 SalesByUtmPanel

# Frontend developer B
Task: T042 OrdersNewVsReturningPanel
Task: T043 SalesByOrderSizePanel
Task: T044 ItemsSoldByProductPanel
Task: T045 SessionsByDevicePanel

# Frontend developer C — meanwhile assembles the shell
Task: T035 CampaignDetailHeader
Task: T036 CampaignSidebar
Task: T037 CampaignKpiCards
```

---

## Implementation Strategy

### MVP First (P1 only — US1 + US2 + US3)

1. Phase 1 setup (T001-T003)
2. Phase 2 foundational (T004-T014) — INCLUDING the 3 migrations
3. Phase 3 (US1 nav restructure) — small, ship quickly for instant discoverability win
4. Phase 4 (US2 Attribution page) — straightforward extraction
5. Phase 5 (US3 Detail rebuild) — the substantial one, parallelize across developers
6. **STOP and validate**: quickstart.md Phases 1-2. Demo to merchant.
7. Cut a release. P1 is the MVP — the Shopify-style layout is the headline win.

### Incremental Delivery (add P2)

8. Phase 6 (US4 auto-match) + Phase 7 (US5 activities) in parallel
9. **Validate**: quickstart Phases 3-4
10. Cut another release.

### Add P3 polish (US6-US9)

11. Phase 8 (US6 duplicate), Phase 9 (US7 compare), Phase 10 (US8 tips), Phase 11 (US9 best-time) — independently
12. **Validate**: quickstart Phases 5-8
13. Final phase 12 polish + run quickstart end-to-end
14. `/speckit-security-review-branch` → if clean, merge to dev

### Parallel team strategy (3+ developers)

- Dev A: Phase 2 migrations + ua-parser wiring (T001-T013)
- Dev B: Phase 3 nav (T015-T016) — short story, can pick up another after
- Dev C: Phase 4 Attribution (T017-T021) — short story, can pick up another after
- Devs A+B+C converge on Phase 5 (US3) — the work split is in the parallel example above
- After P1 merges, devs split P2 + P3 stories one-each

---

## Notes

- **All paths are repo-relative** to either `NUMU-api/` or `numo-merchant-hub/` — never absolute
- **Tests-first**: Constitution Principle III is NON-NEGOTIABLE. Every story's test tasks come BEFORE the implementation tasks within that phase
- **Migrations are sequential**: T004 → T005 → T006 → T007 must run in order on the same Alembic head
- **Avoid scope creep**: WhatsApp campaign improvements + email template editor improvements are explicit non-goals (per spec); reject any PR comment asking to bundle them in
- **Performance budgets are firm**: SC-003 / SC-010 / SC-012 must be measured before final merge (T103-T105)
- **Commit cadence**: commit after each task or logical group. Branches: one feature branch per story is fine for the parallel team strategy; squash-merge to feature `002-marketing-campaigns-v2` integration branch, then PR to `dev`.
- **Total task count: 109**
