---
description: "Meta Ads extension — Custom Audiences hub UI, Lookalikes, per-campaign attribution panel, and Promote-on-Meta ad-draft creation. Builds on the OAuth + CAPI + Custom-Audience-Service foundation already shipped (Phases 1-22 / Waves 1-4)."
status: draft
created: 2026-05-24
---

# Feature 005 — Meta Ads Extension

## Why now

The Meta CAPI agent's Phases 1–22 + Waves 1–4 (merged to dev) shipped a load-bearing foundation:

- OAuth flow (gated on Meta App Review approval): `oauth_client.py`, `oauth/meta.py` start + callback routes.
- Full CAPI event pipeline: Search / Lead / CompleteRegistration / AddPaymentInfo / Purchase / Refund / Subscribe / Contact / AddToWishlist / CustomizeProduct. Multi-pixel fan-out, MENA phone normalization, Arabic name handling, IP/UA forwarding, advanced matching pipeline.
- Per-purpose granular consent (analytics / marketing / preferences / sale_of_data) with region defaults.
- COD-aware Purchase / Lead triggers via order-status transitions (not just payment webhooks).
- Custom Audience push service (`meta_custom_audience_service.py`) — builds hashed member lists for 3 prebuilt segments (high_ltv / cart_abandoners / lapsed), POSTs to `/{audience_id}/users`, respects `marketing_consent != 'opted_out'`.
- This branch's PR #337 + #338: Purchase events now carry `numu_utm_campaign` / `numu_campaign_id` / etc. into Meta `custom_data`; Disconnect now revokes server-side + audit-logs.

What's still missing is the **merchant-facing surface** that turns that foundation into a 1-click marketing workflow. Today merchants can't trigger a Custom Audience sync from the hub, can't see Meta attribution data per campaign, can't build a Lookalike, and can't push a sent campaign to Meta as an ad draft. This spec covers those.

## Goal

Surface the Meta CAPI / Custom Audience / Ad Account capabilities to merchants through three concrete hub experiences:

1. **Custom Audience sync UI** — a hub page to trigger, monitor, and resync segment-driven Custom Audiences without touching Meta Ads Manager.
2. **Lookalike audience creation** — one-click 1% / 3% / 5% Lookalikes targeting Egypt (or merchant-picked regions) on top of any synced Custom Audience.
3. **Per-campaign Meta attribution + Promote-on-Meta** — KPI card showing Meta-attributed conversions per campaign + a button to fork a sent campaign into a draft ad in the merchant's Ad Account.

All three are gated on the merchant having completed Meta OAuth (feature 001 v1) and Meta App Review having approved the NUMU Meta App for the `ads_management` + `catalog_management` scopes.

## User Stories

### US1 (P1) — Custom Audience sync hub page

**As a merchant**, I want a Marketing → Audiences page that lists my customer segments and lets me push each one to Meta as a Custom Audience with one click, so I can run lookalike ads without exporting CSVs.

#### Surface
- New route `/marketing/audiences` in numo-merchant-hub.
- Lists merchant's `customer_segments` (from feature 003) + 3 prebuilt segments (high_ltv / cart_abandoners / lapsed).
- Per row: name, current member count, last-synced timestamp, sync status badge (NOT_SYNCED / SYNCING / SYNCED / STALE / FAILED).
- Per row actions: **Sync to Meta** / **Resync now** / **View on Meta** (deep-link to Meta Ads Manager).
- Bulk action: enable / disable nightly auto-refresh per audience.

#### Backend wiring
- New entity `meta_custom_audiences`:
  ```
  id (UUID), store_id (FK), segment_id (FK customer_segments | NULL for prebuilt),
  prebuilt_kind (str | NULL — 'high_ltv'/'cart_abandoners'/'lapsed'),
  meta_audience_id (str), member_count (int),
  last_synced_at (timestamp), sync_status (str enum),
  auto_refresh (bool), pixel_id (str — which Meta dataset)
  ```
- `POST /stores/{id}/marketing/audiences/{segment_id}/sync` — enqueues a Celery task that calls `meta_custom_audience_service.push_to_meta()` with the segment members.
- `GET /stores/{id}/marketing/audiences` — list with sync_status from the table.
- `DELETE /stores/{id}/marketing/audiences/{audience_id}` — Meta `DELETE /{audience_id}` + soft-delete locally.
- Nightly Celery task `refresh_meta_custom_audiences` re-runs sync for `auto_refresh=true` rows.

#### Privacy
- Sync filters out customers where any of:
  - `customer.marketing_consent == 'opted_out'`
  - `customer.consent_settings.marketing == false`
  - (EU GDPR path, deferred to US4) `customer.address.country IN EU_COUNTRIES AND consent.marketing != 'explicit'`
