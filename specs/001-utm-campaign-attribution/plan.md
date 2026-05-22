# Implementation Plan: UTM & Campaign Attribution

**Branch**: `001-utm-campaign-attribution` | **Date**: 2026-05-21 | **Spec**: [./spec.md](./spec.md)
**Input**: Feature specification from `./spec.md`

## Summary

Close the loop between marketing campaigns (broadcast vehicles today: email/WhatsApp/SMS) and orders/funnel events. After this feature, a merchant can create a campaign, generate trackable links to specific products / collections / arbitrary storefront paths, send them via any channel, and see attributed sessions, add-to-carts, orders, and revenue per campaign in the dashboard. UTMs persist across the visitor journey via a first-party cookie and tag every funnel event, not just the final order.

**Approach**:
1. Extend three tables additively (`marketing_campaigns`, `orders`, `funnel_events`, `customers`) — one Alembic migration, no rewrites.
2. Backend: new service `link_builder.py`, new merchant endpoints (`trackable-link`, `validate-path`, `campaign performance`), extended storefront endpoints (`/track`, `/track-event`, `/checkout`) to accept and stamp attribution.
3. Storefront: `useAttribution()` client hook, `numu_attribution` cookie, share buttons on PDPs, cookie-banner copy update.
4. Merchant hub: build a new `MarketingCampaigns` page (no UI exists today), campaign detail with Trackable-Links tab and Performance tab, attribution badge in orders list.

## Technical Context

**Repos involved** (3):
- `c:\Users\Yahia\NUMU\NUMU-api` — primary, Python/FastAPI backend
- `c:\Users\Yahia\NUMU\numu-egyptian-bazaar` — Next.js storefront
- `c:\Users\Yahia\NUMU\numo-merchant-hub` — React + Vite merchant dashboard

**Backend** (`NUMU-api`):
- **Language/Version**: Python 3.11
- **Primary Dependencies**: FastAPI, SQLAlchemy 2.0 (async), Alembic, Pydantic v2, Celery (for analytics async path)
- **Storage**: PostgreSQL (managed). Schema is multi-tenant via `tenant_id` discriminator in `public` schema.
- **Testing**: pytest, pytest-asyncio
- **New runtime dep**: `qrcode[pil]` (≥7.0) — Python QR generator with PIL rendering
- **Target Platform**: Linux server (Docker compose on a DO droplet — see [envs-layout.md](../../../.claude/projects/C--Users-Yahia-NUMU/memory/envs-layout.md) for staging/test/prod layout)
- **Performance Goals**: Campaign performance endpoint <3s for 10k funnel events (per spec SC-008); storefront tracking endpoint <50ms p95 (no regression from current).
- **Constraints**: Migrations must be online-safe (no table rewrites, no long locks).

**Storefront** (`numu-egyptian-bazaar`):
- **Framework**: Next.js 14+ App Router, React 18+, TypeScript
- **State / context**: React Context (`StoreContext`, `ThemeContext`, `PromoProvider`)
- **Cookies**: `js-cookie` is already a dep; reuse for cookie reads/writes.
- **Constraints**: Tenant layout (`app/(store)/[subdomain]/layout.tsx`) must NOT call `headers()` or `cookies()` — those force every page below it to be dynamic, breaking ISR. The `useAttribution()` hook runs client-side only.

**Merchant Hub** (`numo-merchant-hub`):
- **Framework**: React 18 + Vite + TypeScript
- **UI**: shadcn/ui, lucide-react, sonner toasts
- **i18n**: react-i18next (Arabic + English)
- **API client**: `src/services/api.ts` (`apiClient` wrapper, auto-refreshes 401, responses as `{ data: T }`)
- **Routing**: react-router via `App.tsx`. Lazy-loaded pages.

**Project Type**: Multi-repo web application (3 codebases, single feature).
**Scale/Scope**: O(10⁴) campaigns per store at the high end; O(10⁶) funnel events per active store per month. Index strategy in [data-model.md](./data-model.md) sized for that.

## Constitution Check

No `.specify/memory/constitution.md` is present in this repo. Constitution gate is a no-op for this feature.

