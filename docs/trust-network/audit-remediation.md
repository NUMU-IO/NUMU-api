# NUMU Trust Network — Audit & Remediation Report

> Due-diligence capstone. An acquisition-readiness audit of the COD Trust
> Network found a product with impressive surface area but several **dormant,
> divergent, or under-counting load-bearing pieces** — the profile that fails
> technical DD. This report records what was found and how each item was closed,
> with commit + test evidence. Branch: `feat/trust-network-acquisition-readiness`.

## Posture: before → after

| | Before audit | After remediation |
|---|---|---|
| Trust score | inflated (a signal counted 7×) + partly fabricated inputs | each signal counted once; real inputs |
| Decision engine | **two divergent brains** on one table (Shopify ladder vs native guards) | **one FSM**; native surface cut over (proven equivalent) |
| Trust auto-approve | **dormant** — called only from tests | **live**, gated, recorded; kill-switch now has a producer |
| Negative (RTO) signal | leaked: prepaid/manual/dropped + J&T/Mylerz mislabel | captured on every path + a self-healing reconciliation sweep |
| Audit trail | path-dependent (legacy mutators skipped logging) | every transition logged via the guarded path |
| Buyer experience | hard 403 wall | graceful pre-flight + two merchant-chosen flows (block / recover) |
| Provability | none | `moat-metrics` endpoint proves catch-rate + RTO delta |

## Findings & remediation

### P0 — due-diligence blockers
| # | Finding | Remediation | Evidence |
|---|---|---|---|
| **P0-1** | Trust-score inflation: `net_pos` fed to two weighted inputs (×7); `prepaid_orders`/`whatsapp_response_rate` hardcoded; fragile `dir()` hack | Count each signal once; real prepaid query; WhatsApp rate from order-confirmation history | `a51e928f`, `e6f7dac6` · +tests |
| **P0-2** | Headline trust auto-approve **dormant** (only called in tests) | Wired through the final-score task with real `manual_approve_count` (Shopify-app `approve` actions); records `action_taken_by="system_trust_auto"` — closing the kill-switch producer gap (R6) | `84b7f600` |
| **P0-3** | **Two divergent decision engines** on one table → same buyer, two numbers | Canonical `TrustDecision` FSM + `decide()`; native surface **cut over** after an 85-case equivalence proof; Shopify surface shadowed | `a59de2be`→`63989901`, `c44642cd` |
| **P0-4** | Negative signal under-counts: prepaid/manual/dropped RTOs never recorded; J&T/Mylerz recorded RTO as CANCELLED via an illegal table-bypass | RTO fires for any payment method on every path; couriers corrected to `return_to_origin`; nightly reconciliation backfills misses | `a51e928f`, `1346b6ef` |
| **P0-5** | Audit trail path-dependent: `Order.confirm/ship/deliver/cancel/refund` skipped `status_history` | All routed through the guarded `transition_to` | `a51e928f` |

### P1 — reliability
| # | Finding | Remediation | Evidence |
|---|---|---|---|
| **P1-1** | No reconciliation — one dropped webhook permanently desyncs the moat | Nightly courier-agnostic sweep replays missed events | `1346b6ef` |
| **P1-2** | Network writes not DB-idempotent (concurrent writers could double-count) | `dedup_key` migration + `ON CONFLICT DO NOTHING`; courier/manual/reconciliation share one key | `ca155d34` |
| **P1-3** | Kill-switch silent (DB-only; merchant unaware) | Event + bilingual merchant email on self-disable | `5d1f6f70` |
| **P1-4** | Dead formula input (`whatsapp_response_rate` always 0) | Fed from WhatsApp **order-confirmation** responsiveness | `e6f7dac6` |
| **P1-5** | `subscription_active=True` hardcoded | Real, fail-closed subscription lookup | `a51e928f` |
| **P1-6** | RLS / isolation unverified | Documented model + structural test (`trust-network-isolation.md`) | `49d46c08` |

### P2/P3 — differentiator & coherence
| # | Finding | Remediation | Evidence |
|---|---|---|---|
| **P2-1** | Buyer UX a skeleton (hard 403) | Pre-flight `cod-eligibility` endpoint + two flows (block / **recover** via WhatsApp payment-link offer) | `6effbe6d`, `6d8d4c39`, `9afcdc95`, `add34209` |
| **P3-1** | Inverted scales (risk high-bad vs trust high-good) across surfaces | One canonical `risk_score` axis in the FSM | `a59de2be` |
| **P3-2** | Stale in-code docs (old 5-factor docstring) | Corrected to the real 2-fast / 9-full model | `a51e928f` |

## What remains (deliberately gated, not skipped)
- **Shopify `_suggested_action` display-strangle** — gated on the production
  shadow-agreement data the Shopify shadow now produces (the native cutover only
  happened after equivalence was *proven*; the same bar applies here).
- **Recover-flow ops** — Meta-approve `cod_recovery_offer_v1` + ship the
  storefront `/pay` page (fully specced in `../whatsapp-templates/cod-recovery-offer-spec.md`).
- **Buyer-facing frontend** + **new couriers** — need a running storefront /
  courier specs to verify live (backend is ready).
- **Constitution drift (R7)** — two constitution copies exist; declare v1.2.0
  authoritative before launch.

## Verification
~18 commits, each unit-verified and green; ~150 net-new tests across scoring,
the FSM (incl. the 85-case equivalence + fail-open matrix), reconciliation,
idempotency, the recover flow, isolation, and the moat metrics. Pre-existing
`test_invoice` (tax rounding) and `cart._carts` (import) failures are unrelated
and untouched.
