# NUMU Trust Network — Architecture Overview

> Due-diligence reference. The COD Trust Network is a cross-merchant fraud /
> reliability layer for Cash-on-Delivery commerce in MENA — a *"credit score for
> COD"*. A serial RTO (return-to-origin) abuser flagged at Store A is caught at
> Store B even as a first-time buyer. This document maps how it's built. Pairs
> with `moat-metrics.md` (does it work?) and `../security/trust-network-isolation.md`
> (is it safe?).

---

## 1. The moat in one paragraph

Every COD order is scored for risk before fulfilment. The score blends a store's
own history with a **global, phone-keyed reputation graph** built from the COD
outcomes of *every* merchant on the platform. New merchants inherit protection
they never collected themselves; abusers can't escape by hopping stores. The
graph holds only an HMAC-SHA256 phone hash plus aggregate counters — no PII — so
sharing it across merchants is privacy-preserving by construction.

---

## 2. System architecture — one network, two surfaces, one brain

```
 ┌─────────────────────────┐        ┌──────────────────────────┐
 │  Shopify app             │        │  Native NUMU storefront   │
 │  (numu-payments-         │        │  (numu-egyptian-bazaar /  │
 │   intelligence, Remix)   │        │   numu-storefront)        │
 │  orders/create webhook   │        │  POST /checkout           │
 └───────────┬─────────────┘        └────────────┬─────────────┘
             │  risk inputs                       │  risk inputs
             ▼                                    ▼
     ┌───────────────────────────────────────────────────────┐
     │   ONE deterministic scorer  +  ONE decision FSM         │
     │   risk_scoring_engine.score_order  (9 factors, =1.00)   │
     │   customer_trust_formula           (0-100 + tiers)      │
     │   trust_decision_service.decide()  → TrustDecisionState │   ← Principle IV:
     │   (AUTO_APPROVED|CONFIRM_PENDING|HELD|BLOCKED|CANCELLED) │     deterministic,
     └───────────────────────┬───────────────────────────────┘     no ML
                             │ reads / writes (phone_hash)
                  ┌──────────▼───────────┐
                  │  network_reputation   │  ◀── couriers (Bosta/J&T/Mylerz),
                  │  (the global moat)     │      manual marks, OTP, recovery,
                  └────────────────────────┘      nightly reconciliation sweep
                             │
                  ┌──────────▼───────────┐
                  │  LLM (Gemini)         │  explanation ONLY — risk_narrative_service
                  │  PII-tokenized        │  (never scores, never decides)
                  └────────────────────────┘
```

**The architecture's defining choice is a three-layer split** (the answer to
"state machine vs LLM?"):
1. **Scoring = deterministic** — fast, auditable, reproducible, constitution-
   mandated (Principle IV, "Explainable Scoring, no opaque ML in v1").
2. **Decisioning = one explicit finite-state machine** (`trust_decision.py`,
   `decide()`) that both surfaces consume — replacing four formerly-divergent
   decision authorities (a risk ladder, settings thresholds, the automation rule
   engine, and the native guard sequence).
3. **LLM = explanation only** (`risk_narrative_service`, Gemini, PII-tokenized) —
   never on the scoring or decision path.

### Backend layering (`NUMU-api`, clean/hexagonal)
`core/entities` (Order + OrderStatus, RecoveryFlow, **TrustDecision** FSM) →
`application/{services,use_cases}` (scoring engine, trust formula,
cod_trust_service, trust_decision_service, network_reputation_service,
cod_recovery_service, risk_narrative_service) → `infrastructure`
(models, repositories, `messaging/tasks` Celery, `events/handlers` EventBus) →
`api/v1/routes` (storefront checkout, stores, shopify webhooks, risk).

---

## 3. The decision pipeline (per order)

```
order arrives
  → enrich inputs (network lookup by phone_hash, store history, location)
  → score        risk_score 0-100 (ascending-bad)  +  customer_trust 0-100
  → decide()     SCORED → one of:
        AUTO_APPROVED   trusted buyer, gates pass (spec-010 FR-002)
        CONFIRM_PENDING WhatsApp tap-to-confirm
        HELD            merchant review
        BLOCKED         reject COD (native "block" flow)
        CANCELLED       safety-gated auto-cancel (final + grace + 5 manual cancels)
  → act           Shopify: tags/notes via automation engine; Native: allow/block
  → fulfil        SHIPPED → DELIVERED | RTO  (the order lifecycle FSM)
  → feed back     write delivery/rto into network_reputation (the loop closes)
```