If/when a constitution is established, the relevant principles to re-check would be:
- Single Responsibility — the feature touches three repos but each change is locally cohesive (one service + one set of endpoints per repo).
- Testability — every new endpoint has a contract spec; the storefront hook is testable independently.
- Backwards compatibility — all changes are additive (new columns, new endpoints, extended schemas with optional fields).

## Project Structure

### Documentation (this feature)

```text
specs/001-utm-campaign-attribution/
├── spec.md                              # User-facing feature specification (Phase -1)
├── plan.md                              # This file (Phase 1 output)
├── research.md                          # Phase 0 output — design decisions + alternatives
├── data-model.md                        # Phase 1 output — every schema change
├── quickstart.md                        # Phase 1 output — end-to-end verification recipe
├── version-guard-report.md              # Pre-plan check — skipped (no npm in NUMU-api)
├── contracts/
│   ├── merchant-campaign-api.md         # HTTP contracts for trackable-link, validate-path, performance
│   ├── storefront-attribution-api.md    # Cookie shape + extended /track and /checkout schemas
│   └── link-builder-service.md          # Internal Python module contract
└── checklists/
    └── requirements.md                  # Spec quality checklist
```

### Source code — three repos, file-by-file delta

```text
c:\Users\Yahia\NUMU\NUMU-api\
├── alembic/versions/
│   └── utm_attribution_20260521_add_campaign_attribution.py          # NEW migration
├── src/
│   ├── application/services/
│   │   ├── link_builder.py                                           # NEW — storefront URL composer
│   │   ├── campaign_resolver.py                                      # NEW — utm_campaign → MarketingCampaign lookup
│   │   ├── attribution_sanitizer.py                                  # NEW — UTM string sanitizer
│   │   └── short_code_generator.py                                   # NEW — Crockford base32 generator
│   ├── api/v1/
│   │   ├── routes/storefront/
│   │   │   ├── tracking.py                                           # EDIT — extend TrackPageViewRequest + _emit_funnel_event with attribution
│   │   │   └── checkout.py                                           # EDIT — accept attribution payload, resolve campaign_id, stamp customer first_touch
│   │   ├── routes/stores/
│   │   │   ├── marketing_campaigns.py                                # EDIT (or NEW if missing) — add trackable-link, performance, list endpoints
│   │   │   └── storefront_validation.py                              # NEW — validate-path endpoint
│   │   └── schemas/storefront/
│   │       └── checkout.py                                           # EDIT — add utm_term, utm_content, attribution to CheckoutRequest
│   ├── core/entities/
│   │   ├── marketing_campaign.py                                     # EDIT — add short_code to entity
│   │   └── attribution.py                                            # NEW — AttributionTouch + AttributionSnapshot value objects
│   ├── infrastructure/
│   │   ├── database/models/tenant/
│   │   │   ├── marketing_campaign.py                                 # EDIT — add short_code column
│   │   │   ├── order.py                                              # EDIT — add utm_term, utm_content, campaign_id FK, attribution JSONB, first_touch_at
│   │   │   ├── funnel_event.py                                       # EDIT — add UTM columns, campaign_id FK, referrer column
│   │   │   └── customer.py                                           # EDIT — add first_touch_attribution, first_touch_at
│   │   └── repositories/
│   │       ├── analytics_repository.py                               # EDIT — add campaign_performance() method
│   │       └── funnel_event_repository.py                            # EDIT — extend create() with UTM kwargs
│   └── infrastructure/messaging/tasks/
│       └── analytics_ingest_task.py                                  # EDIT — Celery task carries attribution payload
└── tests/
    ├── contract/test_trackable_link_api.py                           # NEW
    ├── contract/test_campaign_performance_api.py                     # NEW
    ├── contract/test_validate_path_api.py                            # NEW
    ├── contract/test_attribution_ingest.py                           # NEW
    ├── unit/test_link_builder.py                                     # NEW
    ├── unit/test_short_code_generator.py                             # NEW
    ├── unit/test_attribution_sanitizer.py                            # NEW
    ├── unit/test_campaign_resolver.py                                # NEW
    └── integration/test_attribution_e2e.py                           # NEW — quickstart flow as an integration test


c:\Users\Yahia\NUMU\numu-egyptian-bazaar\
├── app/(store)/[subdomain]/
│   └── layout.tsx                                                    # EDIT — mount <AttributionProvider> inside Providers tree
└── src/
    ├── lib/
    │   ├── attribution-client.ts                                     # NEW — cookie read/write/encode/decode utilities
    │   └── attribution-types.ts                                      # NEW — TypeScript types matching the contract
    ├── hooks/
    │   └── useAttribution.ts                                         # NEW — page-load hook reading URL + writing cookie
    ├── contexts/
    │   └── AttributionContext.tsx                                    # NEW — exposes current cookie value to checkout submit
    ├── components/
    │   ├── store/
    │   │   ├── CookieBanner.tsx                                      # EDIT — copy update per R-08
    │   │   └── ProductDetail/
    │   │       └── ShareButtons.tsx                                  # NEW (or EDIT existing share UI) — auto-tag share URLs
    │   └── checkout/
    │       └── CheckoutForm.tsx                                      # EDIT — read attribution from context, attach to POST body
    └── tests/
        ├── attribution-client.test.ts                                # NEW
        ├── useAttribution.test.tsx                                   # NEW
        └── ShareButtons.test.tsx                                     # NEW


c:\Users\Yahia\NUMU\numo-merchant-hub\
└── src/
    ├── App.tsx                                                       # EDIT — add /campaigns and /campaigns/:id routes
    ├── pages/
    │   ├── MarketingCampaigns.tsx                                    # NEW — list view (email + WhatsApp + SMS unified)
    │   └── MarketingCampaignDetail.tsx                               # NEW — detail with tabs: Overview, Audience, Performance, Trackable Links
    ├── components/campaigns/
    │   ├── TrackableLinkBuilder.tsx                                  # NEW — destination + source + medium + term + content + QR
    │   ├── CampaignPerformanceTab.tsx                                # NEW — sessions/ATC/orders/revenue + funnel chart
    │   ├── PathValidator.tsx                                         # NEW — wraps validate-path API for the custom-destination input
    │   └── QrCodeDisplay.tsx                                         # NEW — renders + downloads the PNG
    ├── services/
    │   └── campaignApi.ts                                            # NEW — list/create/update/get + trackable-link + performance + validate-path
    └── pages/Orders.tsx                                              # EDIT — small badge per row showing attributed campaign name
```

