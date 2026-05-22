# Security Review — Task List

**Feature**: 001-utm-campaign-attribution
**Reviewed**: 2026-05-21
**Artifacts reviewed**: spec.md, plan.md, research.md, data-model.md, contracts/, quickstart.md, tasks.md
**Reviewer**: pre-implementation security pass

---

## Executive Summary

The task list is generally well-structured for security: sanitization is foundational (T005), tenant scoping is explicitly tested (T014), persistent attribution for declined-consent visitors is intentional and paired with a cookie-banner copy correction (T041), and the cookie's threat model has been thought through (host-scoped per-store, SameSite=Lax, non-sensitive payload).

However, three issues need to be addressed before implementation starts:

1. **No explicit authorization-check task** on the three new merchant endpoints (T017/T019/T050) — relying on "Depends on auth" in the contract isn't the same as encoding a per-task verification. Cross-tenant data leak risk if `campaign_id` from the URL path isn't validated against the authenticated user's store.
2. **SSRF surface on `validate-path` (T019)** — the endpoint issues HEAD requests; while constrained to the store's canonical origin, the task lacks explicit guardrails (no internal-IP blocklist, no redirect-loop cap, no rate limit).
3. **Cryptographic randomness for `short_code` (T004)** is not specified — must use `secrets`, not `random`, even though the security consequence is minor.

Plus a handful of medium/low gaps: test coverage for negative cases, attribution JSONB size cap, audit logging considerations.

**None block design**. All can be addressed by amending existing tasks or adding 3–4 new ones via `/speckit-security-review-followup`. The plan itself does not need to change.

---

## Tasks Reviewed

All 62 tasks in `tasks.md` (T001–T062), with particular attention to:

- T004 — `short_code_generator`
- T005 — `attribution_sanitizer`
- T011 — `campaign_resolver`
- T017 — POST `/trackable-link` endpoint
- T019 — POST `/validate-path` endpoint
- T022 — Checkout attribution stamping (cross-tenant resolution)
- T034 — Storefront `attribution-client.ts` (cookie handling)
- T041 — Cookie-banner copy update
- T045 — Server-side cookie parsing in tracking endpoints
- T050 — GET `/campaigns/{id}/performance` endpoint

---

## Vulnerability Findings

### 🔴 High — F-01: Authorization not explicit on new merchant endpoints

**Affected tasks**: T017, T019, T050
**Risk**: Cross-tenant data leak. A merchant authenticated to store A could call `/api/v1/stores/{B}/campaigns/{B_campaign_id}/performance` and receive store B's data if authz is only checked at the path-parameter level and not at the FK level.

**What the contract requires** (`contracts/merchant-campaign-api.md` Authentication section):
> the authenticated user must have write access (for the link generator) or read access (for performance) on `store_id`

**What the tasks specify**: Nothing explicit. The task descriptions name the endpoint and the request/response shape but do not encode an authorization-check step. NUMU's existing `Depends(get_current_user)` pattern requires the route handler to additionally verify the user→store relationship; some routes do, some don't.

**Specific risk paths**:
- T017 (`/campaigns/{campaign_id}/trackable-link`): if `campaign_id` is fetched without filtering on `store_id`, a merchant could generate trackable links for another store's campaign. The link itself isn't damaging (it's a URL on someone else's storefront), but it leaks campaign existence + name + short_code.
- T050 (`/campaigns/{campaign_id}/performance`): same problem, much higher impact — reveals revenue, order counts, top products from another store.
- T019 (`/storefront/validate-path`): less impactful, but a merchant on one custom domain could probe another's storefront pages by manipulating `store_id`.

**Fix**: Each endpoint task must include "and verify the authenticated user has [write|read] access to `store_id`, and reject requests where `campaign_id`'s `store_id` doesn't match the path's `store_id`." Add a dedicated authorization-check task: load the campaign with `WHERE id = :campaign_id AND store_id = :store_id LIMIT 1` and 404 if not found. The 404 (not 403) avoids leaking campaign existence.

### 🔴 High — F-02: SSRF surface on `validate-path`

**Affected task**: T019
**Risk**: The endpoint issues an arbitrary HEAD request from the backend's network. While the path is concatenated onto `canonical_origin` (the store's own public storefront), several attack vectors remain:

- **Redirect to internal**: HEAD `https://acme.numueg.app/about` could 302 to an internal admin URL if the storefront ever has an open-redirect; the validator would then HEAD that internal URL with backend-side credentials/network access.
- **DNS rebinding**: a custom-domain store points its DNS at an internal IP between the time the merchant configures it and the time validate-path runs.
- **Slow-loris-style stalls**: the 3-second timeout is per-request; without connection-level timeouts (DNS, TCP, TLS), a slow read can pin a worker.

