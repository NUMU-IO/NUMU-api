# Feature Specification: Risk-Model Completeness

**Feature Branch**: `backend-016-risk-model-completeness`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

The 2026-05-09 audit found that of the **8 risk factors advertised in
marketing**, only 5 were implemented in `score_order`. Of the
remaining three:

  * `payment_method` was a parameter on `score_order` but **never
    referenced in the scoring logic** — captured for show, ignored in
    fact.
  * `time_pattern` was completely absent.
  * `product_risk` was completely absent.

This feature ships the missing three factors so the public messaging
("8-factor risk scoring") matches the actual code.

## Requirements

- **FR-001**: New `_score_payment_method(payment_method)` returns
  high-risk for COD aliases, low-risk for prepaid (paymob, card,
  wallet, instapay, fawry, kashier, fawaterak, stripe), neutral for
  unknown / missing.
- **FR-002**: New `_score_time_pattern(created_at)` returns elevated
  risk when the order's Cairo-time hour is in the `[1, 5)` window,
  baseline otherwise. Naive datetimes are treated as UTC.
- **FR-003**: New `_score_product_risk(product_tags)` returns
  elevated risk when any tag matches the high-risk substring list
  (electronics, phone, laptop, jewelry, watch, luxury, perfume,
  high_value). When no tag data is available, returns
  `score=0.0` with `reason="no_tag_data"` so the factor's
  inapplicability is honest rather than smeared as "neutral."
- **FR-004**: `score_order` MUST emit nine factor records (network +
  the existing five + the three new) with weights summing to exactly
  1.00. New weights: network 0.25, history 0.20, value 0.15,
  cancellation 0.13, payment_method 0.07, address 0.05, phone 0.05,
  time_pattern 0.05, product_risk 0.05.
- **FR-005**: The Celery task `compute_full_risk_score` MUST accept
  `created_at_iso` (string) and `product_tags` (list[str]),
  parse the timestamp once, and forward both to `score_order`.
- **FR-006**: The Shopify `orders/create` webhook handler MUST
  extract `payload.created_at` (or `processed_at` fallback) and
  the union of per-line and order-level `tags` strings, forwarding
  both to the Celery enqueue.

## Out of scope

- Per-store custom factor weights (a future merchant-tunable feature).
- Time-zone configuration per store. Cairo-only is correct for the
  MENA-first launch; expanding to other markets adds a `store.timezone`
  setting in a separate spec.
- A "neutral baseline for `no_tag_data`" — the audit specifically
  called out that smearing missing data with a guess is dishonest.
  We return 0 with a documented reason instead.

## Success Criteria

- **SC-001**: `pytest tests/api/test_risk_factors.py -v` green across
  35 cases (payment_method aliases + prepaid recognition,
  time_pattern Cairo TZ + late-night window, product_risk tag
  matching + normalization, full 9-factor weight invariant, end-to-
  end COD-late-night vs prepaid-daytime score separation).
- **SC-002**: `score_order(...).factors` contains exactly the nine
  named factors and the weights sum to 1.00 within 1e-6.