**Structure Decision**: Three-repo web app. Each repo's changes stay inside that repo (no shared package introduced). The contracts in `contracts/` are the interop boundary — they specify the wire shapes that all three repos must agree on.

## Implementation Order (high level)

The user-story priorities in [spec.md](./spec.md) drive the order. Each priority is a self-contained increment that ships independently.

### P1 — Trackable Links + Order Attribution (the MVP)

1. Migration: extend `marketing_campaigns` (short_code), `orders` (utm_term, utm_content, campaign_id FK, attribution JSONB, first_touch_at), `customers` (first_touch fields). Backfill short_codes for existing campaigns.
2. Backend: `short_code_generator.py`, `link_builder.py`, `campaign_resolver.py`, `attribution_sanitizer.py`. Pure functions, fully unit-testable.
3. Backend: `trackable-link` endpoint, `validate-path` endpoint, `campaign performance` endpoint. Contract tests cover each.
4. Backend: extend `CheckoutRequest` with `utm_term`, `utm_content`, `attribution`. Extend the checkout service to stamp Order columns + customer first_touch.
5. Merchant hub: `campaignApi.ts`, `MarketingCampaigns` list page, `MarketingCampaignDetail` page with Overview + Trackable Links tabs, attribution badge in Orders list.

**Ship-able state**: a merchant can create a campaign, generate a trackable link, share it, complete a purchase via that link, and see the order attributed in both the orders list and the campaign detail page. No journey persistence yet — only direct-purchase clickthrough.

### P2 — Visitor Journey Persistence

