# Research — UTM & Campaign Attribution

**Feature**: 001-utm-campaign-attribution
**Phase**: 0 (Outline & Research)
**Date**: 2026-05-21

Each section below answers a question that affected the design before the data model and contracts were written. Format per item: *Decision*, *Rationale*, *Alternatives considered*.

---

## R-01 — Attribution storage shape on the client

**Decision**: A single first-party cookie named `numu_attribution`, JSON-encoded, signed only with origin (no HMAC needed since it is informational), 90-day TTL, `SameSite=Lax`, secure when served over HTTPS. Cookie payload:

```json
{
  "v": 1,
  "first_touch": {
    "ts": "2026-05-21T14:33:00Z",
    "utm_source": "facebook",
    "utm_medium": "social",
    "utm_campaign": "eid-sale-2026-AB7K",
    "utm_term": null,
    "utm_content": null,
    "gclid": null,
    "fbclid": "PAQ...",
    "referrer": "https://www.facebook.com/",
    "landing_path": "/product/abc-123"
  },
  "last_touch": { /* same shape */ },
  "session_id": "01HX2M..."  // ULID, regenerated when session_fingerprint regenerates
}
```

**Rationale**: One cookie is easier to read/write atomically than splitting first/last touch across two cookies (avoids partial-write races). JSON payload keeps the structure self-describing for the inevitable v2. 90 days matches the GA4 / Meta Ads default attribution window — merchants comparing reports between platforms will see consistent attribution windows. `SameSite=Lax` is required so the cookie survives the first navigation from an external campaign URL (cross-site referrer); `Strict` would discard it.

**Alternatives considered**:
- Separate `numu_first_touch` and `numu_last_touch` cookies → atomic write hazards, double the header bytes per request.
- `localStorage` instead of cookie → would not survive the SSR fetch path in Next.js (server cannot read localStorage), making the funnel-event POST harder to enrich server-side.
- `sessionStorage` for all visitors → was the leading option before the user's FR-009 reversal; rejected because the user explicitly chose persistent attribution for everyone (classified as functional analytics).

---

## R-02 — Campaign short_code generation

**Decision**: 6-character base32 (Crockford alphabet) generated server-side at campaign create time, unique within `(store_id, short_code)`. Stored as a `String(8)` column on `marketing_campaigns` (8 leaves headroom). Generator retries on uniqueness conflict (max 5 attempts).

Example codes: `AB7K9X`, `M3PQ2N`, `7H8RWZ`.

**Rationale**:
- Base32-Crockford excludes `I`, `L`, `O`, `U` to avoid look-alike confusion and accidental profanity. Important: merchants will hand-edit these into Facebook ad URLs and read them off QR codes.
- 6 chars at 32-char alphabet = ~10⁹ possible codes. Per-store uniqueness scope means even a heavy merchant with 10k campaigns has collision probability ~10⁻⁵ per insert → 5-retry loop is overwhelming margin.
- Generated server-side, not derived from the campaign name. Renaming "Eid Sale" → "Ramadan Special" must not invalidate links that already shipped.
- 6 chars keeps the URL short enough to fit in WhatsApp previews and on a printed QR-code business card.

**Alternatives considered**:
- nanoid (URL-safe 21 chars) → too long for a UTM value alongside the other params; ugly in shared screenshots.
- Sequential integers per store → enumerable (a competitor can iterate `?utm_campaign=1, 2, 3, …` to learn campaign counts); also fragile if campaigns get hard-deleted.
- UUID → 32 chars, defeats the "looks clean in a URL" requirement.
- Hashids (Sqids) → shorter encoding of integer IDs, but still enumerable; same issue as sequential.

---

## R-03 — UTM persistence in funnel events

**Decision**: Add dedicated nullable columns to `funnel_events`:
- `utm_source VARCHAR(200)`, indexed
- `utm_medium VARCHAR(200)`
- `utm_campaign VARCHAR(200)`, indexed
- `utm_term VARCHAR(200)`
- `utm_content VARCHAR(200)`
- `campaign_id UUID NULLABLE FK marketing_campaigns.id ON DELETE SET NULL`, indexed
- `referrer VARCHAR(500)` (preserves existing `step_data.referrer` as a top-level column for queryability)

These are written by the server in `tracking.py::_emit_funnel_event` after reading the visitor's `numu_attribution` cookie via the `Cookie:` header (server-side, even though the existing tracker is fire-and-forget POST).

