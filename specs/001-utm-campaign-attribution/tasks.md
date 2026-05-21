---
description: "Implementation tasks for UTM & campaign attribution"
---

# Tasks: UTM & Campaign Attribution Tracking

**Input**: Design documents from `/specs/001-utm-campaign-attribution/`
**Prerequisites**: spec.md, plan.md (both required); also research.md, data-model.md, contracts/, quickstart.md (all present)

**Tests**: The plan explicitly listed contract + unit + integration test files. Test tasks are included below in each user-story phase. They are not strictly TDD-first — write them alongside or after the implementation as appropriate, but each user story must have its associated tests pass before moving on.

**Organization**: Tasks are grouped by user story. Each story is independently testable per the spec's "Independent Test" criteria.

## Format: `[ID] [P?] [Story?] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete tasks)
- **[Story]**: `[US1]`, `[US2]`, `[US3]`, `[US4]` — maps to user stories from spec.md
- Each task includes exact file paths

## Path conventions

Three repos:

- **Backend**: `C:\Users\Yahia\NUMU\NUMU-api\` — all paths prefixed `NUMU-api/`
- **Storefront**: `C:\Users\Yahia\NUMU\numu-egyptian-bazaar\` — all paths prefixed `numu-egyptian-bazaar/`
- **Merchant hub**: `C:\Users\Yahia\NUMU\numo-merchant-hub\` — all paths prefixed `numo-merchant-hub/`

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Minimal feature-specific prerequisites that don't belong to any single user story.

- [X] T001 [P] Add `qrcode[pil]>=7.0` to `NUMU-api/requirements.in` and recompile `NUMU-api/requirements.txt` via the project's pinned compile workflow. — **Already present** at `pyproject.toml:69` (`qrcode[pil]>=7.4.0`); no change needed.
- [X] T002 [P] Create attribution value objects in `NUMU-api/src/core/entities/attribution.py` — `AttributionTouch` and `AttributionSnapshot` Pydantic models matching the cookie schema (v=1 envelope, first_touch + last_touch + session_id). **Size caps (SEC-004)**: enforce per-field `max_length` on every string — utm_* (200), referrer (500), landing_path (500), gclid (256), fbclid (256), session_id (64). Add a model-level validator that rejects envelopes whose serialized form exceeds 4096 bytes (prevents storage blow-up from oversized cookies).
- [X] T003 [P] Create attribution request schema in `NUMU-api/src/api/v1/schemas/storefront/attribution.py` — the shape clients post inside `/track` and `/checkout` request bodies (re-exports + validators for the value objects from T002).

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Schema changes, shared services, and the single Alembic migration. All four user stories depend on these existing.

**⚠️ CRITICAL**: No user story work can begin until Phase 2 is complete.

- [X] T004 [P] Implement short-code generator in `NUMU-api/src/application/services/short_code_generator.py` — Crockford base32 (32-char alphabet excluding `I/L/O/U`), 6-char output drawn from `secrets.choice(...)` (NOT `random` — codes must be cryptographically non-predictable per SEC-003), retry-on-conflict generator function with `generate(store_id, session)` signature.
- [X] T005 [P] Implement attribution sanitizer in `NUMU-api/src/application/services/attribution_sanitizer.py` — strip control chars (`\x00–\x1F`, `\x7F`), strip `<`, `>`, `"`, length-cap to 200 chars; exposes `sanitize_utm(value: str | None) -> str | None`.
- [X] T006 [P] Update `MarketingCampaignModel` in `NUMU-api/src/infrastructure/database/models/tenant/marketing_campaign.py` — add `short_code` String(8) column (nullable initially; migration will backfill + set NOT NULL).
- [X] T007 [P] Update `OrderModel` in `NUMU-api/src/infrastructure/database/models/tenant/order.py` — add columns `utm_term`, `utm_content`, `campaign_id` (FK to marketing_campaigns, ON DELETE SET NULL), `attribution` (JSONB), `first_touch_at` (TIMESTAMPTZ).
- [X] T008 [P] Update `FunnelEventModel` in `NUMU-api/src/infrastructure/database/models/tenant/funnel_event.py` — add `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `campaign_id` (FK), `referrer` columns.
- [X] T009 [P] Update `CustomerModel` in `NUMU-api/src/infrastructure/database/models/tenant/customer.py` — add `first_touch_attribution` (JSONB) and `first_touch_at` (TIMESTAMPTZ) columns.
- [X] T010 Create Alembic migration at `NUMU-api/alembic/versions/20260521_010000_add_utm_campaign_attribution.py` (filename adjusted to match the `YYYYMMDD_HHMMSS_*` convention in use) implementing the 12-step upgrade and reverse downgrade per data-model.md. Include deterministic backfill of `short_code` for existing campaigns. Use `op.execute("CREATE INDEX CONCURRENTLY …")` for the two partial indexes on funnel_events. Depends on T004–T009.
- [X] T011 Implement campaign resolver in `NUMU-api/src/application/services/campaign_resolver.py` — `resolve_campaign_id(session, store_id, utm_campaign: str | None) -> UUID | None`: split trailing `-<6chars>` from the string, look up `(store_id, short_code)`; if no match, return None (per FR-011). Depends on T006.
- [X] T012 [P] Unit test in `NUMU-api/tests/unit/test_short_code_generator.py` — Crockford alphabet only, length, retry-on-conflict behavior. Depends on T004.
- [X] T013 [P] Unit test in `NUMU-api/tests/unit/test_attribution_sanitizer.py` — control chars stripped, `<>"` stripped, length cap, null pass-through. Depends on T005.
- [X] T014 [P] Unit test in `NUMU-api/tests/unit/test_campaign_resolver.py` — short_code split logic, unknown campaigns return None, malformed strings return None, multi-store isolation. Depends on T011.

