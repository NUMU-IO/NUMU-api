# Security Review — Follow-Up Plan

**Feature**: WhatsApp Integration Phase 1 — Backend Foundation
**Branch**: `backend-030-whatsapp-foundation`
**Date**: 2026-05-24
**Source findings**: [`security-review-plan.md`](./security-review-plan.md) (3 HIGH, 7 MEDIUM, 3 LOW)
**Status**: Pre-tasks — `tasks.md` does not yet exist; nothing in the backlog can be marked "already covered."

---

## Executive Summary

13 findings from the plan-phase security review. **All 13 will be implemented now** — none are deferred as technical debt. The split is:

- **3 spec/contract edits** (HIGH-1, HIGH-2, HIGH-3) — apply directly to `spec.md` and `contracts/` before `/speckit-tasks` so the design hole is closed before implementation starts. Use `/speckit-security-review-apply` (or apply inline) to land these.
- **10 TASK-SEC entries** (all 7 MEDIUM + all 3 LOW) — fold into `tasks.md` when `/speckit-tasks` runs, sequenced as security foundations (TASK-SEC-001 through TASK-SEC-010).

No item is safe to defer:
- HIGH items are real attack/abuse vectors. Deferring HIGH-3 (template submission under shared WABA) would cause merchant-visible incidents at scale.
- MEDIUM-1 (`customers/redact` extension) is a GDPR Recital 47 obligation — Constitution Principle II is NON-NEGOTIABLE, so this cannot be technical debt.
- The remaining MEDIUM and all three LOW items are small enough that deferring them costs more in coordination overhead than implementing them now.

---

## Inputs Reviewed

| Input | Status |
|-------|--------|
| `security-review-plan.md` (13 findings) | ✅ Source of truth |
| `tasks.md` | ❌ Does not exist yet (pre-`/speckit-tasks`) |
| `spec.md` | ✅ Reviewed |
| `plan.md` | ✅ Reviewed |
| `data-model.md` | ✅ Reviewed |
| `contracts/` (5 OpenAPI + 1 webhook md) | ✅ Reviewed |
| `quickstart.md` | ✅ Reviewed |
| Project memory hub (`MEMORY.md` + linked) | ✅ Reviewed |
| Repo Constitution (`.specify/memory/constitution.md`) | ✅ Reviewed |

---

## Resolution Decisions

| Finding | Severity | Decision | Where it lands |
|---------|----------|----------|----------------|
| HIGH-1 — storefront opt-in abusable | High | Implement now — spec/contract edit | `spec.md` FR-006/FR-007 + `whatsapp-opt-ins.openapi.yaml` |
| HIGH-2 — webhook signature fallback oracle | High | Implement now — contract edit | `whatsapp-webhook-meta.md` |
| HIGH-3 — shared-WABA template submission | High | Implement now — spec/contract edit | `spec.md` FR-026 + `whatsapp-templates.openapi.yaml` |
| MEDIUM-1 — `customers/redact` extension | Medium | Implement now — TASK-SEC-001 | `tasks.md` |
| MEDIUM-2 — dead-letter PII access role | Medium | Implement now — TASK-SEC-002 | `tasks.md` |
| MEDIUM-3 — BYO connect rate limit | Medium | Implement now — TASK-SEC-003 | `tasks.md` |
| MEDIUM-4 — DLQ replay rate limit | Medium | Implement now — TASK-SEC-004 | `tasks.md` |
| MEDIUM-5 — `tenant_session` ctx mgr + lint | Medium | Implement now — TASK-SEC-005 | `tasks.md` |
| MEDIUM-6 — log sanitization for BYO secrets | Medium | Implement now — TASK-SEC-006 | `tasks.md` |
| MEDIUM-7 — customer-merge cross-store guard | Medium | Implement now — TASK-SEC-007 | `tasks.md` |
| LOW-1 — webhook idempotency | Low | Implement now — TASK-SEC-008 | `tasks.md` |
| LOW-2 — Meta error field whitelist | Low | Implement now — TASK-SEC-009 | `tasks.md` |
| LOW-3 — STOP-ack bypass allowlist constant | Low | Implement now — TASK-SEC-010 | `tasks.md` |

