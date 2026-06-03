# NUMU Trust Network — Data Room

The COD Trust Network is a cross-merchant fraud / reliability layer for
Cash-on-Delivery commerce in MENA — a *"credit score for COD."* A serial RTO
abuser flagged at one store is caught at the next, even as a first-time buyer.
This folder is the due-diligence data room.

## Read in this order

| Doc | Answers |
|---|---|
| [`architecture.md`](architecture.md) | **How is it built?** One network, two surfaces, one decision FSM; deterministic scoring + LLM-for-explanation-only; the trust loop. |
| [`audit-remediation.md`](audit-remediation.md) | **Is it sound?** The acquisition-readiness audit's findings (P0–P3) and how each was closed, with commit + test evidence; before→after posture. |
| [`moat-metrics.md`](moat-metrics.md) | **Does the moat work?** Coverage, cross-store catch rate, and the auto-approve RTO-vs-baseline delta — live at `GET /api/v1/risk/moat-metrics`. |
| [`api-reference.md`](api-reference.md) | **What's the surface?** Merchant, storefront, and internal endpoints; the COD-trust contract; auth tiers. |
| [`../security/trust-network-isolation.md`](../security/trust-network-isolation.md) | **Is it safe?** HMAC hashing, tenant isolation, the global-table rationale (no PII), GDPR erasure. |
| [`../whatsapp-templates/cod-recovery-offer-spec.md`](../whatsapp-templates/cod-recovery-offer-spec.md) | **The recover flow** — WhatsApp payment-link template + `/pay` page provisioning spec. |

## The headline numbers (what "working" looks like)
Over time, a working moat shows **coverage ↑**, **cross-store catch > 0**, and a
**negative auto-approve RTO delta** (the network's cleared orders return *less*
than COD overall). Those three together = the moat catches abusers across stores
and safely fast-tracks good buyers.

## Implementation
Branch `feat/trust-network-acquisition-readiness` (`NUMU-api`). The product also
ships as a standalone Shopify app (`numu-payments-intelligence`) over the same
backend. Spec-kit history under `NUMU-api/specs/backend-*` and
`numu-payments-intelligence/specs/`.
