# Security Review — Plan (Pre-Implementation)

**Feature**: WhatsApp Integration Phase 1 — Backend Foundation
**Branch**: `backend-030-whatsapp-foundation`
**Date**: 2026-05-24
**Reviewer**: speckit-security-review-plan (automated)
**Plan revision**: `specs/backend-030-whatsapp-foundation/plan.md` (post-clarify, post-Phase-1)

---

## Executive Summary

Plan is **broadly safe** and aligns with the NUMU-api constitution (Privacy by Hashing N/A, GDPR Recital 47 path documented, async-first + strict typing, RLS on every new tenant table). Existing patterns (`service_credentials` AES-256, `message_log` audit-of-record, `app.current_store_id` RLS variable, Meta signature verification) are reused rather than duplicated.

**Findings**: 3 HIGH, 7 MEDIUM, 3 LOW. None of the HIGH items block planning — they are design clarifications that must be resolved before or during implementation. Two HIGH findings can be closed by lightweight spec edits; one HIGH (template submission semantics under shared platform WABA) is a real design hole that needs a decision.

**Recommendation**: Address HIGH-1 (storefront opt-in abuse), HIGH-2 (webhook signature fallback oracle), and HIGH-3 (shared-WABA template submission) before `/speckit-tasks`. The 7 MEDIUM items can be folded into the task list as discrete subtasks.

---

## Plan Artifacts Reviewed

| Artifact | Status | Notes |
|----------|--------|-------|
| `spec.md` | ✅ Reviewed | 42 FRs, 12 SCs, 5 clarifications applied |
| `plan.md` | ✅ Reviewed | Constitution check PASS pre + post Phase 1 |
| `research.md` | ✅ Reviewed | 8 operational research items |
| `data-model.md` | ✅ Reviewed | 3 new tables + extensions; RLS on all |
| `contracts/whatsapp-connection.openapi.yaml` | ✅ Reviewed | |
| `contracts/whatsapp-templates.openapi.yaml` | ✅ Reviewed | |
| `contracts/whatsapp-opt-ins.openapi.yaml` | ✅ Reviewed | |
| `contracts/whatsapp-scheduled-sends.openapi.yaml` | ✅ Reviewed | |
| `contracts/whatsapp-dead-letters.openapi.yaml` | ✅ Reviewed | |
| `contracts/whatsapp-webhook-meta.md` | ✅ Reviewed | |
| `quickstart.md` | ✅ Reviewed | |
| Project memory (`MEMORY.md` + linked notes) | ✅ Reviewed | Constitution principles + dual-storefront + InstaPay-cred pattern referenced |

---

## Vulnerability Findings

### HIGH-1 — Storefront opt-in endpoint is abusable

**Where**: `contracts/whatsapp-opt-ins.openapi.yaml`, `POST /storefront/{store_slug}/whatsapp/opt-in` (anonymous, rate-limited)

**Issue**: The endpoint accepts any phone number from any unauthenticated caller. Anyone who knows a store slug can write opt-in rows for arbitrary phones. Impact:
- A bad actor opts a victim's phone in for store X, then triggers a real order or message that causes the victim to receive unwanted WhatsApp from X.
- Multiplied across stores, this becomes a phone-number-enumeration + nuisance-messaging vector.

**Why the existing mitigation is insufficient**: `rate_limit_storefront` middleware throttles request frequency but not request *legitimacy*. A slow-burn script can still write thousands of opt-ins.

**Fix options** (pick one before implementation):
1. **Tie the opt-in to a live checkout session**: require a checkout session token in the request; verify the phone in the body matches the phone in the active cart for that session. Cleanest fix; aligns with how the spec describes "checkout consent checkbox tick."
2. **CAPTCHA / Turnstile** in front of the endpoint. Adds vendor dependency + degraded UX.
3. **Confirmation step**: write opt-in as `pending`, send a confirmation template requesting reply "YES" to activate. Strongest but slowest UX; doubles message volume.

**Recommendation**: Option 1. Spec already says the storefront writes the row "when the customer ticks the WhatsApp consent checkbox" — that implies a checkout context. Make the checkout-session-token requirement explicit in the contract and add a request-signature check.

**Severity**: HIGH (real abuse vector, compliance-adjacent — Meta penalises platforms with high opt-in-then-block ratios).

---

### HIGH-2 — Webhook signature fallback chain creates an oracle

**Where**: `contracts/whatsapp-webhook-meta.md` § "Signature verification"

> "Resolution order: try platform first; if signature fails, try BYO via the `entry[].id` (waba_id) → store lookup."

**Issue**: Trying multiple HMAC keys in sequence and accepting whichever one verifies turns the webhook into an oracle for "which apps does NUMU know about?" An attacker can submit crafted payloads and observe response timing or error variance to enumerate which WABA IDs have BYO credentials registered. Beyond enumeration, a per-key fallback weakens the security guarantee: any of several keys can authenticate the payload, increasing the blast radius of a single compromised key.