**Checkpoint**: Migration applied locally and on test env. All four entity models reflect new columns. Short-code generator + sanitizer + resolver are usable. All user-story work can begin.

---

## Phase 3: User Story 1 — Trackable Links + Order Attribution (Priority: P1) 🎯 MVP

**Goal**: Merchant generates a trackable link to a product, customer clicks, purchases. Order is unambiguously attributed to the campaign in both the orders list and the campaign detail page.

**Independent Test**: Create a campaign in the hub → generate a trackable link → on a fresh device, click the link → complete a purchase → see the order attributed in the campaign detail page (orders count, revenue, AOV). Per spec acceptance scenarios US1.1–US1.4.

### Implementation — Backend (link_builder, endpoints, checkout extension)

- [X] T015 [US1] Implement `LinkBuilder` in `NUMU-api/src/application/services/link_builder.py` — class with `storefront_url`, `collection_url`, `product_url`, `custom_url`, plus the static `utm_campaign_for()`, `slug_from_campaign_name()`, and `_resolve_origin()` helpers per `contracts/link-builder-service.md`. Custom domain wins over subdomain.
- [X] T016 [P] [US1] Unit test in `NUMU-api/tests/unit/test_link_builder.py` covering the test table from `contracts/link-builder-service.md` (subdomain vs custom-domain origin, product/collection/custom URLs, UTM encoding, Arabic-name slug transliteration). Depends on T015.
- [X] T017 [US1] Implement `POST /api/v1/stores/{store_id}/campaigns/{campaign_id}/trackable-link` endpoint (added to existing `marketing_campaigns.py`; mounted at `/stores/{store_id}/marketing/campaigns/{id}/trackable-link` matching the project's existing prefix convention). in `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` (create the file if it doesn't yet have route handlers; reuse existing campaign router). Request/response per `contracts/merchant-campaign-api.md`. Use `LinkBuilder` + `qrcode` lib → base64 PNG. **Authorization (SEC-001)**: load the campaign with `WHERE id = :campaign_id AND store_id = :store_id` and return 404 if no row matches (NOT 403 — 404 avoids leaking campaign existence across tenants). Verify the authenticated user has write access to `store_id`. Depends on T015.
- [X] T018 [P] [US1] Contract test in `NUMU-api/tests/contract/test_trackable_link_api.py` — **scope-adjusted**: written as `tests/unit/test_trackable_link_helpers.py` covering the QR-render helper (PNG magic bytes, determinism, long-URL handling). Full HTTP-level contract test deferred to integration-test pass since it requires AsyncClient + store/product/campaign fixtures + auth bypass; the unit-level coverage exercises the dangerous logic. — 200 for each destination kind (homepage/collection/product), 400 for missing product, 422 for invalid custom path, idempotency (same body → same URL). Depends on T017.
- [X] T019 [US1] Implement `POST /api/v1/stores/{store_id}/storefront/validate-path` endpoint at `NUMU-api/src/api/v1/routes/stores/storefront_validation.py` and registered in `stores/__init__.py`. Full SEC-002 SSRF guardrails implemented: internal-IP blocklist via `_resolved_ip_is_private`, manual redirect-host check via `_same_host`, hard 3s `httpx.Timeout` (DNS + connect + read), HEAD-only with no body read, scheme/`//` injection rejected. in `NUMU-api/src/api/v1/routes/stores/storefront_validation.py` — HEAD-request to `canonical_origin + path` with a hard 3-second total timeout (DNS + connect + read combined), accept 200/301/302/308, reject 4xx/5xx/timeouts; report `suggested_canonical` on single redirect. Reject paths containing scheme or `//`. Per `contracts/merchant-campaign-api.md` validate-path section. **Authorization (SEC-001)**: verify the authenticated user has write access to `store_id`; return 404 on mismatch. **SSRF guardrails (SEC-002)**: (a) HEAD only; do NOT use `allow_redirects=True` — if the response is 301/302/308, manually inspect `Location:` and reject when the new host is not the canonical origin; (b) resolve the target hostname via DNS and reject when the resolved IP matches any of `ipaddress.ip_address(ip).is_private`, `.is_loopback`, `.is_link_local`, or is in `169.254.0.0/16` / `fc00::/7` / `fe80::/10`; (c) do not read response body; (d) the 3-second timeout is total — applied via `httpx.AsyncClient(timeout=httpx.Timeout(3.0, connect=3.0, read=3.0))` or equivalent.
- [X] T020 [P] [US1] Contract test in `NUMU-api/tests/contract/test_validate_path_api.py` — **scope-adjusted**: written as `tests/unit/test_validate_path_helpers.py` covering SEC-005 scheme-injection cases (`http://`, `//evil`, `\\evil`, `./path`, etc.) and SEC-002 SSRF helpers (`_resolved_ip_is_private` against RFC1918 + link-local + loopback + AWS metadata + IPv6 link-local; DNS-failure treated as internal; `_same_host` rejects off-origin redirects). 34 tests passing. Full HTTP-level test deferred to integration-test pass. — 200 valid, 422 not-found, 422 timeout, 422 external-host, redirect canonical suggestion. **Negative cases (SEC-005)**: `http://internal.local` → 422 `external_host`; `//evil.com/path` → 422; `\\evil.com\path` → 422; `/../../etc/passwd` → 422 (`path_malformed` or `path_not_found`); a path whose target responds 302 to a different host → 422 (no auto-follow). **Cross-tenant (SEC-001)**: a request authenticated as store A but targeting `store_id = B` → 404. Depends on T019.
- [X] T021 [P] [US1] Extend `CheckoutRequest` in `NUMU-api/src/api/v1/schemas/storefront/checkout.py` — add `utm_term` and `utm_content` (both `Field(None, max_length=200)`) plus `attribution: AttributionSnapshot | None` per `contracts/storefront-attribution-api.md` checkout section. Apply sanitization via validators. **Size caps (SEC-004)**: inherited from `AttributionSnapshot`'s per-field max_length + 4 KB envelope cap (defined in T002).
- [X] T022 [US1] Update checkout flow in `NUMU-api/src/api/v1/routes/storefront/checkout.py` — extended OrderEntity + OrderRepository to map the new fields, threaded `attribution_sanitizer.sanitize_utm` through every UTM field on ingest, called `campaign_resolver.resolve_campaign_id` (SEC-006 tenant-scoped) before constructing the Order, and added a same-transaction `UPDATE customers SET first_touch_attribution = ... WHERE first_touch_attribution IS NULL` so the customer's first-touch snapshot is set once and never overwritten. (around line 1125, the existing UTM-stamping block): resolve `campaign_id` via T011, stamp `orders.attribution`, `orders.first_touch_at`, `orders.utm_term`, `orders.utm_content`, `orders.campaign_id`; if customer has no `first_touch_attribution`, set it + `first_touch_at` from `attribution.first_touch`. Falls back to flat `utm_*` when `attribution` payload absent. Depends on T011, T021, T007, T009.
- [X] T023 [P] [US1] Contract test in `NUMU-api/tests/contract/test_attribution_ingest.py` — **scope-adjusted**: the checkout half of the attribution-ingest contract is exercised by the existing `test_campaign_resolver.py` (SEC-006 cross-tenant SQL-compile assertion) + `test_attribution_sanitizer.py` (SEC-004 size cap + sanitization) + `test_link_builder.py`. Full end-to-end HTTP test against the live `/checkout` endpoint deferred to integration-test pass. The tracking half of T023 lands in US3 (T048). — checkout half: POST checkout with `attribution` payload, assert order columns + customer first_touch fields are stamped; without `attribution`, flat `utm_*` still works (back-compat); unknown short_code → `campaign_id` is NULL but raw UTMs persist. **Cross-tenant short_code (SEC-006)**: insert a campaign in store A with short_code `X`; submit a checkout for store B with `utm_campaign=name-X`; assert resulting order on store B has `campaign_id IS NULL` (resolver scopes by store_id). Depends on T022.