- On consent change to opt-out: nightly task includes a DELETE diff against Meta to remove the now-opted-out members.

#### Minimum-size UX
- Meta requires 100+ matched users for an audience to be eligible for Lookalike. Show the member count + a warning chip when below threshold ("Add more members to unlock Lookalike").

---

### US2 (P1) — Lookalike audience creation

**As a merchant**, once I have a Custom Audience synced and it has ≥100 matched members, I want a "Build Lookalike" button that creates a 1% / 3% / 5% Lookalike targeting Egypt (or other countries I pick) without me leaving the hub.

#### Surface
- Custom Audience row action: **Build Lookalike** (disabled with tooltip when source has <100 members).
- Dialog: size (1% / 3% / 5% checkboxes — multiple allowed in one submission), country multi-select (default EG, common MENA options preselected).
- After submit: dialog closes, the new Lookalike(s) appear in the audience list with status badge "BUILDING (1%)" / "BUILDING (3%)" etc.
- Status polling: nightly task transitions BUILDING → READY when Meta's audience status flips.

#### Backend wiring
- `POST /stores/{id}/marketing/audiences/{audience_id}/lookalike` body: `{ sizes: [1, 3, 5], countries: ["EG"] }`.
- For each (size, country) tuple: POST `/{ad_account_id}/customaudiences` with `subtype: LOOKALIKE`, `origin_audience_id`, `lookalike_spec: { country: ..., ratio: 0.01 | 0.03 | 0.05 }`.
- Insert a `meta_custom_audiences` row per Lookalike (kind=`lookalike`, source_audience_id=<original>).

#### Failure modes
- Source audience <100 members → return 422 with a typed error code so the hub renders a localized message ("Source audience needs at least 100 members").
- Token missing `ads_management` scope → 412 Precondition Failed with `re_oauth_required: true` flag so the hub re-prompts for OAuth with expanded scope.
- Meta rate-limit → exponential-backoff retry via Celery (max 3 attempts).

---

### US3 (P2) — Per-campaign Meta attribution panel

**As a merchant**, on the campaign detail page, I want to see Meta's attribution view: "of N sales attributed to this campaign by Meta, X are last-touch and Y are assisted-touch."

#### Surface
- New KPI card on `MarketingCampaignDetail`, sits next to the existing Sessions / Sales / Orders / AOV cards.
- Data: campaign UTM-attributed Purchase events from Meta's `/insights` endpoint with `breakdowns=action_type`.
- Cached daily via Celery — show "Updated 4h ago" timestamp. Not real-time.
- Empty state when Meta not connected: "Connect Meta to see this" → linkable button to the integrations page.

#### Backend wiring
- New endpoint `GET /stores/{id}/marketing/campaigns/{campaign_id}/meta-attribution`.
- Reads from a new `meta_campaign_attribution_cache` table: `(campaign_id, snapshot_date, conversions_last_touch, conversions_assisted, value_total_cents, raw_response_json)`.
- Nightly task `refresh_meta_campaign_attribution` per active campaign queries Meta's `/insights` endpoint scoped to the campaign's `utm_campaign = short_code` filter.
- Surfaces cleanly when Meta isn't connected: endpoint returns `{ connected: false }` so the hub renders the empty state.

---

### US4 (P3) — Promote-on-Meta (sent campaign → draft ad)

**As a merchant**, on a completed marketing campaign's detail page, I want a "Promote on Meta" button that creates a draft ad in my Ad Account with the same hero image + CTA as the email, automatically targeted at the campaign's promoted-item Custom Audience (if synced).

#### Surface
- Button visible on campaign detail header **only when** `campaign.status == 'completed'` AND `store.meta.connected == true`.
- Click → preview dialog (creative preview, targeting summary, ad-account selector if multiple) → submit.
- After submit: shows the new ad's Meta Ads Manager deep-link in a toast. Merchant publishes from there (draft → active).

#### Backend wiring
- `POST /stores/{id}/marketing/campaigns/{campaign_id}/promote-on-meta`.
- Builds the creative from `campaign.inline_subject` (headline) + `campaign.promoted_item.snapshot.image_url` (hero) + `campaign.promoted_item.snapshot.url` (CTA link).
- Targeting: defaults to the synced Custom Audience matching the campaign's `audience_filter`. If no matching audience, defaults to "Egypt, 18-65" + the campaign's promoted-item product as a `product_set` for Dynamic Ads.
- Two-call flow:
  1. POST `/{ad_account_id}/adcreatives` → returns `creative_id`.
  2. POST `/{ad_account_id}/ads` with `status: 'PAUSED'` → returns `ad_id`.