**Rationale**:
- The repository's existing `get_attribution_data` (`funnel_event_repository.py:194`) already reads UTM from `step_data` JSONB. JSONB key-lookups need expression indexes (`((step_data->>'utm_campaign'))`) and complicate joins. A dedicated column is simpler and consistent with `orders.utm_source`.
- Indexed columns on `(store_id, utm_campaign, created_at)` and `(store_id, campaign_id, created_at)` make the per-campaign funnel query a normal range scan instead of a JSONB extraction.
- `step_data` stays for everything else (path, referrer detail, custom event payload).

**Alternatives considered**:
- Keep UTMs in `step_data` JSONB only → harder to query, no FK enforcement for `campaign_id`, no nullability semantics.
- Add a foreign table `attribution_snapshots` joined by `session_fingerprint` → adds a JOIN to every funnel query. Premature normalization for v1.

---

## R-04 — Server-side attribution stamping (where to read the cookie)

**Decision**: Read `numu_attribution` from the request `Cookie:` header inside `tracking.py::track_page_view` and `tracking.py::track_analytics_event`, parse the JSON, validate the shape (Pydantic), and pass the parsed snapshot into `_emit_funnel_event` (extended kwargs). For the Celery async path, include the snapshot in the task payload alongside `event_id`, `step`, etc.

For checkout, do the same in `checkout.py` — extend `CheckoutRequest` with an `attribution` payload submitted by the client (since the client is already constructing the body, this is more reliable than re-parsing the cookie there). The cookie is still the source of truth on the client.

**Rationale**:
- Reading the cookie server-side means the funnel events get correct attribution even if the client forgets to attach it to the POST body.
- The Celery task is the persistence chokepoint — extending its event payload means both sync and async paths end up writing the same shape to `funnel_events`.
- Checkout's extension via the request body is consistent with how it already accepts `utm_source/medium/campaign` — the client packs them into the JSON body.

**Alternatives considered**:
- Read the cookie only on the client, send via header → loses attribution for visitors with restrictive client extensions that block client-set headers but allow cookies.
- Read at a middleware layer → adds latency to every request including ones that have nothing to do with attribution.

---

## R-05 — QR code generation

**Decision**: Server-side via the `qrcode` Python library (`pip install qrcode[pil]`), returned as a base64-encoded PNG in the trackable-link response. Sized 512×512 at error-correction level M.

**Rationale**:
- Server-side keeps the UI dependency-free (no `qrcode.react` bundle on the merchant hub).
- Single source of truth: the same code path that generates the trackable URL also generates the QR, eliminating drift between "the URL the merchant copies" vs "the URL encoded in the QR."
- 512×512 PNG at level M is plenty for both screen and reasonable-size print (~5cm at 300dpi). Merchants printing for posters/billboards can scale up via the standard print pipeline.
- `qrcode` is pure-python with PIL for rendering — no system dependencies.

**Alternatives considered**:
- Client-side `qrcode.react` → bundle bloat in the merchant hub; would need separate dependency for download-as-PNG; risk of URL drift if regenerated client-side from stale state.
- External QR API (e.g., goqr.me) → introduces an external dependency in the hot merchant-tooling path; potential rate-limit and outage exposure.

---

## R-06 — Custom destination path validation

**Decision**: New backend endpoint `POST /merchant/stores/{store_id}/storefront/validate-path` that takes a path string and returns `{ valid: bool, reason?: string, suggested_canonical?: string }`. Validation logic:
1. Path must start with `/` and contain no scheme or host (reject `http://`, `//evil.com/...`).
2. Path length ≤ 500 characters.
3. Issue a server-side `HEAD` request to `{canonicalOriginFor(store)}{path}` with a 3-second timeout. Accept 200, 301, 302, 308; reject 4xx, 5xx, timeouts.
4. If the response is a redirect, follow once and report the canonical path in `suggested_canonical` so the merchant can opt to use it.

**Rationale**:
- Avoids the alternative of duplicating Next.js's route-matching logic on the Python side — that would drift the moment a new route is added to the storefront.
- A 3-second `HEAD` is cheap and merchants validate links interactively (one click = one HEAD), not at scale.
- Canonical suggestion handles the common case of merchants pasting `/product/abc/` (trailing slash) when the canonical is `/product/abc`.

**Alternatives considered**:
- Hard-coded allowlist of routes (`/`, `/products`, `/product/[id]`, `/collections`, …) → drifts with frontend changes; rejects legitimate custom-theme routes.
- Client-side validation only → trivially bypassed by an inspector-savvy user; we want server-enforced sanity.
- No validation at all (trust merchant input) → produces broken trackable links and merchant blame falls on the platform.

---

## R-07 — Merchant-hub campaign UI: build new vs extend

