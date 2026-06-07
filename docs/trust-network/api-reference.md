# NUMU Trust Network — API Reference

> Due-diligence reference for the trust-network API surface. Base URL:
> `https://numueg.app/api/v1`. Grouped by audience. Schemas live in
> `api/v1/schemas/`; the trust decision behind every COD check is the FSM
> documented in `architecture.md`.

---

## Merchant (dashboard — authenticated, store-scoped)

### `GET /stores/{store_id}/settings/cod-trust`
### `PATCH /stores/{store_id}/settings/cod-trust`
Read / update the COD-trust protection settings (`api/v1/routes/stores/settings.py`).
Key fields (`CodTrustResponse` / `UpdateCodTrustRequest`):

| Field | Type | Meaning |
|---|---|---|
| `enabled` | bool | master switch (default off — opt-in) |
| `threshold` | int 0-100 | network-risk score at/above which the action fires |
| `min_confidence` | `low\|medium\|high` | never act below this confidence (default `medium`) |
| `action` | `block\|warn\|recover` | **the two flows**: reject COD / allow+log / allow + WhatsApp pay-online offer |
| `recovery_promo` | str? | promo line shown in the `recover` WhatsApp offer |
| `auto_rto_disabled`, `auto_rto_days` | bool/int | auto-RTO sweep for forgetful merchants |

### `GET /stores/{store_id}/customers/{customer_id}/trust-stats`
A customer's COD trust card (`stores/customers.py`) — score, tier, percentile
label, and reasoning bullets. Keyed off the customer's canonical E.164 phone.

### `GET /stores/{store_id}/cod-trust/decisions`
The COD-trust **decisions feed** (`stores/cod_trust_decisions.py`) — the
allow/block/recover decisions the system made (rows with
`action_taken_by="cod_trust"`), so the merchant sees the protection working.

---

## Storefront (buyer-facing — public, no auth)

### `POST /storefront/store/{store_id}/cod-eligibility`
Pre-flight COD check so the storefront can gate COD **before** submit
(`storefront/checkout_config.py`). Body: `{ phone, latitude?, longitude?,
accuracy?, source? }`. Response: `{ cod_available: bool, fallback_payment_methods:
[...] }`. Coarse-only (no score/reason leaked); **fails open**.

### `GET /storefront/store/{store_id}/checkout-config`
Dynamic checkout-field config. When `cod_trust.enabled`, marks `phone`
`required` with `required_reason="cod_trust"` so the form explains it up-front.
Also returns `enabled_payment_methods`.

### `POST /storefront/store/{store_id}/checkout` — the COD-trust contract
On a high-risk COD order under `action="block"`, returns **`403`**:
```json
{ "code": "cod_trust_blocked",
  "message_en": "Unable to complete this order with cash on delivery…",
  "fallback_payment_methods": ["paymob_card", "paymob_wallet"] }
```
Missing phone under an enabled cod_trust returns `400 phone_required_for_cod`.
Under `action="recover"` the order is **created as COD** and a WhatsApp
pay-online offer is scheduled (no error).

---

## Internal / due-diligence (internal-key — `verify_internal_key`)

### `GET /risk/moat-metrics`
Platform-wide proof-the-moat-works metrics (PII-free): `coverage`,
`cross_store_catch`, `auto_approve_quality` (RTO delta vs baseline),
`kill_switch_incidents`, `trust_tier_distribution`. See `moat-metrics.md`.

### `POST /risk/narrative`
Generate a PII-tokenized, deterministic-factor risk narrative (Gemini) for the
merchant dashboard / recovery personalization (`risk/narrative.py`). The **only**
LLM call in the system; never scores or decides.

---

## Notes
- **Auth tiers:** merchant routes use the dashboard's bearer/session auth +
  store scoping; storefront routes are public (buyer-facing); `risk/*` is
  internal-key only.
- **The decision is one engine.** Every COD check above — storefront 403,
  pre-flight eligibility, the Shopify webhook path — resolves through the same
  `trust_decision_service.decide()` FSM, so answers are consistent across
  surfaces (`architecture.md` §2-3).
