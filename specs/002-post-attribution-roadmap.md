# Post-Attribution Roadmap

> Forward-looking work after feature **001-utm-campaign-attribution** + the
> 5-feature follow-on stack shipped this round. Treat this as the punch list,
> not a history doc.

**Last updated:** 2026-05-22

---

## 0. Status snapshot — what's in flight

14 PRs across 5 repos are open and stacked. Nothing is merged yet. Code is
written, lint/format clean, unit tests pass, no Sentry findings remain on the
backend PRs.

### Backend (NUMU-api)
| PR | Branch | Tip | What it ships |
| -- | ------ | --- | ------------- |
| [#321](https://github.com/NUMU-IO/NUMU-api/pull/321) | `001-utm-campaign-attribution` | `dbdfe8a1` | Parent — UTM + campaign attribution foundation + 2 Sentry HIGH fixes |
| [#322](https://github.com/NUMU-IO/NUMU-api/pull/322) | `feat/ltv-by-channel-dashboard` | `ceeda546` | `/analytics/ltv-by-channel` endpoint |
| [#323](https://github.com/NUMU-IO/NUMU-api/pull/323) | `feat/short-link-redirector` | `286e34bb` | Apex `/r/{short_code}` redirector |
| [#324](https://github.com/NUMU-IO/NUMU-api/pull/324) | `feat/campaign-coupon-fk` | `9ff4504a` | Coupon ↔ campaign FK + auto-issue endpoint |
| [#325](https://github.com/NUMU-IO/NUMU-api/pull/325) | `feat/customer-journey-table` | `d0acc841` | `customer_touches` table + journey endpoint |
| [#326](https://github.com/NUMU-IO/NUMU-api/pull/326) | `feat/multi-touch-attribution` | `a49d51a0` | 5 attribution models + multi-touch endpoint |

### Merchant hub (numo-merchant-hub)
| PR | Branch | What it ships |
| -- | ------ | ------------- |
| [#117](https://github.com/NUMU-IO/numo-merchant-hub/pull/117) | `feat/ltv-by-channel-dashboard` | `/analytics/ltv` page |
| [#118](https://github.com/NUMU-IO/numo-merchant-hub/pull/118) | `feat/short-link-redirector` | Short-link checkbox in trackable-link builder |
| [#119](https://github.com/NUMU-IO/numo-merchant-hub/pull/119) | `feat/campaign-coupon-fk` | Discount Codes tab on Campaign Detail |
| [#120](https://github.com/NUMU-IO/numo-merchant-hub/pull/120) | `feat/customer-journey-table` | Journey timeline on Customer Detail |
| [#121](https://github.com/NUMU-IO/numo-merchant-hub/pull/121) | `feat/multi-touch-attribution` | `/analytics/multi-touch` dashboard |

### Storefront layer
| Repo | PR | What it ships |
| ---- | -- | ------------- |
| numu-egyptian-bazaar | [#155](https://github.com/NUMU-IO/numu-egyptian-bazaar/pull/155) | `/track` POSTs include attribution envelope in body |
| numu-theme-sdk | [#13](https://github.com/NUMU-IO/numu-theme-sdk/pull/13) | `useAnalytics` sends funnel-event shape + customer_id from bridge |
| numu-storefront | [#11](https://github.com/NUMU-IO/numu-storefront/pull/11) | `/api/storefront/track` proxy + `__numu_customer` bridge |

---

## Phase A — Land the stack (≈ 5–7 days, mostly other people's time)

**Goal:** all 14 PRs merged, migrations applied to test → stage → prod, end-to-end
smoke confirmed.

### A.1 Code review pass
- [ ] Backend PRs reviewed in order (#321 → #322 → #323 → #324 → #325 → #326)
- [ ] Hub PRs reviewed in order (#116 → #117 → #118 → #119 → #120 → #121)
- [ ] Storefront chain reviewed (bazaar #155, SDK #13, storefront #11)
- [ ] Each Sentry-resolved comment marked addressed by the human reviewer

### A.2 Merge order
1. Backend stack merges to `master`
2. Hub stack merges to `dev`
3. Storefront layer merges (any order — bridges are no-op if one side ships first)
4. Bazaar storefront merges separately

### A.3 Apply migrations to test environment
- [ ] `alembic upgrade head` on `test.numueg.app` DB
- [ ] Three new migrations land in sequence:
  - `utm_campaign_attribution_20260521` (feature 001 — already deployed?)
  - `short_links_20260522`
  - `campaign_coupon_fk_20260522`
  - `customer_touches_20260522`
- [ ] Smoke: `\d+ short_links`, `\d+ customer_touches`, `\d+ coupons` show the new columns / tables

### A.4 Manual quickstart
Walk the full path on `test.numueg.app`:
1. Create a campaign in the hub → generate a trackable link → check the short URL
2. Visit the trackable URL → land on the storefront → confirm `numu_attribution` cookie is set
3. Add to cart → checkout → confirm the order has `campaign_id` + `attribution`
4. Open `/analytics/ltv-by-channel` → see the test cohort
5. Open `/analytics/multi-touch` → flip through models → verify totals match
6. Open the customer's journey timeline → verify the touch appears with `is_first_touch=True`
7. Issue a coupon under the campaign → redeem at checkout from a different (no-UTM) visitor → confirm the order attributes to the campaign

### A.5 Stage rollout + 24h soak
- [ ] Deploy to `staging.numueg.app`
- [ ] 24h with synthetic traffic generating UTMs, conversions, journey
- [ ] Watch the new endpoints' latency in Grafana (once panels exist — see C.2)
- [ ] No new errors in Sentry above baseline

### A.6 Prod cutover
- [ ] Migration window scheduled (5 minutes; all migrations are online-safe)
- [ ] Backend deploy
- [ ] Heroku BYOT storefront deploy (numu-storefront)
- [ ] Hub deploy
- [ ] Cloudflare cache purge (if any of the new endpoints get edge-cached)

---

## Phase B — Remaining roadmap items

From the original 7-item plan, two items are unshipped.

### B.1 Bot filtering (~1 week initial + ongoing)

**Why:** every dashboard we just built degrades when bot traffic pollutes
`funnel_events` and `customer_touches`. A merchant looking at "Customer LTV by
channel" with bot-driven Facebook clicks will get misleading numbers.

**Scope:**
- User-Agent classifier (regex against known crawler/bot signatures)
- IP reputation check (lookup against MaxMind / Cloudflare bot scores if
  available; or open-source list)
- Apply at the `/track` ingress: if classified as bot, write to a separate
  `funnel_events_bot` table OR skip entirely
- Same gate on `customer_touches` capture
- Configurable per-store "treat as bot" override (some merchants want to keep
  the data for debugging)
- Daily Celery job that re-scans recent funnel_events for retroactive
  reclassification

**Files to touch:**
- `src/application/services/bot_classifier.py` (new)
- `src/api/v1/routes/storefront/tracking.py` (gate at ingress)
- `src/application/services/customer_touch_service.py` (gate before capture)
- Migration for `funnel_events.is_bot` boolean (or new bot table)

**Why this could wait:** for an Egyptian-market store with low organic traffic,
bot volume is probably <5% of sessions. The dashboards will be slightly wrong
but still directionally useful.

### B.2 TikTok + Google Ads pixel + CAPI (~2 weeks)

**Why:** Meta CAPI integration exists (`src/infrastructure/messaging/tasks/`).
Merchants advertising on TikTok or Google Ads have no equivalent server-side
event fan-out, so their attribution windows on those platforms are degraded.

**Scope per platform:**
- Server-side event API client (TikTok Events API; Google Ads Conversion API)
- Mirror the Meta CAPI pipeline pattern in `src/infrastructure/messaging/`
- Per-store credential storage (TikTok Pixel ID + Access Token; Google Ads
  Conversion Action ID + OAuth)
- Per-store enable toggle in merchant hub Settings
- Celery task that fans out funnel-event → relevant platforms based on
  enable flags
- Same opt-out / consent gating as Meta CAPI (Wave 3 Phase 18 pattern)

**Files to touch:**
- `src/infrastructure/messaging/tiktok_events.py` (new)
- `src/infrastructure/messaging/google_ads_conversion.py` (new)
- `src/application/services/capi_dispatcher.py` (route by platform)
- Hub: `pages/Settings/IntegrationsPage.tsx` (new platform tiles)

**Why this could wait:** Egypt's ad spend is overwhelmingly on Meta. TikTok is
growing but the merchant ROI of building this for v1 is marginal compared to
shoring up the existing Meta pipeline.

---

## Phase C — Production hardening (the "5-day path to 9/10 prod-ready")

These are NOT new features. They're the operational work that makes the stack
defensible in prod.

### C.1 Integration tests against real Postgres
- [ ] T058 test scaffold in `tests/integration/test_attribution_e2e.py` is
      currently `pytest.skip`-gated by `NUMU_E2E_ATTRIBUTION=1`
- [ ] Set up a CI lane that runs against a Postgres container with seed
      fixtures (a single migrant-store row, a customer, a campaign)
- [ ] Add equivalent tests for the new code: short-link, journey,
      multi-touch, campaign-coupon
- [ ] Wire into the existing GH Actions matrix

### C.2 Observability
- [ ] Sentry tags on the new endpoints (`/analytics/ltv-by-channel`,
      `/analytics/multi-touch`, `/r/{short_code}`, `/customers/{id}/journey`)
- [ ] Grafana panels for p50/p95/p99 latency on each new endpoint
- [ ] Grafana panel for `customer_touches` write rate (busiest write target
      after migration)
- [ ] Alert: `/multi-touch` p95 > 3s for 5 minutes (matches SC-008 cap)
- [ ] Alert: `customer_touches` write error rate > 1% for 10 minutes
- [ ] Alert: `/r/{short_code}` 5xx rate > 0.1% (redirect path is hot)

### C.3 Performance probes
- [ ] T061 perf probe (`tests/perf/test_journey_perf.py` — currently a stub)
      seeded and executed at 10k orders / 100k touches
- [ ] EXPLAIN ANALYZE on the 3 hot queries:
  - `compute_multi_touch_attribution._fetch_orders` (5000-order ceiling)
  - `compute_multi_touch_attribution._fetch_touches_for_customers`
  - `analytics_repository.ltv_by_channel`
- [ ] Confirm indexes (`ix_customer_touches_customer_ts`, etc.) are actually
      used — partial index predicates need a matching WHERE clause

### C.4 Rate limiting
- [ ] `/r/{short_code}` redirect — needs a per-IP cap (a script hammering the
      redirector could enumerate codes; the 8-char Crockford namespace makes
      enumeration infeasible but rate-limiting is cheap defense)
- [ ] `/track` — already gated by the storefront proxy timeout (3s), but
      backend should also enforce per-fingerprint rate ceiling

### C.5 Merchant onboarding docs
- [ ] Hub Help docs: "What is multi-touch attribution?" explainer with the
      5 models illustrated
- [ ] Hub Help docs: "How to read your customer journey timeline"
- [ ] Hub Help docs: "Generating short links for QR codes"
- [ ] Numu-docs repo: contracts for `/analytics/multi-touch` and
      `/analytics/ltv-by-channel` endpoints (for third-party integrations
      that might consume them)

---

## Phase D — LOW-severity follow-ups (cleanup, no rush)

Flagged during self-audit but not fixed in the current PR stack. Bundle into
a "post-merge cleanup" sprint or just close as won't-fix.

### Hub
- [ ] `LtvByChannelTab` / `CustomerJourneyTimeline` / `MultiTouchAttributionTab`
      have no distinct loading skeleton — show EmptyState on first paint
- [ ] Same components don't surface `query.error` outside of the multi-touch
      400 case (already fixed)
- [ ] Arabic pluralization is singular-only ("عميل" for any count) — proper
      dual/plural forms would match the platform tone
- [ ] `QrCodeDisplay` encodes the long URL when a short URL is available —
      could re-encode the short URL for smaller QR density
- [ ] "Link copied" toast doesn't distinguish short vs full
- [ ] `MultiTouchAttributionTab` model selector state not persisted in URL
      (lost on refresh)
- [ ] Backend default for `multi-touch` is `last_touch`, hub default is
      `linear` — pick one for consistency

### SDK
- [ ] `view_collection` maps to generic `page_view` — no separate funnel
      step for collection views

### Storefront proxy
- [ ] No request body size limit (Next.js default applies; explicit cap
      would be defensive)
- [ ] `x-numu-host` header trust relies on infra to strip client-set
      values — verify nginx config explicitly drops it

### Backend
- [ ] Pre-existing OpenAPI schema name collisions (20 model-name dupes)
      flagged by `test_no_model_name_collisions` — none caused by this
      work but the test fails in CI on the new branches
- [ ] `analytics_repository._NON_REVENUE_STATUSES` is still
      `(CANCELLED, REFUNDED)` everywhere except the multi-touch service —
      should we propagate the `RETURNED` exclusion to other analytics queries?
      Decision needed: is RETURNED non-revenue everywhere, or only in
      attribution?

---

## Phase E — Long-term Shopify-parity gaps

The "25-35% of Shopify's attribution surface" assessment still stands after
this round. To close more of the gap:

### E.1 Customer journey UX polish
- [ ] Intermediate-touch storage: currently `customer_touches` records every
      UTM-tagged inbound. A timeline view that ALSO shows page-views between
      touches would match Shopify Plus's "Customer Journey" report. Would
      require either pulling from `page_views` table OR adding non-UTM touch
      types. Decision: which axis is more useful for merchants?
- [ ] LTV cohort dashboard: data exists on `customers.first_touch_attribution`
      and `customer_touches.is_first_touch`; cohort-by-acquisition-month
      retention view is a natural next dashboard

### E.2 Discount-code intelligence
- [ ] Coupon redemption rate over time (per-code time-series)
- [ ] "Coupon attribution conflict" detection: when a coupon's campaign_id
      doesn't match the UTM-resolved campaign_id on the order — surface to
      the merchant so they know which channel claimed credit

### E.3 Bot filtering quality
- [ ] Beyond UA + IP: behavioral signals (zero scroll, sub-50ms page-view
      timing, no mouse movement) for client-side bot detection. The
      storefront would post a "human signal" beacon and the backend would
      treat sessions without it as suspect.

### E.4 Multi-touch model extensions
- [ ] Custom weights: let the merchant define their own position-based
      ratios (not just 40/40/20). Easy backend change; needs a settings UI.
- [ ] Window-bound first-touch: "first touch within 30 days of conversion"
      — easy with the touches table already collecting timestamps.
- [ ] Data-driven attribution (ML-derived weights from conversion paths) —
      genuinely 2026-Shopify-Plus territory; not v1 material.

### E.5 Channel grouping
- [ ] Today: utm_source bucket = display name. Shopify groups utm_source values
      into higher-level "Marketing channels" (Paid Social, Email, Organic
      Search, etc.) so the dashboard isn't 30 chips. Pure UI/config work.

### E.6 Branded short-link domain
- [ ] Today: `numueg.app/r/AB7K9X`. Merchants on custom domains might want
      `shop.acme.com/r/AB7K9X`. Requires per-store DNS + nginx handling.
- [ ] Or: a single short-link domain like `nm.ag` (3-letter co-branded with
      NUMU) — simpler, but requires registering and configuring the apex.

### E.7 Cross-device stitching
- [ ] Currently single-device only (per cookie, per browser). Cross-device
      requires linking sessions via authenticated customer identity OR
      probabilistic signals (browser fingerprint + geo + temporal proximity).
      Privacy-heavy; explicitly out of scope for v1 and probably v2.

---

## Decision log (open questions)

1. **Should `RETURNED` be a non-revenue status across all analytics, not just
   multi-touch?** Sentry flagged it for multi-touch; the rest of
   `analytics_repository` still treats RETURNED as revenue. Pick one and
   apply uniformly.

2. **Is the SDK's `view_collection → page_view` mapping correct?** If
   merchants want to see collection visits as their own funnel step, we'd
   need a separate step in the backend's `_VALID_FUNNEL_STEPS`.

3. **Does numu-storefront's `__numu_customer` bridge need to refetch on
   focus/visibility change?** Currently it only fetches on mount. A
   logged-out → logged-in transition in the same tab leaves the bridge
   stale until next navigation. Acceptable for analytics; might surface
   weirdness in BYOT themes that rely on the bridge for other use cases.

4. **Should we add an `analytics_attribution_reports` table for caching
   expensive multi-touch results?** The 5000-order cap exists because
   on-demand calculation is too slow at scale. A nightly Celery rollup
   would let dashboards serve from cache. Trade-off: data freshness vs
   cost. Pick after Phase C.3 perf probe confirms or denies the need.

5. **Bot filtering: per-store toggle or global?** A platform-wide bot list
   simplifies operations; a per-store override gives merchants control over
   "this looks like real traffic to me." Default to global with override.

---

## Risk register

| Risk | Likelihood | Impact | Mitigation |
| ---- | ---------- | ------ | ---------- |
| Migration locks on busy `orders` table | LOW | HIGH | All migrations are online-safe (no row rewrites); FKs use `NOT VALID`; manual smoke on stage before prod |
| `customer_touches` becomes the hottest write table | MEDIUM | MEDIUM | Indexed for the query patterns; partial indexes keep them small; Phase C.3 perf probe verifies |
| Multi-touch endpoint 5000-order cap surfaces to merchants on big stores | MEDIUM | LOW | Phase D.1 surfaces the 400 error clearly; long-term fix is C.3 + cached rollups (decision #4) |
| BYOT theme that doesn't install `__numu_attribution` bridge gets empty journey data | HIGH | LOW | numu-storefront installs the bridge for ALL BYOT themes; per-theme breakage requires per-theme audit |
| Cross-origin cookie issues in non-numueg.app deployments (custom domain) | MEDIUM | MEDIUM | The bazaar storefront fix (#155) bypasses the cookie problem entirely by sending envelope in body; numu-storefront proxy reads cookie server-side. Custom-domain stores haven't been tested. |

---

## Out of scope (explicit non-goals — won't ship in this roadmap)

- Cross-device stitching beyond logged-in customer
- Click fraud / ML-based bot detection
- Custom attribution models the merchant defines via UI
- Multi-currency LTV (today's LTV is store-default-currency only)
- Real-time attribution updates (current dashboards are on-demand
  recalculation, not streaming)
- Data-driven attribution (ML-derived weights)
- Customer-level identity resolution (matching customers across devices via
  hashed email/phone)

Anything not listed in Phases A–E is undecided; reopen the spec process when
it becomes a priority.
