# Quickstart — WhatsApp Foundation Phase 1

**Audience**: developer picking up this feature for implementation.
**Prereq reading**: [spec.md](./spec.md), [plan.md](./plan.md), [data-model.md](./data-model.md), [research.md](./research.md).

This walks you through the end-to-end flow once the feature is implemented, so you know what to build toward.

---

## 0. Setup

```powershell
# From repo root:
cd C:\Users\Yahia\NUMU\NUMU-api

# Ensure the feature branch is checked out
git checkout backend-030-whatsapp-foundation

# Install / update deps (none new; everything uses existing packages)
poetry install

# Run the new migration
alembic upgrade head

# Verify the new tables exist
psql $DATABASE_URL -c "\d whatsapp_opt_ins"
psql $DATABASE_URL -c "\d whatsapp_scheduled_sends"
psql $DATABASE_URL -c "\d whatsapp_dead_letters"
```

---

## 1. End-to-end: order confirmation flow

A platform-managed store has WhatsApp configured (the existing pattern); we'll place an order and verify the message lands.

```powershell
# 1. Create a test customer + opt-in
curl -X POST https://numueg.app/api/v1/storefront/test-store/whatsapp/opt-in `
  -H "Content-Type: application/json" `
  -d '{"phone": "01001234567", "locale": "ar"}'
# → 201 with opt-in row; phone canonicalized to +201001234567

# 2. Place an order (existing storefront flow). Backend emits OrderCreatedEvent.
# Within 30s:
#   - whatsapp_notification_handler.handle_order_created fires
#   - WhatsAppSendGuard checks: phone valid ✓, store has creds ✓,
#     notifications.order_confirmation = true ✓ (platform-managed default),
#     opt-in active ✓ (we just wrote it), 24h window not applicable (template send)
#   - get_whatsapp_service resolver returns platform-Meta service
#   - send_order_confirmation called with template params
#   - message_log row created with metadata['order_id'] = <order_id>

# 3. Inspect
psql $DATABASE_URL -c "
  SELECT template_name, status, metadata->>'order_id' AS order_id
  FROM message_log
  WHERE customer_phone = '+201001234567'
  ORDER BY created_at DESC LIMIT 3;
"
# Expect: order_confirmation row, status='sent'

# 4. Replay the same event (idempotency test)
# → handler queries message_log first, finds existing send for this order_id,
#   skips with reason 'already_sent'. No duplicate message.
```

---

## 2. End-to-end: STOP keyword opt-out

```powershell
# 1. Simulate inbound STOP via the webhook (use the existing test helper)
python -m tests.helpers.simulate_meta_inbound `
  --from "+201001234567" `
  --to-phone-number-id "<platform_phone_id>" `
  --text "STOP"

# 2. The webhook handler:
#   - Logs the inbound message (existing behaviour)
#   - Runs stop_keyword_detector → detects "stop" as first word
#   - Flips opt-in to opted_out_at=NOW, opt_out_reason='inbound_stop_keyword'
#   - Sends optout_confirmation_ar template (or _en based on convo language)

# 3. Inspect
psql $DATABASE_URL -c "
  SELECT phone, opted_in_at, opted_out_at, opt_out_reason
  FROM whatsapp_opt_ins
  WHERE phone = '+201001234567'
  ORDER BY opted_in_at DESC LIMIT 5;
"
# Expect: most recent row has opted_out_at set

# 4. Place another order for the same customer → no WhatsApp send
#   - Guard returns skip_reason='opt_out'
#   - message_log gains no row; logger emits structured skip event
```

---

## 3. Scheduled follow-up (3-day post-delivery review request)

```powershell
# When the order moves to "delivered", the existing OrderStatusChangedEvent
# fires. We add a new use-case that schedules a follow-up:

# Programmatic schedule:
curl -X POST https://numueg.app/api/v1/stores/<store_id>/whatsapp/scheduled-sends `
  -H "Authorization: Bearer <merchant_token>" `
  -H "Content-Type: application/json" `
  -d '{
    "phone": "+201001234567",
    "template_id": "<review_request_template_id>",
    "template_params": {"order_number": "1234", "review_url": "https://..."},
    "scheduled_for": "2026-05-27T12:00:00Z",
    "related_order_id": "<order_id>"
  }'
# → 201 with row status='pending'

# Wait until 2026-05-27T12:00:00Z (or for testing, set scheduled_for to NOW + 90s):
# Every minute the dispatcher Celery beat task runs:
#   whatsapp_scheduled_send_dispatcher.dispatch_due_sends
# It SELECTs WHERE scheduled_for <= NOW AND status='pending' FOR UPDATE SKIP LOCKED,
# runs the guard (still active opt-in? merchant setting still on?),
# and dispatches via send_template (or skips with reason if guards fail).

# Cancel before fire:
curl -X DELETE https://numueg.app/api/v1/stores/<store_id>/whatsapp/scheduled-sends/<send_id> `
  -H "Authorization: Bearer <merchant_token>"
# → status='cancelled'

