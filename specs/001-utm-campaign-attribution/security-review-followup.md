# Security Review — Follow-Up Plan

**Feature**: 001-utm-campaign-attribution
**Generated**: 2026-05-21
**Source**: [security-review-tasks.md](./security-review-tasks.md)

---

## Executive Summary

The security review surfaced 9 findings: 2 High, 2 Medium, 5 Low. All 9 are recommended for **immediate remediation** before implementation begins — none of them are large enough to defer, and all are cheap if folded into existing task descriptions or added as small new tasks.

One finding (F-08, audit logging) has a *conditional* tech-debt component: if NUMU does not already have platform-wide audit logging, building it is out of scope for this feature, so it would become tech debt.

After applying these follow-ups, the task count grows from 62 → 71. No findings are deferred outright. No findings are duplicates of existing tasks.

---

## Inputs Reviewed

- [security-review-tasks.md](./security-review-tasks.md) — security review report
- [tasks.md](./tasks.md) — current task list (T001–T062)
- [contracts/merchant-campaign-api.md](./contracts/merchant-campaign-api.md), [contracts/storefront-attribution-api.md](./contracts/storefront-attribution-api.md), [contracts/link-builder-service.md](./contracts/link-builder-service.md)
- [research.md](./research.md), [data-model.md](./data-model.md), [spec.md](./spec.md), [plan.md](./plan.md)

---

## Resolution Decisions

| Finding | Severity | Resolution | Rationale |
| ------- | -------- | ---------- | --------- |
| F-01 (authz on endpoints) | High | Implement now | Cross-tenant data leak risk — cannot ship without |
| F-02 (SSRF on validate-path) | High | Implement now | Network egress from backend, must be locked down at build time |
| F-03 (secrets vs random for short_code) | Medium | Implement now | One-line change in T004, no excuse to defer |
| F-04 (attribution JSONB size cap) | Medium | Implement now | Schema-level fix; cheap; storage-blow-up risk is real |
| F-05 (validate-path negative tests) | Low | Implement now | Tests already in T020 — just add a few more cases |
| F-06 (cross-tenant short_code test) | Low | Implement now | One new assertion in T023 |
| F-07 (banner copy sweep) | Low | Implement now | Small scope expansion of T041 |
| F-08 (audit log for campaigns) | Low | Implement now (verify) + conditional tech debt | New small Phase-7 task; if audit log infra missing, tech-debt entry opens |
| F-09 (XSS rendering discipline) | Low | Implement now | One-line constraint added to T032 and T053 |

---

## Immediate Remediation Tasks

These integrate into `tasks.md` either as **amendments to existing tasks** or as **new tasks inserted in the relevant phase**. Apply via `/speckit-security-review-apply`.

| Task ID | Title | Severity | Type | Source Finding | Depends On | Acceptance Criteria |
| ------- | ----- | -------- | ---- | -------------- | ---------- | ------------------- |
| TASK-SEC-001 | Add authorization check to merchant endpoints (T017, T019, T050) | High | Amend existing | F-01 | T011 (campaign_resolver), existing `get_current_user` dep | Each endpoint loads the campaign (where applicable) with `WHERE id = :campaign_id AND store_id = :store_id`; returns 404 (not 403) if mismatch. Verified by adding cross-tenant negative cases to the existing contract tests T018, T020, T051. |
| TASK-SEC-002 | Harden `validate-path` against SSRF (T019) | High | Amend existing | F-02 | T019 | The handler: (a) resolves the path's target hostname via DNS and rejects if the resolved IP is in any of `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `::1`, `fc00::/7`, `fe80::/10` (using `ipaddress.ip_address(...).is_private/is_loopback/is_link_local`); (b) sets a hard 3-second total timeout including DNS + connect + read; (c) does NOT follow redirects automatically — reads the `Location:` header and rejects when the new host differs from the canonical origin; (d) issues HEAD only, no body read. Verified by new test cases in T020. |
| TASK-SEC-003 | Use `secrets` (not `random`) in `short_code_generator` (T004) | Medium | Amend existing | F-03 | none | Generator imports `secrets` and uses `secrets.choice(alphabet)`. Verified by code review (no `import random` in the module) and by the existing T012 test which can be left unchanged. |
| TASK-SEC-004 | Cap attribution payload sizes (T002, T021, T042, T043) | Medium | Amend existing | F-04 | none | `AttributionTouch` Pydantic model enforces per-field max_length: utm_* (200), referrer (500), landing_path (500), gclid (256), fbclid (256). `AttributionSnapshot` enforces session_id (64). Whole envelope JSON is rejected via a validator if serialized form exceeds 4096 bytes. Verified by a new unit test in T013 (sanitizer module or schema test file). |
| TASK-SEC-005 | Add scheme-injection + redirect-host negative cases to validate-path tests (T020) | Low | Amend existing | F-05 | TASK-SEC-002 | T020 covers, at minimum: `http://internal.local` → 422 external_host; `//evil.com` → 422; `\\evil.com` → 422; `/../../etc/passwd` → 422 path_malformed or path_not_found; a path that 302s to a different host → 422. |
| TASK-SEC-006 | Add cross-tenant short_code test in checkout (T023) | Low | Amend existing | F-06 | T011, T022 | T023 includes: insert a campaign in store A with short_code X; submit checkout for store B with `utm_campaign=name-X`; assert resulting order on store B has `campaign_id IS NULL`. |
| TASK-SEC-007 | Sweep + update other privacy disclosures during cookie-banner update (T041) | Low | Amend existing | F-07 | T041 | T041 includes a grep across `numu-egyptian-bazaar/` for "decline", "marketing cookies", "tracking", "consent" — every match is reviewed and updated to match the new functional-analytics framing. The `/privacy` page template in `seo-server.ts:pageCopy('/privacy')` is updated for both AR and EN. |
| TASK-SEC-008 | Verify campaign create/update/link-generation are audit-logged | Low | New task (Phase 7) | F-08 | none | New T063 in Phase 7: grep platform-wide audit-log infrastructure (e.g., `audit_log`, `event_log`, `order_activity`) for usage in existing campaign routes. If audit logging is wired, confirm trackable-link generation events flow through it. If audit logging is missing, open TD-AUDIT-LOG-001 (see Technical Debt). |
| TASK-SEC-009 | Forbid `dangerouslySetInnerHTML` for UTM-derived display strings (T032, T053) | Low | Amend existing | F-09 | none | Code review for T032 (orders-list badge) and T053 (top-products table + KPI cards) confirms: every campaign-name / utm-string / customer-name render uses standard JSX text interpolation, not `dangerouslySetInnerHTML`. No `eval`-style template helpers. Add a short eslint rule if the repo doesn't already block `dangerouslySetInnerHTML` in these components. |

