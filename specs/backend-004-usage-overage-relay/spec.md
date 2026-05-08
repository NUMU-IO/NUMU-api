# Feature Specification: Verification Overage Relay

**Feature Branch**: `backend-004-usage-overage-relay`
**Created**: 2026-05-08
**Status**: Draft

## Why this exists

The Shopify-app exposes `POST /api/billing/usage-record` (shipped in
v002) that translates a billing-usage event into a Shopify Billing API
`appUsageRecordCreate` mutation. Today, **nothing in numu-api ever
calls it.** Merchants who exceed their tier's WhatsApp/SMS verification
cap are not billed for overages — the revenue path is broken at the
last mile.

This feature adds the missing caller side. The architecture:

  numu-api whatsapp_nudge_task → records to `message_logs` →
  triggers a usage check (`record_overage_if_needed`) →
  if overage exists → `UsageRelayService.post_usage()` →
  Shopify-app `POST /api/billing/usage-record` →
  Shopify `appUsageRecordCreate` → merchant's invoice line

## User Story 1 — Overage POSTs to the Shopify app (Priority: P1)

As numu-api, when a merchant on the Starter plan has sent their 451st
WhatsApp message this billing cycle, I POST a usage event to the
Shopify-app's `/api/billing/usage-record` with `amount_cents = 5`
(one message × $0.05). The Shopify app translates that into a Shopify
`appUsageRecordCreate` mutation; merchant pays $0.05 on next invoice.

**Independent Test**: with a fake HTTP client, call
`UsageRelayService.post_usage(shop_domain, amount_cents,
description, idempotency_key)` and assert the request body matches the
Shopify-app's expected schema and the X-Internal-Key header is set.

**Acceptance Scenarios**:

1. **Given** valid params, **When** `post_usage` is called, **Then** a
   POST to `<SHOPIFY_APP_URL>/api/billing/usage-record` is sent with
   the correct body shape and `X-Internal-Key` header.
2. **Given** the Shopify-app returns 200 with `{recorded: true,
   capped: false, shopifyUsageRecordId: "..."}`, **When** the relay
   processes the response, **Then** it returns a typed `RelayResult`
   with `recorded=True` + the Shopify usage-record id.
3. **Given** the Shopify-app returns 200 with `{recorded: false,
   capped: true}`, **When** the relay processes the response, **Then**
   it returns `RelayResult(recorded=False, capped=True)` — the cap is
   reached upstream, no error.
4. **Given** the Shopify-app returns 401 (wrong internal key), **When**
   the relay processes, **Then** it raises `RelayConfigError` so the
   ops team gets paged (this is a deployment-config failure, not a
   transient one).
5. **Given** a network-level failure (timeout, connection refused),
   **When** the relay processes, **Then** it raises `RelayUnavailable`
   so the calling Celery task can retry.

## Requirements

- **FR-001**: `UsageRelayService.post_usage(shop_domain, amount_cents,
  description, idempotency_key)` MUST POST to
  `<SHOPIFY_APP_URL>/api/billing/usage-record` with the documented body
  shape: `{shop_domain, amount_cents, description, idempotency_key}`.
- **FR-002**: The `X-Internal-Key` header MUST be set from
  `settings.shopify_internal_key`. Missing key → `RelayConfigError`.
- **FR-003**: The base URL MUST come from `settings.shopify_app_url`
  (new setting; defaults to empty + raises `RelayConfigError` when
  missing).
- **FR-004**: 401 response → `RelayConfigError`. Network errors →
  `RelayUnavailable`. 5xx → `RelayUnavailable`. 200 → typed
  `RelayResult`. 422 → `RelayInvalidPayload` (programmer error).
- **FR-005**: HTTP timeout: 5 seconds (matches the Shopify-app's
  expected response latency for a single GraphQL mutation).

## Success Criteria

- **SC-001**: `pytest tests/api/test_usage_relay.py -v` green.
- **SC-002**: Idempotency: called twice with the same
  `idempotency_key` from numu-api side → the Shopify-app dedupes
  (Numu-side trust is fine; the dedup is upstream).

## Out of scope (follow-ups)

- The trigger that calls `post_usage` from the existing whatsapp tasks.
  That's a one-liner addition in `whatsapp_nudge_task.py` after the
  send succeeds. Tracked as `backend-004b` follow-up.
- The plan-cap-aware overage CALCULATION (count messages this cycle,
  subtract cap, compute overage cents). Tracked as `backend-004c`.
  Today the relay accepts pre-computed `amount_cents` from its
  caller — keeps this commit focused on the HTTP boundary.
