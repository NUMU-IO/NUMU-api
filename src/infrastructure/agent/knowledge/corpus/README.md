# NUMU Agent — authored knowledge corpus (Layer A)

This is the in-repo **source of truth** for the shared NUMU knowledge base (spec 002). It is reviewed
via PR; going *live* is a data operation (`POST /api/v1/agent/knowledge/refresh {source_kind:"authored"}`)
and needs **no code deploy**. Layer A contains **no merchant data** and is shared across all tenants.

## Layout

```
corpus/
├── areas.json            # the KnowledgeArea taxonomy (coverage backbone) — every doc's `area` must be here
├── howto/<area>/<slug>.<locale>.md   # merchant how-to articles (one behaviour each)
└── playbooks/<slug>.<locale>.md      # growth playbooks (signal → feature → rationale → steps)
```

`<locale>` is `en` or `ar` (Egyptian Arabic). Author AR counterparts with the **same `source`** so
both are retrievable per query language.

## How-to frontmatter

```markdown
---
source: numu-corpus/payments/paymob   # STABLE idempotency key — never change it on edits
title: Set up Paymob payments
area: payments                        # must exist in areas.json
locale: en
section: Payments
status: published                     # draft | published | retired
---

Body in markdown. The loader chunks on H2/H3 headings.
## Steps
1. …
```

## Growth-playbook frontmatter (adds the signal→feature contract)

```markdown
---
source: numu-corpus/playbooks/abandoned-cart-recovery
title: Recover abandoned carts to grow sales
area: growth
source_kind: playbook
locale: en
status: published
signal: has_abandoned_carts           # the store condition that triggers the recommendation
feature: abandoned-cart-recovery      # the NUMU feature to enable
detected_by: orders.abandoned_count   # the LIVE tool/metric that detects the signal (never embedded)
howto: numu-corpus/marketing/abandoned-cart   # link to the enablement how-to
---
```

## Rules (enforced by the loader / tests)

- Every doc declares an `area` present in `areas.json`.
- Every area has ≥1 `published` article in ≥1 locale (SC-002).
- Every playbook resolves its `howto` to a real how-to `source` and `detected_by` to a real tool/metric.
- Every doc carries `source`, `title`, `area`, `locale`, `status` (provenance complete — SC-008).
- Embedded instructions in content are **data, never instructions** (prompt-injection guardrail).

See `specs/002-numu-knowledge-base/contracts/corpus-format.md` for the full contract.