**Fix**: Pre-resolve the target WABA from the payload (`entry[].id` is unsigned but is the authoritative routing key). Look up the store and the **single** correct secret for that WABA (platform secret if it's a platform-managed store; BYO secret if BYO). Verify once. No fallback.

**Edge case**: if `entry[].id` is missing or doesn't resolve → return 400 (or 200 with no-op logged, per Meta's preference to avoid retry storms) — never accept the payload.

**Severity**: HIGH (active attack surface; webhook is internet-exposed).

---

### HIGH-3 — Template submission semantics under shared platform WABA

**Where**: `contracts/whatsapp-templates.openapi.yaml` `POST /stores/{store_id}/whatsapp/templates`; `spec.md` FR-026

**Issue**: In `platform_managed` mode, all stores share NUMU's single platform WABA. WhatsApp templates are scoped per WABA on Meta's side — submitting a custom template via NUMU creates it under NUMU's WABA where it's accessible to every other platform-managed store. Risks:
- **Cross-store visibility**: Store A's custom "Promo for women's perfume" template appears in Store B's template list (or worse, sends from Store B).
- **Approval-budget exhaustion**: Meta caps template submission rate per WABA. A single noisy merchant could DoS NUMU's template-approval throughput for every other platform-managed store.
- **Rejected content blast radius**: a flagged or rejected template from one merchant degrades NUMU's WABA quality rating, affecting send-tier and pricing for all platform-managed stores.

**Plan does not address this.** The data model has `is_system` to flag NUMU-seeded templates and `store_id` to scope visibility on the NUMU side, but Meta does not know about NUMU's `store_id` — at Meta's layer, all templates under the platform WABA are global.

**Fix options**:
1. **Restrict platform-managed mode to system templates only**. Custom template submission requires BYO mode. Simplest; matches the intent of platform-managed ("just works, no setup"). Requires updating FR-026 and the contract to return 403 for platform-managed stores.
2. **Namespace template names with store slug** (e.g., `numu_{store_slug}_promo_perfume`) and filter the local `whatsapp_templates` list by store_id. Solves cross-store visibility but not approval-rate or quality-rating issues.
3. **Per-store WABA under NUMU's BMA** — each store gets its own WABA inside NUMU's Business Manager Account. Solves all three but blows up the operational model; not in scope for Phase 1.

**Recommendation**: Option 1 for Phase 1. Document Option 3 as a forward path if custom templates on platform-managed becomes a real demand.

**Severity**: HIGH (design hole; will cause incidents once a single merchant uses it at scale).

---

### MEDIUM-1 — `customers/redact` GDPR webhook extension not enumerated as a task

**Where**: `data-model.md` § "DSAR / Erasure"

**Issue**: The data model says rows are deleted "alongside other customer tables (CASCADE is via app logic, not DB cascade, to preserve store-level audit)." Plan does not list the corresponding task to extend the existing `customers/redact` handler to purge `whatsapp_opt_ins`, `whatsapp_scheduled_sends`, and `whatsapp_dead_letters`. Missing this is a GDPR Recital 47 violation (Constitution Principle II — NON-NEGOTIABLE).

**Fix**: Add an explicit task in `/speckit-tasks` step to extend the redaction handler. Add a corresponding integration test (`tests/integration/whatsapp/test_customer_redact_purges_whatsapp_rows.py`).

**Severity**: MEDIUM (deterministic to fix; high downside if missed).

---

### MEDIUM-2 — Dead-letter list/get returns PII without role check

**Where**: `contracts/whatsapp-dead-letters.openapi.yaml` `GET /stores/{store_id}/whatsapp/dead-letters`

**Issue**: Dead-letter rows include `phone`, `template_params` (which may carry customer name, address fragments, order totals), and full error history. Contract uses generic `bearer` auth — any user with a valid store-scoped token can list and read. In multi-user stores (merchant + staff + viewer roles), low-privilege staff would have full access to recent failed-send content.

**Fix**: Require an `admin` or `owner` role on the merchant team. Add role guard in the route. Match the pattern used by `service_credentials` endpoints (which already gate by role).

**Severity**: MEDIUM (real exposure to a real role hierarchy).

---

### MEDIUM-3 — Rate limiting absent on BYO connect

**Where**: `contracts/whatsapp-connection.openapi.yaml` `POST /stores/{store_id}/whatsapp/byo/connect`

**Issue**: Each BYO connect triggers three Meta API calls. A merchant (or malicious admin token holder) submitting repeatedly burns NUMU's Meta API rate-limit budget on the platform's IP. Meta's enforcement is per-Meta-app, so a noisy BYO connector affects NUMU's overall reputation.