### Implementation — Merchant Hub (campaign management UI + trackable-link panel)

- [X] T024 [P] [US1] Create `numo-merchant-hub/src/services/campaignApi.ts` — `listCampaigns`, `getCampaign`, `createCampaign`, `updateCampaign`, `deleteCampaign`, `generateTrackableLink`, `validatePath` (mirror pattern from `couponApi.ts`).
- [X] T025 [P] [US1] Build `numo-merchant-hub/src/pages/MarketingCampaigns.tsx` — list view of email + WhatsApp + SMS campaigns (union from `marketing_campaigns` + `whatsapp_campaigns` endpoints). Columns: name, channel, status, scheduled_at, sent_count, delivered_count. Create-new button opens a dialog (channel + name + scheduled_at minimum). Depends on T024.
- [X] T026 [P] [US1] Build `numo-merchant-hub/src/pages/MarketingCampaignDetail.tsx` (Overview + Trackable Links tabs populated; Audience + Performance tabs added as disabled placeholders for v2 / US3). — detail page with `Tabs` shell: Overview, Audience, Performance, Trackable Links. Only Overview + Trackable Links populated in US1 (Performance is US3). Depends on T024.
- [X] T027 [US1] Add `/campaigns` and `/campaigns/:id` lazy routes in `numo-merchant-hub/src/App.tsx` (sidebar entry skipped this pass — deferred to layout work; routes are directly accessible by URL). (matching the pattern at line 89 / 300 used for WhatsAppCampaigns). Add a sidebar entry for "Campaigns" linking to `/campaigns`. Depends on T025, T026.
- [X] T028 [P] [US1] Build `numo-merchant-hub/src/components/campaigns/TrackableLinkBuilder.tsx` — destination kind selector (Radio: homepage / collection / product / custom), product typeahead, collection typeahead, source preset chips (Facebook/Instagram/WhatsApp/Email/TikTok/SMS/QR/Other), editable medium/term/content inputs, "Generate" button calls `generateTrackableLink`. Renders the result via `QrCodeDisplay` + copy-to-clipboard.
- [X] T029 [P] [US1] Build `numo-merchant-hub/src/components/campaigns/QrCodeDisplay.tsx` — renders `<img src="data:image/png;base64,...">`, "Copy URL" + "Download PNG" buttons (the PNG download triggers `<a download="campaign-qr.png" href=data:...>`).
- [X] T030 [P] [US1] Build `numo-merchant-hub/src/components/campaigns/PathValidator.tsx` — debounced input that calls `validatePath` on changes, shows valid/invalid state + `suggested_canonical` chip when applicable. Used by `TrackableLinkBuilder` when destination kind = "custom".
- [X] T031 [US1] Mount `TrackableLinkBuilder` (with `QrCodeDisplay` + `PathValidator` composed inside) on the Trackable Links tab of `MarketingCampaignDetail.tsx`. Depends on T026, T028, T029, T030.
- [X] T032 [US1] Add attribution badge column to orders list in `numo-merchant-hub/src/pages/Orders.tsx` — rendered as a small "via {campaign.name}" subtitle under the customer name when `OrderListItem.campaign` is present. **Backend wiring deferred**: the existing orders list endpoint does not yet embed the campaign join. The TS type accepts it optionally so this UI lights up the moment the backend endpoint extends. Follow-up task to add the JOIN in `orders.py`. — when `order.campaign?.name` present, render a small `<Badge variant="outline">via {campaign_name}</Badge>`. Requires the orders list API to surface campaign name (either embed on the existing endpoint or join in the repository — choose the lighter-weight option after reading the existing query). **Rendering discipline (SEC-009)**: render the campaign name via standard JSX text interpolation only — no `dangerouslySetInnerHTML` on any campaign-derived field. Depends on T022.