**Fix** (amend T019 description):
- Issue only `HEAD` (already specified), do not follow redirects automatically — manually check the `Location:` header and reject any redirect whose host is not the same as `canonical_origin`.
- Use a hard 3-second total timeout (`requests.head(..., timeout=3)` or `httpx.AsyncClient(timeout=3)`), and additionally validate the resolved IP is not in private/loopback/link-local ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10`).
- Cap response size (don't read body) — HEAD-only requests should never read more than headers.
- Cap per-merchant per-minute call rate (probably already covered by the existing API rate limiter, but verify).

### 🟠 Medium — F-03: Cryptographically secure RNG not specified for short_code

**Affected task**: T004
**Risk**: Per R-02, short_codes must be non-guessable so that one merchant can't enumerate another's campaigns by hitting `?utm_campaign=*` strings (also, predictable codes would let a competitor probe campaign existence). If the implementation uses `random.choice(...)` it is seeded predictably from system time and codes become guessable.

**Fix**: T004 must specify `secrets.token_urlsafe` or `secrets.choice` for the random source. Crockford base32 alphabet is independent of the RNG choice — the alphabet stays the same; only the source changes.

Update T004 description from:
> Implement short-code generator ... Crockford base32, 6-char output, retry-on-conflict generator function with `generate(store_id, session)` signature.

To:
> Implement short-code generator ... Crockford base32 (32-char alphabet excluding `I/L/O/U`), 6-char output drawn from `secrets.choice(...)` (NOT `random`), retry-on-conflict generator function with `generate(store_id, session)` signature.

### 🟠 Medium — F-04: Attribution JSONB size not capped on ingest

**Affected tasks**: T021, T042, T043, T045
**Risk**: The `numu_attribution` cookie is composed client-side and posted to `/track` and `/checkout`. A malicious or buggy client could submit a 1MB attribution payload (e.g., 10kB referrer URL × all the nested fields). It gets stored in `orders.attribution` JSONB, in `customers.first_touch_attribution`, and in any funnel-event step_data fallback.

**Fix**: Cap each string field in `AttributionTouch` to its sensible max via Pydantic (`Field(max_length=N)`):
- `utm_*` strings: 200 chars (already in sanitizer)
- `referrer`: 500 chars (matches funnel_events.referrer column)
- `landing_path`: 500 chars
- `gclid`, `fbclid`: 256 chars
- `session_id`: 64 chars
- Whole envelope serialized: reject if `> 4KB`.

Add to T002 (the value object task) that these caps are part of the value-object definition, and to T021 / T042 / T043 that the schema validators enforce them.

### 🟡 Low — F-05: No negative-case tests for path scheme injection

**Affected task**: T020
**Risk**: T020 (validate-path contract test) lists 200/422/redirect cases but not scheme injection (`http://internal.local/`, `//evil.com/`, `\\\\evil`).

**Fix**: Amend T020 to include test cases for:
- `http://internal.local` → 422 with reason `external_host`
- `//evil.com/path` → 422
- `\\evil.com\path` → 422
- `/../../etc/passwd` → 422 (path-traversal attempt) or accepted as a path that just happens to 404
- A path that 302s to an off-origin host → 422

### 🟡 Low — F-06: Cross-tenant short_code collision attack not covered in tests

**Affected task**: T023
**Risk**: T023 covers "unknown short_code → campaign_id is NULL". But it doesn't cover the case where a short_code from store A is sent to store B's `/checkout`. The resolver should not stamp store A's campaign_id onto store B's order — that would be a privacy + analytics integrity issue.

**Fix**: Amend T023 to add a case: "submit a checkout for store B with `utm_campaign=eid-sale-AB7K` where AB7K is the short_code of a campaign on store A; assert order.campaign_id is NULL (resolver scopes by store_id)."

The `campaign_resolver` test T014 already mentions multi-store isolation, but a route-level contract test that confirms end-to-end behavior is also worthwhile.

### 🟡 Low — F-07: Cookie banner copy update lacks verification

**Affected task**: T041
**Risk**: T041 updates the banner copy but doesn't include a verification step. It's possible to update the copy in `CookieBanner.tsx` while accidentally leaving other on-site privacy disclosures (privacy policy page, /privacy markdown content, FAQ entries) saying the opposite. The merchant signs up for the platform expecting one privacy model; the platform delivers another.

**Fix**: Amend T041 to include a sweep: grep the storefront repo for any pages that describe what "decline" means, and update them to match the new functional-analytics framing. Also check `seo-server.ts` `pageCopy('/privacy')` — that's a translated string template.

### 🟡 Low — F-08: No audit-log task for campaign creation / link generation