**Fix**: Per-store rate limit (e.g., max 5 connect attempts / 10 min) plus a per-NUMU-IP backstop. Reuse the existing rate-limit middleware if available.

**Severity**: MEDIUM (DoS-flavoured, indirect impact on platform reputation).

---

### MEDIUM-4 — Dead-letter replay has no rate limit

**Where**: `contracts/whatsapp-dead-letters.openapi.yaml` `POST /stores/{store_id}/whatsapp/dead-letters/{dl_id}/replay`

**Issue**: Replay enqueues a real WhatsApp send. A compromised admin token (or a curious merchant) could replay every dead-letter at once — a self-inflicted send spike that may breach Meta's per-WABA rate limits and degrade quality rating. The double-send guard prevents duplicates *per dead-letter row* but does not throttle the *rate* of replays.

**Fix**: Per-store replay rate limit (e.g., max 20 replays / minute). Surface a 429 with `Retry-After`. Optional bulk-replay endpoint that internally paces.

**Severity**: MEDIUM.

---

### MEDIUM-5 — Celery workers must set `app.current_store_id`; no compile-time guarantee

**Where**: `research.md` § R4

**Issue**: RLS protects the database only when `app.current_store_id` is set on the session. Existing tasks (`whatsapp_campaign_tasks.py`, `abandoned_cart_tasks.py`) do this; new tasks will need to as well. If a future task forgets, queries will fail (empty results from RLS), but the worse failure mode is a developer "fixing" it by bypassing RLS with `current_setting('app.current_store_id', true)` returning `NULL` and the policy treating the row as visible.

**Fix**: Provide a `tenant_session(store_id)` async context manager (if not already in the codebase) that sets the session variable on entry and clears it on exit. Mandate its use via a Ruff custom rule or a mypy plugin that flags raw `AsyncSessionLocal()` use inside `messaging/tasks/`.

**Severity**: MEDIUM (operational fragility — the kind of bug that bites a future contributor 3 months in).

---

### MEDIUM-6 — Sensitive fields in BYO connect body must be sanitized from logs

**Where**: `contracts/whatsapp-connection.openapi.yaml` BYO connect request body (`access_token`, `app_secret`)

**Issue**: Plan does not specify log-redaction policy for these fields. FastAPI's default request logging (and Sentry / structlog defaults) can capture full request bodies on error. A 500 from the BYO validation use-case could expose the merchant's Meta access token in logs.

**Fix**: Add the field names to the existing log-sanitization config. Verify via a unit test that asserts these strings never appear in formatter output for a synthetic error path.

**Severity**: MEDIUM (sensitive but contained; tokens have ~60 day lifetimes by default at Meta).

---

### MEDIUM-7 — Customer-merge cross-store wording is ambiguous

**Where**: `data-model.md` § `whatsapp_opt_ins` "Guarantees"

> "customer_id linked lazily — if a guest opt-in is later identified with a customer record, the existing customer-merge use-case updates the FK"

**Issue**: Wording does not explicitly forbid the merge from touching opt-in rows in a *different* store. If the customer-merge use-case has a path that joins customers across stores (e.g., during a backoffice consolidation tool), opt-in FKs could be rewritten across the tenant boundary.

**Fix**: Tighten the line to "the customer-merge use-case updates the FK *only for opt-in rows whose `store_id` matches the merge target's `store_id`*." Add a test (`tests/security/test_customer_merge_does_not_cross_store_optins.py`).