**Hybrid compute** (constitution latency budget): a synchronous 2-factor **fast
score** (<200ms) acknowledges the Shopify webhook inside its 5s timeout; the full
9-factor score runs in **Celery** (<10s) and finalises `customer_trust`, the
auto-approve decision, and recovery-flow spawning.

**Canonical axis:** `risk_score` is 0-100 **ascending-bad** everywhere (an
earlier finding was two inverted scales across surfaces; the FSM unified them).

---

## 4. The scoring model (deterministic)

- **9 factors** summing to 1.00 (`risk_scoring_engine.score_order`):
  network_reputation 0.25, customer_history 0.20, order_value 0.15,
  cancellation_rate 0.13, payment_method 0.07, address 0.05, phone 0.05,
  time_pattern 0.05, product_risk 0.05. Each returns `{score, weight, reason}`.
- **Network reputation** (`compute_network_score`): RTO-rate × 100 + refund
  penalty − delivery bonus, **confidence-dampened** toward a 55 baseline when
  data is sparse (low/medium/high confidence by order count).
- **Customer trust** (`customer_trust_formula`): a signed formula
  (deliveries/prepaid/WhatsApp-responsiveness/network-positives **minus**
  network-RTOs/local-refusals), 0-100, tiers none→gold; snapshot-tested.

---

## 5. The trust loop (how outcomes become signal)

The moat is only as good as the outcomes it ingests. Delivery/RTO signals reach
`network_reputation` from **four** paths, made resilient:
- **Courier webhooks** — Bosta, J&T, Mylerz normalise to delivered/returned and
  write `delivery`/`rto` events (RTO fires for *any* payment method).
- **Manual marks** — `UpdateOrderStatusUseCase` for merchants without a courier
  integration.
- **OTP / recovery** — positive signals on WhatsApp OTP verify + recovery success.
- **Nightly reconciliation sweep** — backfills any event a dropped webhook or
  non-wired courier missed, so a single lost message can't permanently desync the
  graph. All writes are idempotent (an `order.metadata` flag **and** a DB-level
  `dedup_key` on the contribution log).

A **kill-switch** (daily) disables a store's trust auto-approve if its
auto-approved cohort's RTO rate exceeds 5% over ≥20 orders, and emails the
merchant.

---

## 6. The two COD-trust flows (merchant choice)

`cod_trust.action` per store:
- **`block`** — high-risk COD is rejected; the storefront can pre-flight via
  `POST .../cod-eligibility` and offer prepaid fallbacks before submit.
- **`recover`** — the COD order is allowed, then a **WhatsApp payment-link offer**
  (with a merchant promo) is scheduled to convert it to prepaid
  (`cod_recovery_service`); if unpaid it proceeds as COD. (`warn` = allow + log.)

---

## 7. Data model (key tables)

| Table | Scope | Holds |
|---|---|---|
| `network_reputation` | **global** (phone_hash, no tenant) | the moat: aggregate COD counters; HMAC hash + counts only |
| `network_contribution_log` | public, store_id | append-only ledger (GDPR rollback + dedup_key idempotency) |
| `risk_assessments` | public, store_id-scoped | per-order score, factors, customer_trust, action_taken(_by) |
| `shopify_app_settings` | per store | thresholds, toggles, kill-switch state |
| `recovery_flow` / `payment_link_session` | per store | COD→prepaid conversion state machine |
| `orders` | tenant-scoped (RLS) | order lifecycle + WhatsApp confirmation state |

---

## 8. Security & privacy (summary — full detail in `../security/trust-network-isolation.md`)

HMAC-SHA256 phone hashing with an env-only, rotatable `PLATFORM_SECRET_SALT`
(production refuses to boot without it); tenant data isolated by Postgres RLS +
explicit filters; the global `network_reputation` table holds no PII and no
store attribution; GDPR `customers/redact` / `shop/redact` decrement the moat via
the contribution log.

---

## 9. Key design decisions (and why)

- **Deterministic scoring, not ML (v1)** — auditability + reproducibility +
  zero per-order cost + no PII to a model + constitution Principle IV. ML may
  later *calibrate weights*, gated behind a constitution amendment.
- **One FSM, strangler-migrated** — the native surface was cut over only after an
  85-case equivalence proof; the Shopify surface runs in shadow until its prod
  agreement data justifies cutover. No big-bang on a live decision path.
- **Hybrid sync/async** — instant webhook ack + deep async scoring.
- **Global moat table, per-store everything else** — the network effect needs a
  shared graph; isolation needs per-store scoping. Both, deliberately.