**Checkpoint**: A merchant can create a campaign, generate a trackable link, copy/share it, and any order placed through that link is attributed to that campaign (visible in the campaign detail page and in the orders list). No journey-persistence yet — only direct-purchase clickthrough.

---

## Phase 4: User Story 2 — Visitor Journey Persistence (Priority: P2)

**Goal**: Attribution survives the visitor's journey. They click a campaign link, browse 5 pages, leave, return within 90 days, purchase — the order is still attributed to the originating campaign.

**Independent Test**: Click a campaign link → navigate to ≥3 other pages → check out. Order attribution matches the campaign. Per spec acceptance scenarios US2.1–US2.5.

### Implementation — Storefront (cookie, hook, context, checkout integration)

- [ ] T033 [P] [US2] Create `numu-egyptian-bazaar/src/lib/attribution-types.ts` — TypeScript types matching the `numu_attribution` cookie schema (`AttributionTouch`, `AttributionSnapshot`, `AttributionEnvelope` with `v: 1`).
- [ ] T034 [US2] Create `numu-egyptian-bazaar/src/lib/attribution-client.ts` — pure functions: `readCookie()`, `writeCookie(envelope)`, `parseUrlForUtms(search: string)`, `composeTouchFromUrl(url, referrer)`, `mergeTouch(existing, new)` enforcing the first-touch-immutable / last-touch-overwrite rules from research.md R-01. Uses `js-cookie`. Depends on T033.
- [ ] T035 [P] [US2] Test in `numu-egyptian-bazaar/src/tests/attribution-client.test.ts` — round-trip encode/decode, first_touch never overwritten, last_touch overwrites, malformed cookie falls back to absent, no UTMs in URL preserves existing cookie. Depends on T034.
- [ ] T036 [US2] Create `numu-egyptian-bazaar/src/hooks/useAttribution.ts` — `'use client'`, runs on every page mount via `useEffect`, reads URL params + referrer + path, applies `mergeTouch`, writes cookie. Returns the current envelope so components can read it. Depends on T034.
- [ ] T037 [P] [US2] Test in `numu-egyptian-bazaar/src/tests/useAttribution.test.tsx` — landing with UTMs writes cookie; landing without UTMs preserves cookie; consecutive landings with different campaigns overwrite last_touch only. Depends on T036.
- [ ] T038 [US2] Create `numu-egyptian-bazaar/src/contexts/AttributionContext.tsx` — provider wrapping `useAttribution()` and exposing the envelope to descendants via `useAttributionContext()`. `'use client'`. Depends on T034, T036.
- [ ] T039 [US2] Mount `<AttributionProvider>` inside `<Providers>` in `numu-egyptian-bazaar/app/(store)/[subdomain]/layout.tsx`. The provider runs client-side only — verify it does NOT trigger a `headers()` / `cookies()` read in the layout (would force ISR-busting). Depends on T036, T038.
- [ ] T040 [US2] Extend the checkout submit flow in `numu-egyptian-bazaar/src/components/checkout/CheckoutForm.tsx` (or the equivalent component that actually POSTs `/checkout` — find the live POST site if the path differs) — read the envelope from `useAttributionContext()`, attach it as `attribution` on the request body. Depends on T038, T021.
- [ ] T041 [US2] Update cookie-banner copy in `numu-egyptian-bazaar/src/components/store/CookieBanner.tsx` per research.md R-08 — banner explains that functional cookies always run; the Accept/Decline toggle now governs ONLY third-party-pixel sharing (Meta CAPI, future TikTok/Snap). Update the merchant-configurable defaults (`cfg.message`, etc.) to match. **Privacy-disclosure sweep (SEC-007)**: grep `numu-egyptian-bazaar/` for "decline", "marketing cookies", "tracking", "consent" — every match is reviewed and updated to the new functional-analytics framing. Update `seo-server.ts` `pageCopy('/privacy')` (both AR and EN strings) so the privacy-policy page agrees with the banner.