**Severity**: MEDIUM (defensive; assumes the merge use-case is well-behaved today, but planning doc shouldn't leave the door open).

---

### LOW-1 — Meta webhook payload replay not de-duplicated explicitly

**Where**: `contracts/whatsapp-webhook-meta.md`

**Issue**: Meta retries webhook deliveries on non-2xx. Existing handler may or may not be idempotent for status-update events. New `message_template_status_update` events could be processed twice with no observable side effect besides redundant DB writes (which is fine), or could double-fire downstream notifications if any are added later.

**Fix**: Note in the contract that the template-status handler MUST be idempotent (re-applying the same status is a no-op). Verify via a test that posts the same payload twice.

**Severity**: LOW.

---

### LOW-2 — Meta error body surfaced verbatim to merchant on BYO validation failure

**Where**: `BYOValidationFailure` schema — `meta_error: object`

**Issue**: Surfacing the raw Meta error body to the merchant is a usability win but could expose internal-ish Meta fields (request IDs, sub-codes) that aren't useful to a merchant and theoretically increase the attack surface for crafted error responses if a man-in-the-middle interposed.

**Fix**: Whitelist the Meta error fields surfaced: `code`, `error_subcode`, `message`, `type`. Drop `fbtrace_id` etc. unless we deliberately want them for support escalation.

**Severity**: LOW (cosmetic / hygiene; not exploitable as planned).

---

### LOW-3 — Two-tier opt-in policy semantics for "system" sent ack

**Where**: `spec.md` FR-037 + `contracts/whatsapp-webhook-meta.md` § "Acknowledgement reply"

**Issue**: The STOP-ack reply explicitly bypasses the opt-in guard. The contract says: "bypassing the opt-in guard for this specific message only (the customer just messaged in...)". This is correct intent but could become a backdoor if a future developer extends the bypass list. Today it's documented as `optout_confirmation_en` / `_ar` template only.

**Fix**: Add an explicit allowlist constant in code, with a comment pointing at FR-037 and the constitution principle. Add a unit test asserting that no other template name appears in the bypass set.

**Severity**: LOW (defensive against future drift).

---

## Confirmed Secure Patterns

The plan correctly reuses or extends existing patterns where it matters:

1. **AES-256 encryption of BYO credentials** via `service_credentials` (FR-022). Matches existing pattern; no new crypto code.
2. **RLS on every new tenant-scoped table** with the same `app.current_store_id` session variable pattern (Constitution Principle V). Migration template in `research.md` § R4 mirrors existing migrations.
3. **GDPR Recital 47 legitimate-interest basis documented** in `plan.md` § Constitution Check (Principle II — NON-NEGOTIABLE).
4. **No raw PII in cross-store tables** — all three new tables are tenant-scoped (Principle I N/A by design).
5. **Two-tier opt-in policy aligned with Meta policy** — utility templates respect explicit opt-out, marketing requires active opt-in (FR-011).
6. **Phone canonicalization at Pydantic v2 + DB CHECK layers** (FR-008 + data-model.md CHECK constraints) — defence in depth.
7. **Idempotency via `message_log` lookup** (R5) instead of a new dedup table. No state duplication.
8. **Bearer JWT on all merchant-facing endpoints**; anonymous-only on the storefront opt-in (which is the source of HIGH-1 — see above).
9. **90-day dead-letter retention** with daily purge task (FR-035a) bounds PII exposure window.
10. **Double-send guard on replay** queries `message_log` before re-issuing (FR-035).
11. **Existing webhook route URL preserved** for backward compatibility (FR-040). Additive-only changes.
12. **Exponential backoff via Celery's declarative `autoretry_for` + `retry_backoff`** (R3) rather than hand-rolled retry loops. Reduces opportunity for retry-related state bugs.
13. **Async-first + strict typing** consistently applied across the new modules (Constitution Principle IV).
14. **Spec-First, Tests-From-Spec** — every FR mapped to a test file in `plan.md` § Project Structure. Constitution Principle III met.
15. **Non-retriable error classification short-circuits retries** (R3) and routes straight to dead-letter — avoids burning Meta rate-limit budget on hopeless retries.

---

## Recommendations Before `/speckit-tasks`

Required (HIGH):
- [ ] **HIGH-1**: Update spec FR-006/FR-007 and `whatsapp-opt-ins.openapi.yaml` to require a checkout-session token on the storefront opt-in endpoint.
- [ ] **HIGH-2**: Update `whatsapp-webhook-meta.md` § Signature verification — resolve target WABA first from `entry[].id`, then verify against the single correct secret. No fallback.
- [ ] **HIGH-3**: Decide platform-managed template submission policy. Recommended: restrict to system templates in platform-managed mode (require BYO for custom templates). Update FR-026 + `whatsapp-templates.openapi.yaml` accordingly.

Strongly recommended (MEDIUM — can be folded as discrete tasks):
- [ ] MEDIUM-1: Add explicit task to extend `customers/redact` to purge the three new tables.
- [ ] MEDIUM-2: Add role guard on dead-letter list/get/replay endpoints.
- [ ] MEDIUM-3: Rate-limit BYO connect (per-store + per-IP backstop).
- [ ] MEDIUM-4: Rate-limit dead-letter replay.
- [ ] MEDIUM-5: Ship a `tenant_session(store_id)` async context manager and lint rule for `messaging/tasks/`.
- [ ] MEDIUM-6: Add `access_token` and `app_secret` to log-sanitization config + verify with a test.
- [ ] MEDIUM-7: Tighten customer-merge wording in `data-model.md` and add a security test.

Optional (LOW):
- [ ] LOW-1: Document idempotency requirement on template-status webhook handler + test.
- [ ] LOW-2: Whitelist Meta error fields in `BYOValidationFailure.meta_error`.
- [ ] LOW-3: Add allowlist constant + test for the opt-in-guard bypass set.

---

## Sign-off State

**Not yet signed off.** HIGH-1, HIGH-2, HIGH-3 must be resolved before tasks generation. MEDIUM and LOW items can flow through as tasks once the design questions are answered.

Suggested follow-up: run `/speckit-security-review-followup` after addressing HIGH items to convert MEDIUM/LOW findings into discrete spec / task entries.