1. Storefront: `attribution-client.ts`, `attribution-types.ts`, `useAttribution.ts`, `AttributionContext.tsx`. Cookie write/read on page load.
2. Storefront: tenant layout mounts the provider. CheckoutForm reads from context, posts to API.
3. CookieBanner copy update (R-08).

**Ship-able state**: a visitor can land on the storefront via campaign URL, browse, leave, return within 90 days, purchase, and the order is still attributed.

### P3 — Funnel Event Tagging + Per-Campaign Funnel Dashboard

1. Migration follow-up: extend `funnel_events` with UTM columns + campaign_id FK + referrer column. Concurrent index creation.
2. Backend: extend `tracking.py` to read attribution from cookie (server-side) or request body, stamp funnel rows.
3. Backend: extend `analytics_repository.campaign_performance()` to include funnel stages from `funnel_events`.
4. Merchant hub: `CampaignPerformanceTab.tsx` ties it all together — session counts, ATC, checkout-started, conversion %.

**Ship-able state**: per-campaign full-funnel dashboard works. P1 and P2 are unaffected.

### P4 — Customer Share Buttons

1. Storefront: `ShareButtons.tsx` on PDP. Tag outgoing URLs with `utm_source=customer_share` etc.
2. No backend changes — `organic_share` falls under the FR-011 rule (raw UTM stored, no campaign FK, visible in traffic-sources only).

**Ship-able state**: viral shares are measurable separately from paid traffic.

---

## Risks / Open Questions

- **Risk**: The merchant hub today only has `WhatsAppCampaigns.tsx`; building the unified MarketingCampaigns UI is larger than expected. *Mitigation*: P1 can ship with a minimal list page (no segmentation UI, no email-template builder). The marketing-campaigns send pipeline already exists on the backend; we're surfacing what's there, not building the send pipeline.
- **Risk**: The Crockford alphabet excludes `I/L/O/U`; merchants doing visual eyeballing might miscopy `1` vs `J` or `5` vs `S`. *Mitigation*: never ask the merchant to type the code — always show it as a copy button. The code only appears in copy-pasted URLs and QR codes.
- **Risk**: 90-day cookie + persistent attribution under declined consent may not be acceptable if NUMU later expands to EU buyers. *Mitigation*: documented as a v1 assumption explicitly tied to the Egyptian market. Revisit during EU expansion.
- **Risk**: Multi-store merchants — a customer who shops across two stores on the platform gets two separate `numu_attribution` cookies (one per host). *Mitigation*: this is the intended behavior per "each store is its own attribution silo" (R-01 cookie domain).
- **Open**: Should the campaign-send code paths (email/WhatsApp/SMS) auto-tag links in the campaign body via `link_builder`? *Decision*: out of v1 scope. The link builder is available for it but the send service can opt in incrementally — the marginal UX win is small if merchants are already pasting trackable links manually.
- **Tech debt — TD-AUDIT-LOG-001** (conditional): Build platform-wide audit log for campaign + trackable-link mutations. *Opens only if* T063 finds that no audit-log infrastructure exists today (`audit_log` / `event_log` / `order_activity` not present or not used for admin actions). *Risk if deferred*: cannot resolve "who created this trackable link?" disputes; insider risk of staff redirecting attribution to a fake campaign. *Severity*: Low — campaign data is non-sensitive, order-level fraud detection lives elsewhere, merchants can spot anomalies in the orders feed. *Revisit trigger*: any merchant-raised attribution dispute, OR opportunistically as part of a future "admin actions audit" initiative. *Sourced from*: [security-review-followup.md](./security-review-followup.md).

## Phase Status

- [x] Phase 0 — Outline & Research (research.md complete)
- [x] Phase 1 — Design & Contracts (data-model.md, contracts/, quickstart.md complete)
- [ ] Phase 2 — Task generation (`/speckit-tasks`)
- [ ] Phase 3 — Implementation (`/speckit-implement`)

## Next Step

Run `/speckit-tasks` to generate the dependency-ordered task list from this plan plus the contracts.

## Complexity Tracking

No constitution-check violations to justify (no constitution exists). Complexity Tracking section intentionally omitted.