- Always creates as PAUSED (Meta calls this "draft" in the UI) — merchant deliberately publishes from Meta Ads Manager. Never auto-launches paid ads from NUMU.

#### Out of scope
- Ad budget / bid strategy / schedule configuration — merchant edits in Meta Ads Manager.
- Ongoing performance tracking — that's US3.
- Image generation / Canva-style creative editor — feed Meta the raw assets we already have.

---

## Cross-story requirements

### Auth gate
All four stories are gated on three conditions:

1. NUMU Meta App env vars set (`NUMU_META_APP_ID` + `NUMU_META_APP_SECRET`) — i.e. App Review has cleared.
2. Merchant's store has a valid on-file token (`service_credentials.is_active = True` for `META_CAPI`).
3. Token has the required scopes (`ads_management` for all four).

The hub renders a "Connect Meta to use this feature" empty state when (2) is false, and a "Reconnect with additional permissions" prompt when (3) is false.

### Audit logging
Every state-changing call (sync, build lookalike, promote-on-meta) logs to `audit_events` under `ADMIN_CONFIG_CHANGE` so disputes have a trail.

### Rate limits
All four stories hit Meta's Graph API. Implement per-store circuit breakers (3 consecutive 4xx → mark `meta_circuit_open=true` for 1 hour, surface a banner in the hub).

### Data residency
Meta Custom Audience members are hashed before they leave NUMU's infrastructure (SHA-256 of lowercased trimmed email; E.164 phone with leading zeros stripped — Phase 14 already handles MENA). No raw PII ever leaves.

---

## Non-goals
- Other ad platforms (Google Ads, TikTok Ads — separate features each).
- Ad creative editing in NUMU (we hand off to Meta Ads Manager).
- Real-time attribution dashboard (US3 is cached daily — acceptable per merchant feedback).
- WhatsApp campaigns (their Meta WhatsApp Business API integration is separate).
- Backfilling historical campaigns into Meta — too risky for rate limits + Meta's audience-freshness model.

---

## Open questions for clarify phase

1. **Audience refresh cadence**: nightly for all auto-refresh audiences, or per-merchant configurable? Nightly is simpler but a high-velocity merchant might want hourly. Lean **nightly v1, per-merchant cadence in v2**.

2. **Lookalike country list**: hard-code MENA defaults (EG, SA, AE, KW, QA, BH, OM) or expose Meta's full ~190-country list with a search? Lean **MENA-only v1** since 99% of NUMU stores ship MENA-only.

3. **US3 Meta-attribution cache TTL**: 24 hours matches Meta's own UI refresh cadence. Worth running a fresh sync on every page load when the cache is >12h old? Lean **24h hard cache; explicit "Refresh now" button for impatient merchants**.

4. **Promote-on-Meta targeting fallback**: when the campaign's `audience_filter` doesn't map to a synced Custom Audience, do we (a) prompt the merchant to sync first, (b) target "Egypt, 18-65" generically, (c) attempt to build a one-off Custom Audience from the campaign's recipient list inline? Lean **(a) — prompt first** since (b) wastes ad spend on untargeted impressions and (c) couples ad creation to audience sync timing (Meta can take 6-24h on Custom Audience build).

5. **Audit log granularity**: per-recipient on Custom Audience sync (thousands of rows per push) or summary-only ("synced 1,247 members")? Lean **summary-only** — per-recipient is unmanageable at scale and the privacy-sensitive event is "this segment was synced," not "this customer's hash was uploaded."

---

## Dependencies
- Feature 003 — `customer_segments` table (referenced by US1's `meta_custom_audiences.segment_id` FK).
- Existing Meta CAPI agent's `meta_custom_audience_service.py` (used directly by US1's sync endpoint).
- This branch's PR #337 (`numu_utm_campaign` in CAPI custom_data) — feeds US3's per-campaign attribution query.
- This branch's PR #338 (Meta disconnect with revoke + audit) — ensures clean state when merchants disconnect.
- Meta App Review for `ads_management` + `catalog_management` (already submitted by the CAPI agent; status ⟶ external gate).

---

## Success criteria

- A merchant with a synced "High LTV" Custom Audience can build a 1% Lookalike for Egypt in under 60 seconds from clicking the action in the hub.
- 80% of completed campaigns get a "Promote on Meta" click within their first 7 days post-send (proves the loop is useful).
- Custom Audience sync respects opt-outs within one nightly refresh cycle of the customer flipping their consent (no data leakage to Meta past consent revocation).
- Zero merchant-facing errors from Meta token expiry — daily refresh task keeps tokens current; US3's empty-state handles the (rare) case where refresh fails.
- Audit log can answer "which segments were synced when, by whom, with what member count" for any 30-day window.
