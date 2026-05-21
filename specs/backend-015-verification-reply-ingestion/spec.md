# Feature Specification: Verification Reply Ingestion

**Feature Branch**: `backend-015-verification-reply-ingestion`
**Created**: 2026-05-09
**Status**: Draft

## Why this exists

The 2026-05-09 audit found the COD verification loop is half-closed.
Send works (real Meta WhatsApp Graph API). But the inbound webhook
([webhooks/whatsapp.py:204](src/api/v1/routes/webhooks/whatsapp.py)) logs replies as conversation history
and ignores them. No "yes/no" parser, no order-status flip, no risk-
assessment update. Merchant never sees who confirmed.

This feature closes the loop: parse the inbound text, find the related
risk assessment via the most-recent outbound nudge → its store →
the latest pending payment_link_session → the shopify_order_id, and
stamp ``action_taken`` so the merchant dashboard reflects the
customer's intent.

## Requirements

- **FR-001**: New `parse_reply(text)` returns `"confirmed"` for a
  single yes-token (Arabic + English variants), `"rejected"` for a
  no-token, and `"not_a_reply"` for anything else (long messages,
  questions, follow-up clarifications).
- **FR-002**: New `is_within_reply_window(sent_at, now=None)`
  enforces a 24h window. Replies older than that flow to the
  conversation log unchanged (a customer responding the next week
  isn't confirming a shipped order).
- **FR-003**: New `apply_reply(session, phone, text)` correlates the
  inbound to a `risk_assessment` via:
   1. Latest outbound `message_log` for `phone` → store_id.
   2. Latest pending `payment_link_session` for that store →
      `shopify_order_id`.
   3. Latest `risk_assessment` for `(store_id, shopify_order_id)`.
- **FR-004**: On `confirmed`, `risk_assessment.action_taken =
  "customer_confirmed"`, `action_taken_by="customer_whatsapp"`, and
  the related session moves to `intent_confirmed`. On `rejected`,
  `action_taken="customer_rejected"` and session moves to
  `customer_rejected`.
- **FR-005**: `_upsert_conversations_from_webhook` (the existing
  inbound handler) calls `apply_reply` for text + button messages
  before falling through to the conversation upsert.

## Out of scope (deferred)

- Manual `POST /shopify/{store_id}/risk/orders/{id}/resend-verification`
  endpoint. Originally Sprint 2 scope, but a clean implementation
  needs `customer_phone` on `risk_assessments` (currently absent) so
  the resend can target the right phone without reading from
  Shopify Admin API. Punted to its own follow-up — backend-015b — so
  this commit ships the load-bearing reply path without the column
  migration overhead.
- `verification_status` field on `RiskOrderResponse`. Today the
  dashboard reads `action_taken`, which now carries the
  `customer_confirmed` / `customer_rejected` values, so the
  surface is already there. A dedicated derived field is cosmetic
  improvement, not a load-bearing gap.

## Success Criteria

- **SC-001**: `pytest tests/api/test_verification_reply.py -v` green
  across yes/no token classification, the 24h window, and
  malformed/empty inputs.
- **SC-002**: An inbound "yes" from a phone with a recent outbound
  nudge updates the matching `risk_assessment.action_taken` to
  `"customer_confirmed"` end-to-end.