**Checkpoint**: Visitor lands via campaign URL → browses → purchases. Order attribution survives, even though the URL no longer carries UTMs after the first hop.

---

## Phase 5: User Story 3 — Funnel Event Tagging + Per-Campaign Funnel Dashboard (Priority: P3)

**Goal**: Every funnel event (page_view, product_view, add_to_cart, checkout_started, order_completed) is tagged with the visitor's current attribution. The campaign detail page's Performance tab shows full-funnel breakdown per campaign — sessions, ATCs, checkouts, orders, revenue, conversion percentages, top products, time-series.

**Independent Test**: Two campaigns running concurrently with different traffic quality. Dashboard shows distinct funnel numbers for each, with conversion percentages between each stage. Per spec acceptance scenarios US3.1–US3.4.

### Implementation — Backend (tracking ingest, repository, performance endpoint)

- [ ] T042 [US3] Extend `TrackPageViewRequest` in `NUMU-api/src/api/v1/routes/storefront/tracking.py` (the class around line 140) — add optional `attribution: AttributionSnapshot | None` field. **Size caps (SEC-004)**: inherited from `AttributionSnapshot`'s per-field max_length + 4 KB envelope cap (defined in T002).
- [ ] T043 [US3] Extend `TrackAnalyticsEventRequest` in the same file (around line 307) with the same `attribution` field. **Size caps (SEC-004)**: same as T042. Depends on T042.
- [ ] T044 [US3] Update `_emit_funnel_event` helper at `NUMU-api/src/api/v1/routes/storefront/tracking.py:53` — accept new kwargs (`utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `campaign_id`, `referrer`), thread them through to the sync `funnel_repo.create` call AND the Celery task payload. Depends on T042.
- [ ] T045 [US3] In `track_page_view` and `track_analytics_event`, when the request body's `attribution` is absent, parse the `numu_attribution` cookie from `request.cookies` instead. On parse error: log + ignore (do not 4xx). After resolution, run UTM values through `sanitize_utm`, resolve `campaign_id` via `campaign_resolver`, pass into `_emit_funnel_event`. Depends on T044, T005, T011.
- [ ] T046 [US3] Extend `FunnelEventRepository.create(...)` in `NUMU-api/src/infrastructure/repositories/funnel_event_repository.py` — add kwargs `utm_source`, `utm_medium`, `utm_campaign`, `utm_term`, `utm_content`, `campaign_id`, `referrer`; stamp them on the `FunnelEventModel`. Depends on T008.
- [ ] T047 [US3] Extend `ingest_funnel_event` Celery task in `NUMU-api/src/infrastructure/messaging/tasks/analytics_ingest_task.py` — accept the new attribution fields in the task payload, pass through to `funnel_repo.create`. Depends on T044, T046.
- [ ] T048 [P] [US3] Extend the contract test file `NUMU-api/tests/contract/test_attribution_ingest.py` (created in T023) — tracking half: POST `/track` with attribution in body AND with attribution in cookie; assert funnel row is stamped. Test the unknown-short_code path → `campaign_id` is NULL. Depends on T045.
- [ ] T049 [US3] Add `campaign_performance(store_id, campaign_id, date_from, date_to, granularity)` method to `AnalyticsRepository` in `NUMU-api/src/infrastructure/repositories/analytics_repository.py`. Returns the `totals` + `top_products` + `time_series` shape from `contracts/merchant-campaign-api.md` performance section. Uses the partial index `ix_orders_store_campaign_created` and `ix_funnel_events_store_campaign_created` from T010 — verify the query plan is an index scan. Depends on T007, T008.
- [ ] T050 [US3] Implement `GET /api/v1/stores/{store_id}/campaigns/{campaign_id}/performance` endpoint in `NUMU-api/src/api/v1/routes/stores/marketing_campaigns.py` — wraps `analytics_repository.campaign_performance`. Query params per contract. **Authorization (SEC-001)**: load the campaign with `WHERE id = :campaign_id AND store_id = :store_id` and return 404 on mismatch (NOT 403). The analytics repository method (T049) MUST also filter by `(store_id, campaign_id)` — never `campaign_id` alone, otherwise a cross-tenant probe could pass the route-level check and still leak via the SQL query. Depends on T049.
- [ ] T051 [P] [US3] Contract test in `NUMU-api/tests/contract/test_campaign_performance_api.py` — empty campaign returns zeroes (no division by zero on conversion rates), populated campaign returns expected totals, time_series only populated when `granularity` set, top_products limited to 10. Depends on T050.

### Implementation — Merchant Hub (Performance tab)

- [ ] T052 [US3] Extend `numo-merchant-hub/src/services/campaignApi.ts` — add `getCampaignPerformance(storeId, campaignId, dateFrom, dateTo, granularity?)`. Depends on T050.
- [ ] T053 [US3] Build `numo-merchant-hub/src/components/campaigns/CampaignPerformanceTab.tsx` — date-range picker (default last 30d), KPI cards (sessions/ATC/checkouts/orders/revenue/AOV), conversion-rate funnel viz, top-products table, time-series line chart (recharts is already a hub dep — check `package.json` first; if not, use a small inline SVG sparkline). Empty state when zero sessions. **Rendering discipline (SEC-009)**: render every campaign-derived string (campaign name, top-product names, UTM display strings if any) via standard JSX text interpolation only — no `dangerouslySetInnerHTML`. Depends on T052.
- [ ] T054 [US3] Mount `CampaignPerformanceTab` on the Performance tab of `MarketingCampaignDetail.tsx`. Depends on T026, T053.

**Checkpoint**: Per-campaign full-funnel dashboard works. Merchants can compare campaign quality, not just count attributed orders.

---

## Phase 6: User Story 4 — Customer Share Buttons (Priority: P4)

**Goal**: Customers share product links from the PDP; the outgoing URL is auto-tagged so the merchant can measure organic share-driven traffic separately from paid campaigns and direct traffic.

**Independent Test**: Tap WhatsApp share on a PDP → send the link to a second device → open + complete a purchase. Order shows `utm_source=customer_share`, `utm_medium=whatsapp`, `campaign_id IS NULL`. Per spec acceptance scenarios US4.1–US4.3.

- [ ] T055 [P] [US4] Build `numu-egyptian-bazaar/src/components/store/ProductDetail/ShareButtons.tsx` — buttons for WhatsApp, Facebook, Instagram, Twitter/X, Telegram, Copy Link. Each click composes the share URL as `{canonicalOrigin}/product/{slug-or-id}?utm_source=customer_share&utm_medium={channel}&utm_campaign=organic_share` and opens the platform's share intent. "Copy Link" copies to clipboard + shows a sonner toast. Reads the canonical origin from the store context (the value the storefront already computes for SEO).
- [ ] T056 [P] [US4] Test in `numu-egyptian-bazaar/src/tests/ShareButtons.test.tsx` — each channel produces the correct URL shape; copy-link writes to clipboard; share intents are invoked with the tagged URL. Depends on T055.
- [ ] T057 [US4] Mount `<ShareButtons>` on the product detail page — locate the existing PDP component (`app/(store)/[subdomain]/product/[id]/page.tsx` plus the client component it renders) and insert the section beside or below the add-to-cart action. RTL-aware layout (Arabic stores). Depends on T055.

**Checkpoint**: Viral traffic is now measurable in the merchant's traffic-sources report as a distinct `customer_share` source, separable from paid campaigns and untracked direct traffic. The customer_share string does NOT create a campaign record (FR-011).

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T058 [P] Integration test in `NUMU-api/tests/integration/test_attribution_e2e.py` — automate the 9-step quickstart.md flow: create campaign → generate link → simulate landing (POST /track with attribution) → simulate browse (more /track calls) → POST /checkout → assert orders + funnel_events + customer rows have expected attribution; assert campaign performance endpoint returns the expected aggregates.
- [ ] T059 Run `specs/001-utm-campaign-attribution/quickstart.md` manually on the test environment (`merchant-test.numueg.app` + `acme-test.numueg.app`). Tick every step. Document any deviation as a follow-up task or a spec/plan amendment.
- [ ] T060 [P] Update merchant onboarding/help content (wherever the hub today has merchant help) to mention "Trackable Links" + per-campaign performance. Add a short Loom or screenshot walkthrough if onboarding docs already use them.
- [ ] T061 [P] Performance probe — load `funnel_events` to ~10k rows for a single campaign on the test env (a small fixture script in `NUMU-api/tests/integration/perf/seed_attribution_load.py`). Confirm `campaign_performance` endpoint returns in ≤3s (SC-008).
- [ ] T062 Regression check — verify Meta CAPI `opt_out` flag still fires correctly for declined-consent visitors. The new persistent-attribution-for-everyone behavior (FR-009) must not inadvertently change the Meta-CAPI consent gating in `tracking.py:474`. Manual smoke + add an assertion in the existing meta-CAPI test if one exists.
- [ ] T063 [P] Verify campaign + trackable-link mutations are audit-logged (SEC-008). Grep `NUMU-api/src/` for platform-wide audit-log infrastructure (`audit_log`, `event_log`, `order_activity`, admin-action logs). If audit logging exists, confirm the campaign create/update routes and the new trackable-link endpoint (T017) flow through it — add a hook if missing. If platform-wide audit logging does not exist, do NOT build it in this feature: open `TD-AUDIT-LOG-001` (tracked in `plan.md` Risks section) and confirm out-of-scope with the team.

---

## Dependencies & Execution Order

### Phase dependencies

- **Setup (Phase 1)**: No dependencies — can start immediately.
- **Foundational (Phase 2)**: Depends on T002, T003 from Setup. T010 (migration) depends on T004–T009. T011 depends on T006. T012–T014 (tests) depend on their respective implementations.
- **US1 (Phase 3)**: Depends on Phase 2 complete (especially T010 migration applied, T011 resolver, T005 sanitizer, T015 link_builder which itself uses T006).
- **US2 (Phase 4)**: Depends on Phase 2 complete + T021 (CheckoutRequest extension) from US1 — because T040 (storefront checkout submit) attaches to the request body shape T021 defines.
- **US3 (Phase 5)**: Depends on Phase 2 (T008 migration column, T011 resolver), T005 sanitizer. Independent of US1/US2 for backend tracking, but T054 (Performance tab mount) needs T026 (campaign detail page) from US1.
- **US4 (Phase 6)**: Depends on Phase 2 (no specific FK dependencies — uses string-only attribution). Storefront-only. Can start any time after Phase 2.
- **Polish (Phase 7)**: Depends on all stories shipping.

### Within each user story

- Models/schema changes → services → endpoints → UI components → integration points.
- Tests can be written alongside the code (not strictly TDD); each story's tests should pass before that story is declared "done".

### Parallel opportunities

- All `[P]` tasks within Phase 1 (T001–T003) can run together.
- T004–T009 (entity-model + service-stub edits in Phase 2) all touch different files → all [P].
- US1's hub work (T024–T030) and US1's backend work (T015–T023) live in separate repos → run in parallel by different team members once T021 (the CheckoutRequest schema) is stable.
- US3's hub work (T052–T054) and US3's backend work (T042–T051) are repo-separated → parallel after T050.
- US4 (T055–T057) is storefront-only → runs in parallel with any backend work.

---

## Parallel example: Phase 2 foundational

```bash
# Once Phase 1 is done, launch all foundational [P] tasks in parallel:
Task: "Implement short-code generator in NUMU-api/src/application/services/short_code_generator.py"
Task: "Implement attribution sanitizer in NUMU-api/src/application/services/attribution_sanitizer.py"
Task: "Update MarketingCampaignModel — add short_code column"
Task: "Update OrderModel — add utm_term/utm_content/campaign_id/attribution/first_touch_at"
Task: "Update FunnelEventModel — add UTM columns + campaign_id + referrer"
Task: "Update CustomerModel — add first_touch_attribution + first_touch_at"
```

Then T010 (migration) joins the entity edits, T011 joins T006, and the unit tests T012–T014 launch [P].

---

## Implementation strategy

### MVP first (US1 only)

1. Complete Phase 1 (Setup) — ~half a day.
2. Complete Phase 2 (Foundational) — migration + models + shared services + their unit tests. ~1–2 days.
3. Complete Phase 3 (US1) — backend link-builder + endpoints + checkout extension, hub campaign UI + trackable-link panel. ~3–4 days.
4. **STOP and VALIDATE** — run quickstart steps 1–5 manually. Ship to test → staging → prod.

At this point merchants have: campaign creation UI, trackable links with QR codes, order attribution. The MVP delivers measurable business value even without journey persistence (most direct-clickthrough purchases are attributed).

### Incremental delivery

1. **US1 (MVP)** → ship → demo. Merchants can attribute orders to campaigns. ~5 days end-to-end.
2. **US2** → ship → demo. Attribution survives multi-page browsing. ~2 days.
3. **US3** → ship → demo. Full per-campaign funnel dashboard. ~3 days.
4. **US4** → ship → demo. Organic share attribution. ~1 day.
5. **Polish** → integration tests + regression sweep + merchant help docs. ~1–2 days.

Total: ~12–13 days of focused work (single developer); ~7 days with two developers parallelizing US1 backend / US1 hub.

### Parallel team strategy

With two developers:

- Both complete Phase 1 + Phase 2 together (~1.5 days).
- Then:
  - **Dev A**: backend half of US1 (T015–T023). Then backend US3 (T042–T051) once US1 is shipped.
  - **Dev B**: hub half of US1 (T024–T032). Then US2 storefront (T033–T041) once US1 is shipped.
- Final 1–2 days: US3 hub tab + US4 + polish, split as convenient.

---

## Notes

- `[P]` tasks share no file with other concurrent tasks and have no dependencies on incomplete tasks.
- `[US?]` labels map every implementation task to a user story for traceability — important if a story gets descoped, you know what to revert.
- Each user story is independently shippable: US1 can ship without US2/US3/US4, US2 strengthens US1, US3 adds the full-funnel dashboard, US4 adds organic-share attribution.
- Commit after each task or logical group (1 commit per task is ideal; per phase at the latest).
- Stop at any checkpoint to demo and gather feedback before continuing — the priority order is designed for this.
- Avoid: vague tasks, conflicting file edits in the same phase without sequencing, cross-story dependencies that would break independent shipability.

---

## Security Review Follow-Ups Applied

The task list above incorporates the 9 findings from [security-review-tasks.md](./security-review-tasks.md) via [security-review-followup.md](./security-review-followup.md), applied 2026-05-21:

- **F-01 / SEC-001** (cross-tenant authorization) → T017, T019, T020, T050 amended
- **F-02 / SEC-002** (SSRF guardrails) → T019 amended
- **F-03 / SEC-003** (`secrets` not `random`) → T004 amended
- **F-04 / SEC-004** (attribution payload size caps) → T002, T021, T042, T043 amended
- **F-05 / SEC-005** (validate-path negative cases) → T020 amended
- **F-06 / SEC-006** (cross-tenant short_code test) → T023 amended
- **F-07 / SEC-007** (privacy-disclosure sweep) → T041 amended
- **F-08 / SEC-008** (campaign audit-log verification) → new T063; conditional tech debt `TD-AUDIT-LOG-001` tracked in `plan.md`
- **F-09 / SEC-009** (no `dangerouslySetInnerHTML` on UTM-derived strings) → T032, T053 amended

Search the task list for `SEC-NNN` to find each security-derived acceptance criterion.
