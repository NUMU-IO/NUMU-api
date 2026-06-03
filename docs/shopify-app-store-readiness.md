# Shopify App Store — Launch Readiness (NUMU Trust Network app)

> Phase E reference. The Trust Network also ships as a standalone Shopify app
> (`numu-payments-intelligence`, Remix) over this backend. This consolidates the
> **backend-side compliance posture** (verified against `api/v1/routes/shopify/
> webhooks.py`) and the submission checklist. App-side assets (HMAC, listing,
> Protected Customer Data form) live in `numu-payments-intelligence/docs/
> shopify-submission/`.

## 1. Mandatory webhooks ✅

| Topic | Handler | Behaviour |
|---|---|---|
| `app/uninstalled` | `process_webhook` → `install_repo.mark_uninstalled` | deactivate install |
| `app/scopes_update` | `process_webhook` | persist updated scopes |

## 2. GDPR webhooks ✅ (Shopify review hard requirement)

| Topic | Handler | Behaviour |
|---|---|---|
| `shop/redact` | `install_repo.delete_store_data` | delete ALL store data; **decrement the network aggregates** via `network_contribution_log` (no orphaned signal) |
| `customers/redact` | `network_repo.delete_customer_network_data` (+ risk/flows/otp by email) | erase the customer's **cross-merchant contribution** for that store; anonymize zeroed rows (`backend-014` FR-003/004) |
| `customers/data_request` | `network_repo.list_customer_contributions` (+ risk assessments) | DSAR export **includes the network contribution footprint** (`backend-014` FR-005/006) |

The earlier audit's headline GDPR gap — that the network signal (phone-hash
keyed) was *not* erased by the email-keyed redact — is closed: redact now hashes
the payload phone and decrements the moat.

**HMAC verification** happens in the Remix app's webhook route (it holds the
Shopify webhook secret), which then forwards the verified, parsed body to this
backend's internal `/shopify/process`. Review checks the app endpoint; the
backend endpoint is **internal-key gated** — verified: the router carries
`dependencies=[Depends(verify_internal_key)]`, so it isn't independently
callable.

## 3. Protected Customer Data

The app reads customer **phone numbers** — Protected Customer Data, Level 2 — to
HMAC-hash them into the moat. Justification + data-flow are documented in
`numu-payments-intelligence/docs/shopify-submission/{privacy-policy,
data-handling-disclosure,protected-data-checklist}.md`. Privacy story:

- Phone → E.164 → **HMAC-SHA256(salt)** before any storage or lookup; raw phone
  never persisted to the network layer, never returned to the client.
- The cross-merchant table holds **only** the hash + aggregate counters.
- Salt is env-only and rotatable; production refuses to boot without it.

## 4. Performance SLAs (constitution Principle II)

| Budget | Met by |
|---|---|
| webhook ack < 5s (Shopify) | synchronous **fast 2-factor score < 200ms** (`score_order_fast`) |
| full score < 10s | Celery `compute_full_risk_score` (soft limit 15s) |
| pay micro-frontend FCP < 1.5s on 3G | `pay.numu.app` (app-side; verify on submission) |

## 5. Additive mutations (Principle V) ✅

Shopify order mutations are additive: tags prefixed `numu-` and appended
(`numu-hold`, `numu-approved`, `numu-cancelled`), notes prefixed `NUMU: ` —
merchant data is never overwritten (`execute_actions.py`).

## 6. Submission checklist

- [x] Mandatory webhooks (`app/uninstalled`, `app/scopes_update`)
- [x] GDPR webhooks incl. **network erasure + DSAR** (`backend-014`)
- [x] HMAC verification (app-side); `/shopify/process` internal-key gated ✓ (verified)
- [x] Additive mutations
- [x] Deterministic, explainable scoring (no opaque ML — Principle IV)
- [ ] Submit **Protected Customer Data** request in the Partner Dashboard (manual)
- [ ] Verify `pay.numu.app` FCP < 1.5s on throttled 3G
- [ ] Listing assets (copy, screenshots, demo store) — app-side
- [ ] Pass Shopify automated checks + review

## 7. Note

The backend is App-Store-compliant on every item it owns (webhooks, GDPR,
hashing, additive mutations, SLAs). The remaining boxes are the manual Partner
Dashboard workflow and app-side listing assets — neither is a code change.
