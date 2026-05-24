# Feature Specification: WhatsApp Integration — Phase 1: Backend Foundation

**Feature Branch**: `backend-030-whatsapp-foundation`
**Created**: 2026-05-24
**Status**: Draft
**Input**: User description: "WhatsApp Integration — Phase 1: Backend Foundation. Close the backend gaps that block the merchant-hub UI (Phases 2–4): wire OrderCreatedEvent/PaymentReceivedEvent to WhatsApp confirmations; add customer opt-in tracking with inbound STOP/UNSUBSCRIBE/إلغاء handling; add scheduled-follow-up table + worker; finish per-store credential mode (BYO merchant Meta WABA vs NUMU platform-managed); add template submission to Meta; add retry + dead-letter for failed sends; add a central pre-send guard covering opt-in, 24h window, merchant notification settings, and customer channel prefs. Meta Cloud API only — no Twilio/360dialog/WATI."

---

## Clarifications

### Session 2026-05-24

- Q: For the pre-send guard, do utility templates (order updates) require active opt-in like marketing templates, or do they only need to respect explicit opt-out? → A: Two-tier guard keyed off template category — utility templates (order_confirmation, payment_received, shipping, delivery, abandoned_cart) bypass the opt-in requirement but respect any explicit opt-out; marketing templates require active opt-in AND respect opt-out.
- Q: When a merchant connects BYO Meta WABA, should the per-message-type notification toggles default ENABLED or DISABLED? → A: Platform-managed stores default all toggles ENABLED. BYO stores default all toggles DISABLED; the merchant must explicitly enable each toggle after confirming their templates are approved under their WABA.
- Q: For Meta template approval status updates after submission, polling-only or also a webhook subscription? → A: Webhook + polling fallback. Subscribe to Meta's `message_template_status_update` field for near-real-time updates; keep the polling sync action as backfill and for BYO stores whose Meta app webhook NUMU does not own.
- Q: How deeply should BYO credentials be validated at submit time? → A: Three-step validation, no actual send. (1) Read the phone number's metadata. (2) Read WABA-level info to confirm the token has `whatsapp_business_management` and `whatsapp_business_messaging` scopes. (3) Read the WABA's template list to confirm cross-ID consistency. All three must succeed before the credentials are persisted.
- Q: How long should dead-letter entries be retained before automated purge? → A: 90 days, automated purge thereafter. `message_log` remains the long-term audit-of-record; the dead-letter store is a replay surface, not an archive.

---

## Overview

NUMU already has most of the WhatsApp backend (Meta Cloud client, conversations/templates/campaigns tables, abandoned-cart task, shipped/delivered handlers, per-store credential resolver). This phase closes the backend gaps that block the merchant-hub UI work in Phases 2–4: events that should send messages but don't, missing consent tracking, no scheduled follow-up mechanism, no template submission flow, brittle retry behaviour, no central guard against unwanted sends, and an incomplete BYO credentials path. Two operating modes are supported: **NUMU Platform-Managed** (store uses NUMU's platform WhatsApp account) and **BYO** (merchant connects their own Meta WhatsApp Business Account). Both use the same Meta Cloud API; only the credentials differ.

---

## User Scenarios & Testing *(mandatory)*

### User Story 1 — Order confirmation arrives on WhatsApp automatically (Priority: P1)

A customer places an order on a NUMU storefront. Within 30 seconds the customer receives a WhatsApp message confirming the order — including order number, total, and a link to track the order — provided they consented to WhatsApp messaging at checkout and the store has WhatsApp notifications enabled. When they later pay for the order, they receive a separate payment-received confirmation.

**Why this priority**: This is the single largest user-visible gap from the audit. Order confirmation is the most-expected commerce notification; without it the rest of the WhatsApp integration is hard to demo or justify to a merchant.

**Independent Test**: Place an order on a storefront where the customer has opted in and the store has WhatsApp enabled; verify the confirmation message arrives within 30 seconds. Pay for the order; verify the payment-received message arrives within 30 seconds. Repeat with opt-out → verify nothing is sent. Repeat with notifications disabled at the store level → verify nothing is sent.

**Acceptance Scenarios**:

1. **Given** a customer (with or without an opt-in row, but no explicit opt-out) and a store with order-confirmation notification enabled, **When** an order is created, **Then** an order-confirmation WhatsApp message (utility-category template) is sent to the customer's phone within 30 seconds containing the order number, total amount, and tracking link.
2. **Given** a customer without an explicit opt-out, **When** payment for an existing order is confirmed, **Then** a payment-received WhatsApp message is sent within 30 seconds.
3. **Given** a customer who has an explicit opt-out for the store, **When** an order is created for them, **Then** no WhatsApp message is sent and the skip is logged with reason "opt_out".
4. **Given** a store with WhatsApp order-confirmation notifications disabled, **When** an order is created, **Then** no WhatsApp message is sent and the skip is logged with reason "merchant_setting_off".
5. **Given** the same order, **When** the order is created and then immediately retried (duplicate event), **Then** only one WhatsApp message is sent (idempotent per order).

---

### User Story 2 — Customers can stop messages by replying STOP (Priority: P1)

A customer who is receiving too many WhatsApp messages from a NUMU store replies "STOP" (or "UNSUBSCRIBE", or "إلغاء" in Arabic). Within seconds their opt-in is revoked. No further marketing or transactional WhatsApp messages are sent to them by that store, including any in-flight campaigns and scheduled follow-ups. The merchant can see in their dashboard (in Phase 2) that the customer opted out and when.

**Why this priority**: Compliance requirement. Sending after an explicit opt-out risks Meta penalising the WABA and erodes consumer trust. Must ship before any marketing campaign goes live in Phase 3.

**Independent Test**: From a test phone, send "STOP" to a store's WhatsApp number; verify the opt-out is recorded with timestamp and source = "inbound_stop_keyword"; trigger an order-confirmation event for that phone and verify no message is sent; check that any scheduled follow-ups targeting that phone are cancelled or skipped at send time. Repeat with "UNSUBSCRIBE" and "إلغاء".

**Acceptance Scenarios**:

1. **Given** a customer with active opt-in, **When** they send any of {"STOP", "stop", "UNSUBSCRIBE", "unsubscribe", "إلغاء", "الغاء"} as the first word of an inbound message, **Then** their opt-in is marked revoked with the current timestamp, the opt-out source recorded, and a confirmation reply is sent acknowledging the opt-out.
2. **Given** a customer whose opt-in was just revoked, **When** any send_* method (text, template, media, interactive, campaign send, scheduled send) is invoked for that phone, **Then** the send is skipped and the skip reason is "opt_out".
3. **Given** a customer who previously opted out, **When** they place a new order with the consent checkbox ticked at checkout, **Then** a new opt-in row is created with source = "checkout" and subsequent sends resume.
4. **Given** an inbound message that starts with text other than a stop keyword, **When** the webhook processes it, **Then** opt-in status is unchanged.

---

### User Story 3 — Send a follow-up message N days after a trigger (Priority: P2)

A merchant (or the system) schedules a WhatsApp message to be sent at a specific future time — for example, "send a review-request 3 days after delivery" or "send a win-back to customers idle for 30 days". The scheduled send is queued, fires within ±2 minutes of the target time, respects opt-in and merchant settings at send-time (not at schedule-time), and is cancellable. If the related order is cancelled or refunded before the send-time, the follow-up is automatically cancelled.

**Why this priority**: Enables the highest-ROI WhatsApp use cases (review requests, win-backs, post-purchase nudges) but is not a blocker for basic order notifications. Required for Phase 3 campaign features and for the abandoned-cart task to mature beyond its current single-shot behaviour.

**Independent Test**: Schedule a follow-up send for 5 minutes in the future; verify it fires within ±2 minutes of the target. Schedule one, then cancel it; verify it does not fire. Schedule one for an order, then cancel the order; verify the follow-up is auto-cancelled. Schedule one for a customer who later opts out; verify the send is skipped at send-time, not silently sent.

**Acceptance Scenarios**:

1. **Given** a scheduled follow-up with `scheduled_for` 5 minutes in the future, **When** the scheduler runs, **Then** the message is sent within ±2 minutes of `scheduled_for` and the row status moves to "sent".
2. **Given** a pending scheduled follow-up tied to an order, **When** that order is cancelled or refunded, **Then** the follow-up is auto-marked "cancelled" before it would have fired.
3. **Given** a pending scheduled follow-up, **When** the merchant explicitly cancels it, **Then** the row status moves to "cancelled" and the message does not send.
4. **Given** a pending scheduled follow-up for a customer who opts out before the send-time, **When** the scheduler tries to fire it, **Then** the send is skipped with reason "opt_out" and the row status moves to "skipped" (not "failed").
5. **Given** a scheduled follow-up that fails on send (Meta API error), **When** retries are exhausted, **Then** the row moves to "failed" and a dead-letter entry is created.

---

### User Story 4 — Merchant connects their own Meta WhatsApp Business Account (BYO) (Priority: P2)

A merchant who already has a Meta WhatsApp Business Account wants to send WhatsApp messages from their own verified number (rather than NUMU's platform-managed number). They submit their access token, phone number ID, WABA ID, and app secret through the merchant dashboard (UI in Phase 2 — backend endpoint here). The credentials are validated against Meta, stored encrypted, and from that moment all sends for that store route through their account. Switching back to platform-managed is a one-click action. Both modes are first-class.

**Why this priority**: BYO is core to the product positioning ("their own number, their own brand, their own conversation history") but is not blocking order confirmations from working today on platform-managed. The backend resolver already supports the swap; what's missing is a clean management endpoint, validation, and the disconnect-back-to-platform path.

**Independent Test**: Submit valid BYO credentials via API for a store; verify they pass validation, are stored encrypted, and subsequent sends route through the merchant's number. Submit invalid credentials; verify validation rejects them and returns a clear error. Disconnect; verify sends fall back to platform-managed. Send a message in each mode and confirm via the Meta dashboard or message header which account it came from.

**Acceptance Scenarios**:

1. **Given** a store currently on platform-managed mode, **When** a merchant submits valid BYO credentials, **Then** the three validation reads (phone metadata, WABA info + scopes, WABA template list) all succeed against Meta, the credentials are stored encrypted, the store mode becomes "byo", per-message-type toggles are reset to DISABLED (per FR-019a), and subsequent sends route through the merchant's number.
2. **Given** invalid BYO credentials (wrong token, missing `whatsapp_business_messaging` scope, mismatched phone_number_id/waba_id), **When** the merchant submits them, **Then** the failing validation step is identified in the error response, the underlying Meta error is surfaced, and no row is written.
3. **Given** a store in BYO mode, **When** the merchant disconnects, **Then** the per-store credentials are removed (or marked inactive), the mode reverts to platform-managed, and the next send uses platform credentials.
4. **Given** a store in BYO mode whose access token has expired, **When** a send is attempted, **Then** the send fails with a specific error, the store's WhatsApp status reflects the credential problem, and an admin notification is queued (the send is NOT silently routed through platform credentials).

---

### User Story 5 — Merchant submits a new WhatsApp template for Meta approval (Priority: P2)

A merchant wants to create a new message template (for example, a custom shipping notification with their branding) and have it approved by Meta. They submit the template through the merchant dashboard (UI in Phase 2 — backend endpoint here). The system submits it to Meta, records the local template row with status PENDING, and updates the status when Meta later approves or rejects it (via sync or webhook). Once APPROVED, the template can be used for sends.

**Why this priority**: Without this, merchants can only use the small set of seeded templates and whatever exists in Meta's UI; they can't add new branded variants from inside NUMU. This blocks Phase 3 campaign work (campaigns need approved templates).

**Independent Test**: Submit a valid template via API; verify it is POSTed to Meta, a local row is written with status PENDING, and the meta_template_id is recorded. Submit a malformed template; verify Meta's validation error is surfaced and no local row is written. Run a template sync; verify status updates propagate (PENDING → APPROVED or REJECTED with rejection_reason).

**Acceptance Scenarios**:

1. **Given** valid template content (name, language, category, body, optional header/footer/buttons), **When** submitted, **Then** the system POSTs to Meta's template creation endpoint, persists a local row with status PENDING and the returned meta_template_id, and returns the local row to the caller.
2. **Given** template content that Meta rejects synchronously (e.g., name collision, invalid placeholders, disallowed content), **When** submitted, **Then** the local row is NOT created and the Meta error message is returned to the caller.
3. **Given** a previously submitted PENDING template, **When** Meta pushes a status update via webhook OR the polling sync runs (whichever fires first), **Then** the local status is updated to APPROVED / REJECTED / FLAGGED / PAUSED / DISABLED and any rejection_reason is recorded within 15 minutes of the Meta-side change.
4. **Given** a REJECTED template, **When** a send tries to use it, **Then** the send is refused with a clear error and is not retried.

---

### User Story 6 — Failed sends are retried with backoff and end up reviewable, not lost (Priority: P3)

When a WhatsApp send fails for a transient reason (rate limit, temporary Meta outage, network blip), it is retried with exponential backoff. When all retries are exhausted, the failed send is recorded in a dead-letter store with enough context (recipient, template, parameters, error history) for an operator to replay it later. No failure silently disappears.

**Why this priority**: Improves reliability and observability but isn't user-visible until something goes wrong. Worth shipping in Phase 1 because the campaign work in Phase 3 will produce hundreds-to-thousands of sends per merchant and one-shot retries will lose enough of them to matter.

**Independent Test**: Force a transient Meta API error in a test environment; verify the send retries with exponential backoff and eventually succeeds. Force a persistent error; verify retries exhaust, a dead-letter entry is created with full context, and the failure is logged with the final error code.

**Acceptance Scenarios**:

1. **Given** a send that fails with a retriable error (HTTP 429, 5xx, network timeout), **When** the worker handles it, **Then** it is retried with exponential backoff (at least 3 attempts over at least 5 minutes total).
2. **Given** retries exhaust, **When** the final attempt fails, **Then** a dead-letter entry is created capturing recipient phone, template/content, parameters, full error history, and originating context (order_id, campaign_id, or scheduled_send_id).
3. **Given** a send that fails with a non-retriable error (HTTP 400 — bad recipient, opt-out, invalid template), **When** the worker handles it, **Then** it is NOT retried; it is moved directly to dead-letter with the error classification recorded.
4. **Given** a dead-letter entry, **When** an operator triggers a replay, **Then** the send is re-attempted using the original context (no fresh retries spawn a duplicate dead-letter row on success).

---

### Edge Cases

- **Same customer, multiple stores**: a customer may have opted in for store A and out of store B. Opt-in is per-(store, phone), not global. STOP from a customer affects only the store they sent it to.
- **Phone format mismatch**: customer types `01001234567` at checkout but Meta delivers webhook events with `+201001234567`. All opt-in lookups must canonicalize to E.164 before comparison.
- **Re-opt-in after opt-out**: a customer who opted out can opt back in (e.g., re-ticks consent at checkout). The new opt-in supersedes the prior opt-out; history is preserved.
- **24h messaging window expiry mid-send**: a non-template send is queued while the window is open but processed after it closes. Send must be refused at send-time, not at queue-time.
- **Scheduled follow-up tied to a deleted entity** (customer deleted, store deleted): the scheduled send must be auto-cancelled when its required relation disappears.
- **BYO credentials revoked at Meta** (merchant disconnected from Meta side): the next send fails with a credential error; system surfaces the disconnection rather than silently falling back to platform creds.
- **Duplicate OrderCreatedEvent** (event replayed by the message bus): idempotency key on the send prevents duplicate confirmations to the customer.
- **Customer with no phone number**: the order-confirmation handler skips silently with a logged reason "no_phone" — does not error the order pipeline.
- **Templates whose parameters don't match what the handler tries to fill**: handler refuses the send with a clear error rather than silently sending with empty placeholders.
- **Inbound STOP keyword inside a longer message** (e.g., "please STOP sending"): treated as opt-out only if it is the first word of the message; otherwise routed to the conversations inbox normally.
- **Dead-letter replay on an already-succeeded send**: replay endpoint guards against double-send by checking the original send's final status before replaying.

---

## Requirements *(mandatory)*

### Functional Requirements

**Order lifecycle event wiring**

- **FR-001**: System MUST listen for the order-created event and send a WhatsApp order-confirmation message to the customer's phone when all pre-send guards pass.
- **FR-002**: System MUST listen for the payment-received event and send a WhatsApp payment-received message when all pre-send guards pass.
- **FR-003**: Order-confirmation messages MUST include order number, total amount with currency, and a tracking link.
- **FR-004**: Order-confirmation messages MUST be able to attach the order invoice as a PDF document.
- **FR-005**: System MUST NOT send duplicate order-confirmation or payment-received messages if the underlying event is delivered more than once (idempotency keyed on order id + event type).

**Customer consent & opt-in**

- **FR-006**: System MUST track WhatsApp opt-in per (store, phone) with: source, opted_in_at timestamp, opted_out_at timestamp (nullable), opt_out_reason (nullable), and a link to the customer record when available.
- **FR-007**: Storefront checkout MUST be able to create an opt-in row when the customer ticks the WhatsApp consent checkbox; if the customer is later identified to an existing customer record, the opt-in row's customer link MUST be updated.
- **FR-007a**: The storefront-facing opt-in endpoint MUST require a valid checkout-session token. The handler MUST: (a) verify the session token resolves to an active cart on the addressed store, (b) canonicalize the phone supplied in the request body and the phone attached to the cart to E.164, and (c) reject the request (HTTP 403, code `phone_mismatch_with_cart`) if the two phones do not match. This closes the abuse vector where anyone with a store slug could write opt-in rows for arbitrary phones. The merchant-facing opt-in creation endpoint (auth-required) is unaffected by this rule.
- **FR-008**: All phones written to or queried against the opt-in store MUST be canonicalized to E.164 first.
- **FR-009**: Inbound webhook MUST detect when the first word of an inbound message is one of {STOP, UNSUBSCRIBE, إلغاء, الغاء} (case-insensitive) and flip the matching opt-in row to opted-out with opt_out_reason = "inbound_stop_keyword".
- **FR-010**: On detection of a STOP keyword, system MUST send an acknowledgement reply confirming the opt-out.
- **FR-011**: Every WhatsApp send-* method MUST consult the opt-in store before sending. The opt-in requirement is two-tier, keyed off the template's Meta category:
  - **Utility-category** templates (e.g., order_confirmation, payment_received, order_shipped, order_delivered, abandoned_cart) MAY send to any phone that does NOT have an explicit opt-out row for the (store, phone); active opt-in is NOT required.
  - **Marketing-category** templates (including all campaign sends and any custom marketing template) MUST be skipped unless an active opt-in row exists for the (store, phone) AND there is no explicit opt-out.
  - **Authentication-category** templates follow the same rule as utility (no opt-in required; opt-out respected).
  - **Free-form / non-template messages** (text, media, interactive, sent inside the 24h customer-service window) follow the utility rule (no opt-in required; opt-out respected).
  - Skip reasons are logged as `opt_out` (explicit opt-out row exists) or `no_opt_in` (marketing send and no active opt-in row).
- **FR-012**: Re-opt-in after a prior opt-out MUST create a new opt-in row rather than mutating the prior row (preserving history).

**Scheduled follow-ups**

- **FR-013**: System MUST persist scheduled WhatsApp sends with: store, customer/phone, template, template parameters, scheduled_for timestamp, status, related order (nullable), and audit timestamps.
- **FR-014**: A background worker MUST scan for due scheduled sends at least once per minute and dispatch them.
- **FR-015**: Each scheduled send MUST fire within ±2 minutes of its `scheduled_for` time under normal load.
- **FR-016**: Cancelling or refunding the related order MUST auto-cancel any pending scheduled sends tied to that order.
- **FR-017**: Scheduled sends MUST re-evaluate all send-time guards at dispatch (not at schedule-time); guard failures move the status to "skipped" with the reason recorded, never "failed".
- **FR-018**: System MUST allow callers to cancel a pending scheduled send by id.

**Per-store credentials (BYO ↔ Platform-Managed)**

- **FR-019**: Each store MUST operate in exactly one of two modes at any time: "platform_managed" (uses NUMU's platform credentials) or "byo" (uses merchant-supplied Meta credentials). On store creation, default mode is "platform_managed".
- **FR-019a**: Per-message-type notification toggles MUST default ENABLED for stores in "platform_managed" mode and DISABLED for stores in "byo" mode. When a store transitions from "platform_managed" to "byo", all toggles MUST be reset to DISABLED so the merchant explicitly re-enables each one after confirming their templates are approved under their own WABA. When transitioning from "byo" back to "platform_managed", the prior platform-managed toggle state is restored (or, if none exists, defaults to all-ENABLED).
- **FR-020**: System MUST provide an endpoint to submit BYO credentials (access token, phone number ID, WABA ID, app secret).
- **FR-021**: Submitted BYO credentials MUST pass three Meta read calls (no message send) before being persisted:
  1. **Phone metadata read** — confirms `access_token` + `phone_number_id` resolve.
  2. **WABA info read** — confirms the token carries both `whatsapp_business_management` and `whatsapp_business_messaging` scopes against the supplied `waba_id`.
  3. **WABA template list read** — confirms the `phone_number_id` belongs to the `waba_id` (cross-ID consistency) and that the token can enumerate templates (proves messaging-management surface is reachable).

  Any failure MUST surface a specific error identifying which of the three checks failed and what Meta returned, and MUST NOT persist a row.
- **FR-022**: Stored BYO credentials MUST be encrypted at rest using the existing encryption mechanism for service credentials.
- **FR-023**: System MUST provide an endpoint to disconnect BYO and revert the store to platform-managed.
- **FR-024**: System MUST provide a read-only endpoint that returns the store's current WhatsApp mode, connection status, phone number display name, and last-validated timestamp.
- **FR-025**: When BYO credentials fail at send-time (expired/revoked), system MUST NOT silently fall back to platform credentials; it MUST fail the send, mark the store's status as credential-error, and surface the failure to the merchant.

**Template submission to Meta**

- **FR-026**: System MUST provide an endpoint that submits a new template to Meta and records a local template row with status PENDING and the returned Meta template id. **Custom template submission is a BYO-only feature in Phase 1**: stores in `platform_managed` mode MUST receive HTTP 403 with code `template_submission_requires_byo` when calling this endpoint. The reason is that platform-managed stores share NUMU's single Meta WABA; allowing per-merchant custom template submission against the shared WABA would cause cross-store template visibility, exhaust the WABA's approval-rate budget, and let one merchant's rejected template degrade the quality rating for every other platform-managed store. Platform-managed stores rely on the seeded canonical system templates (FR-030). When per-store WABA provisioning becomes viable in a future phase, this restriction is the trigger to lift.
- **FR-027**: Synchronous Meta validation errors MUST be surfaced to the caller and MUST NOT result in a local row.
- **FR-028**: System MUST update local template status from Meta via two mechanisms: (a) a webhook subscription to Meta's `message_template_status_update` field that updates the local template row when Meta pushes a status change (APPROVED, REJECTED, FLAGGED, PAUSED, DISABLED), and (b) a polling sync action that pulls current status for all PENDING local templates as a backfill mechanism. The webhook is the primary signal for platform-managed stores; the polling sync is the primary signal for BYO stores (whose Meta app webhook NUMU does not own) and a fallback for missed webhook deliveries.
- **FR-028a**: The polling sync action MUST run automatically at least every 15 minutes against any template that has been in PENDING for longer than 5 minutes, in addition to being manually invokable. The maximum acceptable lag between Meta status change and local row update MUST be 15 minutes (worst case, polling fallback) or under 1 minute (best case, webhook).
- **FR-029**: Sends MUST refuse to use a template whose local status is not APPROVED.
- **FR-030**: System MUST seed canonical system templates (order_confirmation, payment_received, order_shipped, order_delivered, abandoned_cart) marked as system-owned, so platform-managed stores have these available out of the box.

**Retry & Dead-Letter**

- **FR-031**: Transient send failures (rate limit, 5xx, network error) MUST be retried with exponential backoff, at least 3 attempts spanning at least 5 minutes total.
- **FR-032**: Non-retriable failures (4xx other than 429, opt-out, invalid template) MUST NOT be retried.
- **FR-033**: When all retries are exhausted (or on a non-retriable failure), system MUST create a dead-letter entry containing recipient phone, intended template/content, parameters, full error history, and originating context id (order_id, campaign_id, scheduled_send_id, or "ad_hoc").
- **FR-034**: System MUST provide an operator endpoint to list dead-letters (filterable by store, date range, error class) and to trigger replay of a specific dead-letter.
- **FR-035**: Replay MUST guard against double-send by checking the original send's final status (e.g., via the existing message log) before issuing a new send.
- **FR-035a**: Dead-letter entries MUST be retained for 90 days from creation, then automatically purged by a scheduled task that runs at least daily. Replays that succeeded are also purged 90 days from their final status update. The existing `message_log` is the long-term audit surface and is NOT affected by this purge.

**Central send-time guard**

- **FR-036**: All `send_*` methods (text, template, media, interactive, campaign per-recipient, scheduled, event-triggered) MUST pass through a single pre-send guard before issuing the Meta API call.
- **FR-037**: The pre-send guard MUST verify, in this order: (a) recipient phone is non-empty and E.164-valid, (b) the store has WhatsApp configured (mode + credentials valid), (c) the merchant notification setting for the message type is enabled, (d) the (store, phone) does not have an explicit opt-out row (always enforced except for the STOP-acknowledgement reply itself), (e) **if the template category is marketing**, an active opt-in row exists for the (store, phone) (utility/authentication templates skip this check per FR-011), (f) for non-template messages, the 24h customer-service window is open.
- **FR-038**: Guard failures MUST log a skip with a structured reason code (one of: no_phone, invalid_phone, no_credentials, credentials_invalid, merchant_setting_off, opt_out, no_opt_in, window_closed) and MUST NOT call the Meta API.
- **FR-039**: For event-triggered sends, guard skip reasons MUST be observable per (store, event-type, reason) for monitoring.

**Backwards compatibility & operational**

- **FR-040**: Existing `/webhooks/whatsapp/callback` route MUST continue to function unchanged.
- **FR-041**: Existing send_order_confirmation / send_shipping_notification / send_delivery_notification / send_payment_received / send_text_message / send_media_message methods MUST retain their current signatures (the central guard wraps them; it does not require call-site changes outside the new event handlers).
- **FR-042**: Existing per-store credential resolver MUST be the single source of truth for which credentials a send uses; no new code path may bypass it.

### Key Entities *(include if feature involves data)*

- **WhatsApp Opt-In**: a record that a phone consented (or did not) to WhatsApp messaging from a given store. Holds source, opted-in time, opted-out time, opt-out reason, and a soft link to the customer record. One phone may have many opt-in rows for a store over time (re-opts create new rows).
- **Scheduled WhatsApp Send**: a future-dated intent to send a templated message to a phone for a given store. Holds template reference, parameters, scheduled_for, status (pending/sent/cancelled/skipped/failed), and an optional related-order link for auto-cancellation.
- **WhatsApp Dead-Letter**: an exhausted-retry record of a failed send. Holds recipient, intended content/template + parameters, originating context id, error history, replay state, and a created-at timestamp used to drive the 90-day automated purge.
- **Store WhatsApp Mode** (a property of the store's WhatsApp configuration, not a new table): one of {platform_managed, byo}. Derived from the presence/absence of an active BYO credential row; surfaced via the read-only status endpoint.
- **WhatsApp Notification Settings** (already exists in some form): per-store, per-message-type toggles consulted by the pre-send guard. Phase 1 may need to add toggles for order_created and payment_received specifically if absent today.

---

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: When a customer with active opt-in places an order on a store with order-confirmation notifications enabled, they receive the WhatsApp confirmation within 30 seconds in 99% of cases.
- **SC-002**: When a customer with active opt-in pays for an order, they receive the WhatsApp payment-received message within 30 seconds in 99% of cases.
- **SC-003**: Zero WhatsApp messages are sent to customers who have an active opt-out for the sending store (measured continuously; any breach is a Sev-2).
- **SC-004**: An inbound STOP/UNSUBSCRIBE/إلغاء results in an opt-out recorded within 10 seconds and a confirmation reply sent within an additional 10 seconds, in 99% of cases.
- **SC-005**: Scheduled follow-ups fire within ±2 minutes of their target time in 95% of cases under normal load (defined as ≤5,000 pending sends in the queue).
- **SC-006**: When a related order is cancelled or refunded, any pending scheduled follow-up tied to it is cancelled before its scheduled time in 100% of cases (no scheduled send fires after its order was cancelled).
- **SC-007**: A merchant can connect their own Meta WABA via the API in under 2 minutes from credential entry to "connected" state, and the next message they send routes through their account.
- **SC-008**: A merchant can disconnect BYO and return to platform-managed in a single API call; the next send uses platform credentials.
- **SC-009**: A merchant can submit a new template through the API and see its status update (APPROVED, REJECTED, FLAGGED, PAUSED, DISABLED) reflected in NUMU within 15 minutes of Meta's decision (typically within 1 minute via webhook); rejected templates surface Meta's reason.
- **SC-010**: Transient Meta API failures (rate limit, 5xx) recover via retry in at least 90% of cases without producing a dead-letter.
- **SC-011**: No send failure is silently lost — every exhausted-retry failure produces a dead-letter entry that an operator can list and replay (measurable: count of dead-letters equals count of audit-log "send_failed_final" events).
- **SC-012**: Every send (event-triggered, scheduled, campaign, or ad-hoc) passes through the central guard — measurable by zero direct Meta API calls in the codebase that bypass the guard.

---

## Assumptions

- The existing Meta Cloud client, `whatsapp_templates` / `whatsapp_conversations` / `whatsapp_campaigns` / `whatsapp_campaign_recipients` tables, abandoned-cart Celery task, shipped/delivered handlers, and `service_credentials` AES-256 encryption all work today and are reused as-is. This spec only adds new tables / wires new events / centralises the guard — it does not refactor the working pieces.
- Customer phone canonicalization uses the existing `whatsapp_phone_formatter` (E.164 + Egypt fallback).
- The merchant-hub UI to drive these endpoints (BYO connect form, template builder, scheduled-sends viewer, dead-letter inspector) is **out of scope** for Phase 1. The endpoints here must be complete and testable via API/integration tests; the dashboard wires up in Phase 2.
- Storefront checkout UI for the consent checkbox is out of scope for this phase; the **backend** opt-in table, the write endpoint the checkout will call, and the inbound STOP handler are in scope. Storefront UI lands with Phase 2.
- The audience-builder filter DSL (Phase 3) is out of scope; scheduled follow-ups in this phase are addressed by single-recipient creation (the abandoned-cart task is the existing example of programmatic creation).
- The agent-assignment workflow on conversations (Phase 4) is out of scope.
- WhatsApp provider is **Meta WhatsApp Business Cloud API only**. No Twilio, 360dialog, or WATI in this phase. "BYO" means the merchant's own Meta WABA, not a different provider.
- Existing message-log table (`message_log`) captures every send and is the source of truth for "did this customer already get this message" idempotency checks.
- **Custom template submission is BYO-only in Phase 1** (FR-026). Platform-managed stores cannot create new templates via the API; they consume the system-seeded canonical templates. Lifting this restriction is gated on per-store WABA provisioning, which is not in Phase 1's scope.
- **The storefront-facing opt-in endpoint is no longer freely callable** — it requires a valid checkout-session token (FR-007a). This closes the abuse vector where anyone could write opt-in rows for arbitrary phones; storefront teams must pass the session token through when calling the endpoint.
- **Webhook signature verification has a single-key resolution path** (no fallback). Stale BYO credentials whose `waba_id` no longer matches anything are returned as 200 + no-op + structured warning, not as silent rejection.
- Merchant notification preferences for `order_created` and `payment_received` events may not exist today; if not, they are added (and follow the FR-019a default-toggle behaviour).
- The "30 seconds" SLA for order/payment confirmations assumes Meta API is healthy; the SC-001 / SC-002 99% targets exclude documented Meta incidents.
- "Inbound STOP detected as first word" assumes Latin and Arabic punctuation/whitespace are stripped before the first-word check.

---

## Out of Scope (deferred to later phases)

- Merchant-hub UI for: WhatsApp connection page, templates page, settings page (Phase 2); campaigns + audience builder (Phase 3); conversations inbox + agent assignment (Phase 4).
- Storefront checkout consent checkbox UI (Phase 2 — backend table and write endpoint are in this phase).
- Audience builder filter DSL (Phase 3).
- Conversation assignment workflow (Phase 4).
- Support for non-Meta providers (Twilio, 360dialog, WATI) — explicitly excluded; see [whatsapp-provider-scope](whatsapp-provider-scope.md) memory.
- Per-customer channel preference UI (the guard reads such a preference if present; setting it is a later phase).
- Storefront product/order review pages that the post-delivery follow-up template would link to (this phase only sends the message; the link target is owned elsewhere).