**Affected**: No task — gap
**Risk**: Trackable links can lead to attributed revenue. Without an audit log, you cannot answer "who created this campaign + link?" if a merchant disputes a tracked sale, or if a malicious internal user (rogue staff) creates campaigns to redirect attribution.

**Fix**: This may already be covered by existing platform-wide audit logging. If not, add a small task in Phase 7 (Polish) to confirm campaign create/update + trackable-link generation are recorded in whatever audit log the platform uses (look for `audit_log`, `event_log`, or admin-action logging). Out of scope to *build* an audit log if none exists.

### 🟡 Low — F-09: No XSS-rendering test for UTM-as-display-text

**Affected**: T032 (orders-list badge), T053 (top-products list)
**Risk**: Attribution columns (`utm_campaign`, campaign name, etc.) are displayed in the merchant dashboard. React auto-escapes by default — but only if no `dangerouslySetInnerHTML` is used. The sanitizer strips `<>"` from inputs, but defense-in-depth requires the rendering layer not introduce new attack surface.

**Fix**: Amend T032 + T053 to specify "render via standard JSX text interpolation, no `dangerouslySetInnerHTML` for any campaign-derived field, no `eval`-style template helpers." Lightweight; the developer just needs to follow the existing badge/cell patterns.

---

## Confirmed Secure Patterns

The following are explicitly designed for security and are reflected in the task list:

✅ **T005 — UTM sanitization on ingest**: Strips control characters and `<`, `>`, `"`, length-caps to 200. Applied at every ingest point (T021 checkout, T042/T043 tracking).

✅ **T010 — Tenant-scoped FK + index**: `campaign_id` FK on `orders` and `funnel_events` is paired with `ON DELETE SET NULL`; partial indexes are scoped by `(store_id, campaign_id)` so cross-tenant queries cannot accidentally hit foreign data.

✅ **T011 + T014 — Tenant-scoped campaign resolver**: Resolver lookups `WHERE store_id = :store_id AND short_code = :code`. Unit test explicitly covers multi-store isolation.

✅ **R-09 / T021 — Length-capped UTM columns**: 200-char limit on all UTM fields prevents storage bloat and downstream-display surprises.

✅ **R-01 / contract — Cookie scope is per-host**: `numu_attribution` cookie has no `Domain=` directive, so subdomain stores and custom-domain stores each get their own cookie. No cross-store leakage by design.

✅ **R-01 — `SameSite=Lax`**: Survives cross-site campaign clicks (the common case) but rejected on cross-site POSTs. Prevents the cookie from being read in a CSRF-style attack.

✅ **R-01 — Non-sensitive cookie payload**: UTMs and `session_id` (ULID) only. No PII, no auth tokens, no customer identifier. `HttpOnly: false` is therefore acceptable.

✅ **FR-009 + T041 — Persistent attribution under denied consent is by design**: The user explicitly chose this framing. T041 updates the cookie banner so users are not misled about what "decline" governs. Privacy posture is documented in spec assumptions.

✅ **R-06 / T019 — `validate-path` does not duplicate route-matching**: Uses HEAD to the canonical origin rather than maintaining a separate allowlist that would drift from the frontend's actual routes. (But see F-02 for hardening.)

✅ **R-05 / T017 — Server-side QR generation**: Single source of truth for "the URL the merchant copies" and "the URL encoded in the QR." No client-side regeneration drift.

✅ **R-03 / T008 — Dedicated UTM columns on funnel_events**: Replaces JSONB key-lookup pattern with indexed columns. Predictable query plans, no expression-index proliferation.

---

## Recommended Follow-Up Actions

1. **Run `/speckit-security-review-followup`** to fold the High and Medium findings (F-01, F-02, F-03, F-04) into the task list as either amendments to existing tasks or new tasks. The Low findings (F-05, F-06, F-07, F-08, F-09) are good to fold in too but lower urgency.

2. **Before implementation starts**, ensure:
   - F-01 is resolved (authorization checks added to T017/T019/T050 descriptions).
   - F-02 is resolved (SSRF guardrails written into T019 description).
   - F-03 is resolved (T004 mandates `secrets`).

3. **During implementation**, every reviewer of these endpoints should re-check the tenant scoping at the SQL-query level — not just at the route-handler level.

---

## Verdict

**Pre-implementation security posture**: ✅ Acceptable to proceed after F-01, F-02, F-03 are addressed. The remaining findings are low-priority and can be folded in opportunistically.

The plan demonstrates good security thinking (sanitization-first, tenant-scoped resolver, host-scoped cookies, explicit privacy framing). The gaps are largely **implementation detail not yet captured at the task level** — exactly the kind of thing this review exists to surface before code is written.