# Auto-cancel: cancel the related order →
# whatsapp_scheduled_cancel_handler subscribed to OrderStatusChangedEvent('cancelled')
# bulk-updates all pending scheduled-sends with related_order_id=<order_id> to status='cancelled'
```

---

## 4. BYO connection (merchant brings their own Meta WABA)

```powershell
# Merchant has their own Meta app + WABA. They get credentials and submit:
curl -X POST https://numueg.app/api/v1/stores/<store_id>/whatsapp/byo/connect `
  -H "Authorization: Bearer <merchant_token>" `
  -H "Content-Type: application/json" `
  -d '{
    "access_token": "EAAxxxxxxxxxx",
    "phone_number_id": "123456789",
    "waba_id": "987654321",
    "app_secret": "abcdef..."
  }'

# Backend runs the 3-step validation (research R2):
#   1. GET /{phone_number_id} → expects 200 + verified_name
#   2. GET /{waba_id} → expects 200 + id match
#   3. GET /{waba_id}/message_templates?limit=1 → expects 200

# On success: 201 with mode='byo', notifications.* all DISABLED (FR-019a).
# Merchant must then explicitly enable each toggle:
curl -X PATCH https://numueg.app/api/v1/stores/<store_id>/whatsapp/notifications `
  -H "Authorization: Bearer <merchant_token>" `
  -d '{"order_confirmation": true, "payment_received": true}'

# Disconnect at any time:
curl -X DELETE https://numueg.app/api/v1/stores/<store_id>/whatsapp/byo/disconnect `
  -H "Authorization: Bearer <merchant_token>"
# → mode='platform_managed'; toggles restored from prior platform-managed snapshot.
```

---

## 5. Template submission

```powershell
# Merchant wants a custom shipping notification template
curl -X POST https://numueg.app/api/v1/stores/<store_id>/whatsapp/templates `
  -H "Authorization: Bearer <merchant_token>" `
  -H "Content-Type: application/json" `
  -d '{
    "name": "shipping_eta",
    "language": "ar",
    "category": "UTILITY",
    "components": [
      {"type": "HEADER", "format": "TEXT", "text": "تحديث الشحن"},
      {"type": "BODY", "text": "طلبك رقم {{1}} في الطريق. التوصيل المتوقع: {{2}}"}
    ]
  }'

# Backend:
#   - Resolves credentials via get_whatsapp_service
#   - POSTs to Meta's /{waba_id}/message_templates
#   - On 200 from Meta: persists local row status=PENDING with meta_template_id
#   - Returns 201

# Status updates arrive via webhook (platform-managed) or sync (BYO):
#   Webhook field message_template_status_update → handler updates row.
#   OR
curl -X POST https://numueg.app/api/v1/stores/<store_id>/whatsapp/templates/sync `
  -H "Authorization: Bearer <merchant_token>"
# → reads all PENDING locally, queries Meta for each, updates statuses.
```

---

## 6. Dead-letter replay

```powershell
# Simulate a Meta API outage to force a dead-letter (test env only):
# The send retries with exponential backoff (5 attempts spanning ~25 min), then writes a dead-letter row.

curl https://numueg.app/api/v1/stores/<store_id>/whatsapp/dead-letters?replay_state=not_replayed `
  -H "Authorization: Bearer <merchant_token>"
# Returns list of failed sends with full error_history.

# Inspect one:
curl https://numueg.app/api/v1/stores/<store_id>/whatsapp/dead-letters/<dl_id> `
  -H "Authorization: Bearer <merchant_token>"

# Replay (after the underlying issue is resolved):
curl -X POST https://numueg.app/api/v1/stores/<store_id>/whatsapp/dead-letters/<dl_id>/replay `
  -H "Authorization: Bearer <merchant_token>"
# → 202; worker checks message_log for double-send (none → replays),
#   updates replay_state='replayed_success' on success.

# Daily at 03:00 UTC: whatsapp_dead_letter_purge task deletes rows older than 90 days.
```

---

## 7. Running the tests

```powershell
# Unit tests (pure logic):
pytest tests/unit/whatsapp/ -v

# Integration tests (DB + Celery + Meta mock via respx):
pytest tests/integration/whatsapp/ -v

# All:
pytest tests/ -v -k whatsapp
```

Every FR has a corresponding test; SCs map to integration tests that assert the measurable outcome.

---

## 8. Observability

After implementation, the following logs / metrics will exist (existing structlog patterns):

- `whatsapp.send.dispatched` — counter per (store_id, template_name, success)
- `whatsapp.send.skipped` — counter per (store_id, event_type, reason) — per FR-039
- `whatsapp.send.failed.retriable` — counter
- `whatsapp.send.failed.terminal` → dead-letter created → counter
- `whatsapp.scheduled.lag_seconds` — histogram (fire-time – scheduled_for)
- `whatsapp.optin.created` / `whatsapp.optin.revoked` — counters
- `whatsapp.byo.validation_failed` — counter per failed step

These power SC verification in production.

---

That's the end-to-end. The next phase (`/speckit-tasks`) breaks this into actionable, dependency-ordered tasks.