No `Track as technical debt` items. No `Already covered` items.

---

## Spec/Contract Edits To Apply Before `/speckit-tasks`

> **Status: ✅ APPLIED 2026-05-24.** All three edits have been landed in the planning artifacts. Verification spots:
> - **EDIT-A** → `spec.md` FR-007a added; `whatsapp-opt-ins.openapi.yaml` `/storefront/.../opt-in` requires `checkout_session_token`; 403 response with `phone_mismatch_with_cart` / `invalid_checkout_session` codes added.
> - **EDIT-B** → `whatsapp-webhook-meta.md` § Signature verification rewritten with the deterministic resolve-then-verify algorithm + edge cases. No fallback chain.
> - **EDIT-C** → `spec.md` FR-026 amended to BYO-only with 403 + `template_submission_requires_byo`; `whatsapp-templates.openapi.yaml` `POST /templates` documented as BYO-only with 403 response; Assumptions in `spec.md` updated.

These three edits land in the planning artifacts directly. They are not tasks — they are design corrections that close design holes the review uncovered.

### EDIT-A (resolves HIGH-1) — `spec.md` FR-006/FR-007 + `whatsapp-opt-ins.openapi.yaml`

**Spec change**: FR-006 / FR-007 add the requirement that storefront-side opt-in writes must be authenticated by a live checkout-session token and that the phone in the request body must match the phone in the active cart for that session.

**Contract change**: `whatsapp-opt-ins.openapi.yaml` — the `/storefront/{store_slug}/whatsapp/opt-in` request body gains a required `checkout_session_token` field. The handler verifies the token, loads the cart, and rejects (HTTP 403) if the phone does not match the cart's phone. Add a 403 response variant to the OpenAPI.

### EDIT-B (resolves HIGH-2) — `whatsapp-webhook-meta.md` § Signature verification

**Contract change**: Replace the fallback-chain wording with: "Resolve the target WABA from `entry[].id`. Look up the matching store via (a) platform WABA constant if `entry[].id` equals the platform WABA, else (b) `service_credentials` row with `service_name=WHATSAPP_BUSINESS` and matching `waba_id`. If neither resolves, return 200 with no-op (avoid Meta retry storms) and log a structured warning. Verify the HMAC-SHA256 signature against that single store's `app_secret`. If verification fails, return 401 — do NOT try other secrets."

### EDIT-C (resolves HIGH-3) — `spec.md` FR-026 + `whatsapp-templates.openapi.yaml`

**Spec change**: FR-026 gains a precondition: "Template submission is only permitted for stores in `byo` mode. In `platform_managed` mode, the endpoint returns 403 with code `template_submission_requires_byo` — only system templates (FR-030) are available. Document this as forward-looking: when per-store WABA provisioning becomes viable, this restriction is the trigger to lift."

**Contract change**: `whatsapp-templates.openapi.yaml` — `POST /stores/{store_id}/whatsapp/templates` gains a 403 response with the `template_submission_requires_byo` code. Add an Assumptions entry to `spec.md` noting that custom templates are a BYO-only feature in Phase 1.

---

## Immediate Remediation Tasks (TASK-SEC entries for `tasks.md`)

These 10 tasks should be appended to `tasks.md` when `/speckit-tasks` runs. Sequence them so security foundations (TASK-SEC-005, TASK-SEC-006) land **before** the feature tasks that depend on them (e.g., BYO connect task — would otherwise log the secret).