---

## Technical Debt Backlog

| Task ID | Title | Severity | Type | Trigger | Risk if Deferred | Target |
| ------- | ----- | -------- | ---- | ------- | ---------------- | ------ |
| TD-AUDIT-LOG-001 | Build platform-wide audit log for campaign + trackable-link mutations | Low | Technical Debt | Opens only if TASK-SEC-008 finds that no audit log infrastructure exists today | Without an audit trail, disputes over campaign attribution ("who created this trackable link?") cannot be resolved. Insider risk: rogue staff redirecting attribution to a fake campaign for personal kickback. Severity stays Low because: (a) campaign data is non-sensitive; (b) order-level fraud detection lives elsewhere; (c) merchants can detect anomalies via the existing orders feed. | Re-evaluate at the start of the next planning cycle if any merchant raises an attribution-dispute support ticket, OR opportunistically as part of a future "admin actions audit" initiative |

---

## Already Covered Items

None. All 9 findings name gaps not already addressed by an existing task. The closest existing coverage:

- F-01 is *contractually* required (`contracts/merchant-campaign-api.md` Authentication section) but not *task-level* required — that's exactly the gap this follow-up closes.
- F-04 partially overlaps with T005 (sanitizer's 200-char cap on UTM strings), but only for flat UTM fields, not for the full attribution envelope's other fields (referrer, landing_path, gclid, fbclid, session_id) or for envelope-size.

---

## Confirmed Secure Patterns

Carried forward unchanged from the security review:

✅ UTM sanitization on ingest (T005)
✅ Tenant-scoped FK + partial indexes (T010)
✅ Tenant-scoped campaign resolver with isolation test (T011, T014)
✅ Length-capped UTM columns (200 chars) — flat fields only; envelope coverage added by TASK-SEC-004
✅ Per-host cookie scope, `SameSite=Lax`, non-sensitive payload (R-01)
✅ Persistent attribution under denied consent paired with banner copy correction (FR-009 + T041 + TASK-SEC-007)
✅ Server-side QR generation (R-05 + T017)
✅ Dedicated UTM columns on funnel_events (R-03 + T008)

---

## Application Notes (for `/speckit-security-review-apply`)

If you run the apply command:

1. **TASK-SEC-001 through TASK-SEC-007 + TASK-SEC-009**: these are amendments to existing tasks. The apply command should modify the matching task description in `tasks.md` rather than appending new tasks. Keep the original task ID; extend the acceptance criteria with the security requirement.

2. **TASK-SEC-008**: this is a new task. Insert as `T063` in Phase 7 (Polish), between the existing T062 and the end of Phase 7. Bump task count from 62 → 63.

3. **TD-AUDIT-LOG-001**: do not insert into `tasks.md` — track in a separate technical-debt log (whichever convention the team uses; if none, append to `plan.md`'s "Risks / Open Questions" section as a conditional risk). The apply command should ask whether to create or update such a log.

4. **Re-run**: after applying, re-run `/speckit-security-review-tasks` once to verify the findings are fully addressed in the updated `tasks.md`. Expectation: all 9 findings move to "Confirmed Secure Patterns" with task-level evidence.