**Decision**: Build new. The hub today has `WhatsAppCampaigns.tsx` only — there is no UI yet for the `MarketingCampaignModel` (email/SMS) that the backend already supports. The plan creates:
- `src/pages/MarketingCampaigns.tsx` — list view (already-supported channels: email/whatsapp/sms — union-join across the two campaign tables)
- `src/pages/MarketingCampaignDetail.tsx` — detail view with tabs: Overview, Audience, Performance, Trackable Links
- `src/components/campaigns/TrackableLinkBuilder.tsx` — the link generator + QR panel
- `src/services/campaignApi.ts` — API client matching the existing `couponApi.ts` pattern

**Rationale**:
- Confirmed by source: `grep -r MarketingCampaign src/` in `numo-merchant-hub` returns no usage. The page didn't exist when this spec was written; previous documentation suggesting it did was incorrect.
- Building campaign management UI is in scope because without it, merchants have no way to set the `short_code` or even create campaigns through the UI — and shipping the attribution layer without the campaign-creation UI is half a feature.

**Alternatives considered**:
- Extend `Marketing.tsx` (current coupons + upsell rules page) → conceptual mismatch; would force users to scroll past discounts to find campaigns.
- Extend `WhatsAppCampaigns.tsx` → wrong direction; WhatsAppCampaigns should eventually fold *into* MarketingCampaigns once channel parity is reached. Building the unified UI now sets the right shape.

---

## R-08 — Cookie banner copy alignment (per FR-009)

**Decision**: Update `src/components/store/CookieBanner.tsx` to use clearer category-level copy that matches the resolved consent model:

> *"We use cookies and similar technologies to operate the storefront (these always run), and — with your consent — to share data with our advertising partners. You can change your choice anytime."*

The "Accept" / "Decline" buttons continue to govern only the marketing-tracking category (Meta CAPI fan-out and any future ad-platform pixels). First-party attribution is classified as a "functional" cookie and persists regardless of the decision. The privacy-policy link should explain this categorization.

**Rationale**:
- Honors FR-009 (persistent attribution for everyone) while keeping the banner honest about what the toggle actually controls.
- Avoids "dark pattern" framing — the banner says clearly that functional cookies run regardless.
- The existing `numu:cookie-consent` localStorage key continues to drive third-party pixel firing (no change to that contract).

**Alternatives considered**:
- Leave the banner copy untouched → would mean visitors are misled about what "Decline" does.
- Remove the banner entirely → loses the merchant's third-party-pixel consent gating, breaks Meta CAPI's `opt_out` flag wiring at `tracking.py:474`.

---

## R-09 — UTM length caps and sanitization

**Decision**: Continue with the existing `VARCHAR(200)` cap on all UTM fields (already on Order; mirror to FunnelEvent and CheckoutRequest). Apply control-character stripping (`\x00-\x1F` and `\x7F`) on ingest via a Pydantic validator. Reject (silently coerce to null + log) any UTM value containing `<`, `>`, or `"` after stripping — these are not valid UTM token characters and indicate either tampering or a bad copy-paste.

**Rationale**:
- 200 chars is the existing convention and is generous enough for any legitimate UTM (Facebook ad URLs rarely exceed 80).
- Control characters can break downstream log/CSV exports; trivial to strip.
- The `<>"` filter is a cheap mitigation for the trivial case of a tamper attempt; serious XSS protection lives at the rendering layer (React auto-escapes) but defense-in-depth is free here.

**Alternatives considered**:
- Whitelist regex (e.g., `^[A-Za-z0-9._\-]+$`) → too restrictive; legitimate UTMs from third-party tools (Mailchimp campaign IDs etc.) include `+`, `:`, `%`-encoded characters.
- No sanitization → tampered values pollute logs and CSV exports; trivial to abuse.

---

## R-10 — Attribution standards alignment

**Decision**: Follow Google Analytics 4 / Meta Ads conventions:
- 5 standard UTM dimensions (`source`, `medium`, `campaign`, `term`, `content`)
- 2 click identifiers (`gclid`, `fbclid`) captured into `attribution.first_touch.gclid` / `.fbclid` for future use (Google Ads / Meta cross-checking)
- 90-day attribution window (matches GA4 and Meta Ads defaults)
- Last-non-direct click attribution for orders; first-touch preserved for customer-level analytics

**Rationale**:
- These conventions are universal across ad platforms; merchants reading their NUMU dashboard will see numbers that reconcile with what Meta Ads Manager and Google Ads tell them.
- Capturing `gclid`/`fbclid` even without acting on them yet means we can wire up Google Ads conversion uploads later without backfilling.

**Alternatives considered**:
- Define custom attribution dimensions → cuts merchants off from comparing to industry-standard reports.
- Skip click identifiers → painful retrofit later.

---

## Summary of resolved unknowns

All Phase 0 questions resolved. No remaining `NEEDS CLARIFICATION` markers. Ready to enter Phase 1 (design).