| Task ID | Title | Severity | Type | Source Finding | Depends On | Acceptance Criteria |
|---------|-------|----------|------|----------------|------------|---------------------|
| TASK-SEC-001 | Extend `customers/redact` webhook to purge WhatsApp tables | Medium | Implement | MEDIUM-1 | Migration task (must precede) | (a) `customers/redact` handler deletes rows in `whatsapp_opt_ins`, `whatsapp_scheduled_sends`, `whatsapp_dead_letters` where `customer_id` matches. (b) Integration test `tests/integration/whatsapp/test_customer_redact_purges_whatsapp_rows.py` posts a synthetic redact event and asserts row count = 0 after. (c) DSAR export test asserts the same tables are included in the customer-data export. OWASP A04 (Insecure Design) / GDPR Recital 47. |
| TASK-SEC-002 | Add role guard on dead-letter list/get/replay endpoints | Medium | Implement | MEDIUM-2 | — | (a) `whatsapp-dead-letters.openapi.yaml` updated with `security: [{ bearer_admin: [] }]` (or equivalent role-scoped scheme). (b) Route handlers gated by the same role guard pattern used by `service_credentials` endpoints (identify the exact decorator/middleware during implementation). (c) Test `tests/security/test_dead_letter_role_gating.py` verifies that staff/viewer tokens get 403 and owner/admin tokens succeed. OWASP A01 (Broken Access Control). |
| TASK-SEC-003 | Per-store + per-IP rate limit on BYO connect | Medium | Implement | MEDIUM-3 | — | (a) `POST /stores/{store_id}/whatsapp/byo/connect` returns 429 with `Retry-After` after 5 attempts in 10 min per store, and 30/min per IP. (b) Existing rate-limit middleware reused (or a thin per-store config layer added). (c) Test `tests/integration/whatsapp/test_byo_connect_rate_limit.py` asserts 429 after the threshold. OWASP A04 (Insecure Design — DoS). |
| TASK-SEC-004 | Per-store rate limit on dead-letter replay | Medium | Implement | MEDIUM-4 | — | (a) `POST /stores/{store_id}/whatsapp/dead-letters/{dl_id}/replay` returns 429 with `Retry-After` after 20 replays/min/store. (b) Test asserts 429 after threshold. OWASP A04. |
| TASK-SEC-005 | Provide `tenant_session(store_id)` async context manager + lint enforcement | Medium | Implement | MEDIUM-5 | — | (a) `src/infrastructure/database/tenant_session.py` exposes `async with tenant_session(store_id):` that sets and clears `app.current_store_id`. (b) All new WhatsApp Celery tasks use it. (c) Ruff custom rule (or equivalent — confirm during implementation) flags raw `AsyncSessionLocal()` instantiation inside `src/infrastructure/messaging/tasks/whatsapp_*.py`. (d) Test `tests/security/test_rls_celery_workers.py` runs the dispatcher with a deliberately unset session var and asserts queries return empty (RLS enforced). OWASP A01 (Broken Access Control — tenant isolation). |
| TASK-SEC-006 | Add BYO secrets to log-sanitization config | Medium | Implement | MEDIUM-6 | TASK-SEC-005 (so it's in place before the connect endpoint is wired) | (a) `access_token`, `app_secret`, `phone_number_id`, `waba_id` added to the existing log-redaction allowlist (locate in `src/config/logging_config.py` or equivalent). (b) Test `tests/security/test_byo_secret_log_redaction.py` triggers a synthetic 500 in the connect path and asserts the formatted log line contains `***` (or equivalent redaction marker) for each sensitive field. OWASP A09 (Security Logging Failures). |
| TASK-SEC-007 | Customer-merge cross-store guard for WhatsApp opt-ins | Medium | Implement | MEDIUM-7 | — | (a) `data-model.md` § Guarantees updated to: "customer-merge updates opt-in `customer_id` FK ONLY for rows whose `store_id` matches the merge target's `store_id`." (b) Customer-merge use-case (existing) extended with an explicit per-store predicate when touching `whatsapp_opt_ins`. (c) Test `tests/security/test_customer_merge_does_not_cross_store_optins.py` constructs two stores, places opt-ins for the same phone under each, merges customers, and asserts each store's opt-in row remains store-scoped. OWASP A01 (Broken Access Control). |
| TASK-SEC-008 | Template-status webhook handler must be idempotent | Low | Implement | LOW-1 | — | (a) Handler is no-op when payload state equals current row state. (b) Test `tests/integration/whatsapp/test_template_status_webhook_idempotent.py` posts the same payload twice and asserts no duplicate side effects (no extra DB writes, no duplicate notifications). (c) Brief inline comment + reference to FR-028. OWASP A04. |
| TASK-SEC-009 | Whitelist Meta error fields in `BYOValidationFailure.meta_error` | Low | Implement | LOW-2 | — | (a) Only `code`, `error_subcode`, `message`, `type` from Meta's error body are surfaced; `fbtrace_id` and other internal fields dropped. (b) Test `tests/unit/whatsapp/test_byo_validation_error_shape.py` asserts the field set. OWASP A09 (over-disclosure). |
| TASK-SEC-010 | STOP-ack opt-in-guard bypass allowlist as a constant + test | Low | Implement | LOW-3 | — | (a) `src/domain/whatsapp/send_guard.py` exposes `OPT_IN_BYPASS_ALLOWLIST: frozenset[str]` containing only `{"optout_confirmation_en", "optout_confirmation_ar"}`. (b) Guard code references this constant; no other code path can extend it without editing the constant. (c) Test `tests/unit/whatsapp/test_send_guard_bypass_allowlist.py` asserts the set membership and that any other template name with `bypass=True` raises `AssertionError`. OWASP A04 (Insecure Design — defense in depth against future drift). |

---

## Technical Debt Backlog

**(empty)** — no items deferred.

---

## Already Covered Items

**(empty)** — `tasks.md` does not yet exist; no prior backlog to match against.

---

## Confirmed Secure Patterns (carried forward from review)

These patterns will be **inherited** by the immediate-remediation tasks and should not be re-litigated:

1. RLS on every tenant-scoped table via `app.current_store_id` (Constitution Principle V).
2. AES-256 encryption of BYO credentials via existing `service_credentials` table.
3. GDPR R47 legitimate-interest basis documented (Constitution Principle II).
4. No PII in cross-store / network-scoped tables (Principle I — N/A here by design).
5. Two-tier opt-in policy (utility bypasses opt-in but respects opt-out; marketing requires both).
6. E.164 phone canonicalization enforced at Pydantic + DB CHECK layers.
7. Idempotency via `message_log` lookup — no duplicate dedup table.
8. Bearer JWT on merchant-facing endpoints; anonymous storefront opt-in tightened by EDIT-A.
9. 90-day dead-letter retention bounds PII exposure.
10. Double-send guard on replay (FR-035) queries `message_log` before re-issuing.
11. Existing webhook URL preserved (FR-040 — additive only).
12. Celery declarative `autoretry_for` + `retry_backoff` (no hand-rolled retry loops).
13. Non-retriable errors short-circuit to DLQ; do not burn Meta rate budget on hopeless retries.
14. Async-first + strictly-typed (Constitution Principle IV).
15. Spec-First, Tests-From-Spec (Constitution Principle III).

---

## Sequencing Recommendation for `/speckit-tasks`

When generating `tasks.md`:

1. **Apply EDIT-A, EDIT-B, EDIT-C first** (spec/contract changes — these are not tasks, they are design corrections).
2. Generate the regular feature tasks per `plan.md` (migration, models, repositories, use-cases, event handlers, Celery tasks, routes, schemas, tests).
3. **Insert TASK-SEC-005 and TASK-SEC-006 early** in the sequence — they are foundations consumed by the feature tasks (tenant session helper, log sanitization) and must land before the BYO connect / Celery dispatcher tasks they protect.
4. TASK-SEC-001 must follow the migration task (depends on the new tables existing) but precede the BYO connect task (so BYO-mode customers get redacted correctly from day one).
5. TASK-SEC-002, TASK-SEC-003, TASK-SEC-004 attach to their respective route tasks (DLQ, BYO connect, DLQ replay) — implement as part of the same PR/commit as the route.
6. TASK-SEC-007, TASK-SEC-008, TASK-SEC-009, TASK-SEC-010 can land anywhere after their related primary task; group them at the end of the implementation phase or batch into a "security hardening" mini-phase before `/speckit-implement` completes.

---

## Next Command

Two paths forward:

- **Apply edits programmatically** (recommended): run `/speckit-security-review-apply` and pass this follow-up doc as input. It will land EDIT-A, EDIT-B, EDIT-C in `spec.md` and `contracts/`, and stage the TASK-SEC entries for `tasks.md`.
- **Apply edits manually + then `/speckit-tasks`**: edit `spec.md` and `contracts/` by hand to apply EDIT-A/B/C, then run `/speckit-tasks` and append the TASK-SEC table above to the generated `tasks.md`.

Either way, the design holes are closed and the 10 TASK-SEC entries are ready to be sequenced into the implementation plan.
